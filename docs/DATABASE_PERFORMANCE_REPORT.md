# DATABASE PERFORMANCE REPORT — PHASE 0

本轮只测量、解释执行计划并给出建议。没有创建索引、修改 Schema、切换 journal mode、迁移数据库或调整连接池。

## 查询模式与频率

频率分清单请求实测、源码推算、线上未测。N=账号数、M=启用绑定数、E=账号收到的非空群消息数/分钟、G=同步群数量。

|Query / 位置|Table|Frequency|Possible Bottleneck|Recommended Optimization|
|---|---|---|---|---|
|state: profile/binding/weight 逐账号读取，console_api.py:35|account_profiles / sender_bindings / sender_weights|本地 2/20/100 账号，整个 state 含鉴权分别 17/89/409 SQL；具体结果受 weight 是否存在影响|N+1；权重存在时重复 db.get 两次，临时对象可能已不在弱引用 identity map|先把 weight 存局部变量；再一次读取每类配置并按 ID 建映射，维持响应字段与缺省值|
|state 中 application_settings 两次，console_api.py:288|console_state|2 次/请求；另有鉴权密码哈希读取 1 次|相同 key 重复查；后者用于密码修改后会话失效，不宜随意跨请求缓存|应用设置请求内复用；鉴权缓存需明确失效机制后另评估|
|state 读取全部账号/绑定/同步群，console_api.py:280|accounts / relay_tasks / chats|每次 state，默认约 4 次/分钟/可见无弹窗标签页|大 JSON、ORM 全对象和序列化；100/500/5000 场景 522708 字节|先查询批量化，不改 API contract；按页面拆接口/摘要接口需单独确认|
|每日命中 count(stage, created_at)|relay_jobs|1 次/state，约 4 次/分钟/标签页|EXPLAIN：SCAN relay_jobs|候选 stage+created_at 索引，需更大数据验证后确认|
|每日 DM 成功 count(stage,status,sent_at)|relay_jobs|1 次/state|仅 status 索引，仍筛 stage/time|候选 stage+status+sent_at；也比较单条条件聚合，不能凭感觉两者都上|
|pending/failed 两次统计|relay_jobs|各 1 次/state|使用 status 索引；重复遍历状态集合|比较条件聚合与现有索引访问，小库未必更快|
|消息记录 count + ORDER BY id DESC LIMIT 50 OFFSET|relay_jobs|首页/筛选/分页，每次 2 条业务查询+1 条鉴权|深 offset 随历史量增长；WHERE username LIKE '%…%' 全表扫描|保留分页 contract；先测真实深页，游标或全文搜索需另批准|
|on_message 全部 enabled 任务+profile+binding，relay_engine.py:160|relay_tasks / account_profiles / sender_bindings|未绑定群一条消息也实测 3 SQL；约 3E SELECT/分钟起|每个账号都遍历全部任务，O(M)；JSON/关键词重复解析|先按账号和群提前筛选；缓存必须有绑定修改/暂停/权限更新失效策略与发送前校验|
|接收侧原消息回查(task,stage,text,status) ORDER BY id DESC|relay_jobs|每条匹配 B 消息每条候选绑定 1 次|已有唯一索引只命中 task+stage 前缀，之后文字比对与临时排序|候选 task+stage+id 减少排序；是否引入稳定关联键属于业务/Schema 另案|
|select_sender 遍历账号 join profile，再 binding/weight|accounts / account_profiles / sender_bindings / sender_weights / console_state|每次 DM 入队；2账号总入队实测12 SQL（含写）|分配 N+1；同事件重复进来也先分配，唯一键冲突才回滚|先批量查候选，保持顺序及权重事务；幂等提前判断必须处理竞争，不能替代唯一约束|
|queue 选 enabled + status + due_at ORDER BY id LIMIT 10|relay_jobs / relay_tasks|空闲约每2秒一次，即最多约30次/分钟；忙时周期还含发送耗时|status 索引+临时 B-tree 排序；前10条离线阻塞后续|先解决行为一致的调度验证；候选索引应比较 status/due/id 不同次序，不改变发送顺序|
|reload_workers 查询活跃账号|accounts|每10 tick，一般约20秒（不是严格20秒）|初始化串行网络等待占共享锁|读取本身低成本，主要优化生命周期/锁范围，属于高风险|
|sync_account_chats 查询每个 dialog，再 _assign_primary_listeners 每个群一次|chats / accounts|用户同步时约 O(G) 查询，非固定周期|大量重复往返；全量更新主监听兼容字段|按账号批量加载已有群、按群批量赋值；先确认旧路由兼容|
|旧 /chats 模板读 c.account.name|chats / accounts|访问旧 HTML 路由时|潜在按账号 lazy-load N+1|确认仍用后按需要 eager load；不是现代前台的主流量|
|启动 migrate_profiles，get_profile 缺失时触发|accounts / profiles / bindings / tasks|启动；缺失 profile 时 GET 也触发|逐账号查询，读取函数潜在 flush；GET 关闭 Session 未提交可能反复构造|将迁移与读取明确分离前需旧数据 characterization tests|

## 实测摘要与执行计划

详见 [api.json](performance-baseline/api.json)。其中语句形状带 SELECT 所有 ORM 列，并不是手写 `SELECT *`；不需要无依据全面换 SQL。

- 100 账号 / 500 绑定 / 5000 群 / 50000 记录：state 平均 125.916ms，409 SQL；最后一次 SQL 执行累计 15.258ms，剩余耗时不能全部叫“数据库慢”。
- 同数据下 records 首页平均 2.620ms、offset 5000 为 2.666ms、用户名包含搜索为 9.854ms；是本机串行预热样本，不是生产 SLA。
- 命中计数和用户名搜索：SCAN relay_jobs。
- queue：status index → task PK → USE TEMP B-TREE FOR ORDER BY。
- 原消息回查：唯一索引 task/stage 前缀 → USE TEMP B-TREE FOR ORDER BY。
- 当前 relay_jobs 只有主键、事件唯一约束和 status 单列索引；旧 send_queue 的 status/due 索引不能替新表服务。

## 索引建议与变更门槛

优先候选是 `(stage, created_at)`：直接针对每15秒每日命中全表统计。第二候选 `(stage,status,sent_at)` 用于每日成功统计。原消息回查考虑 `(task_id,stage,id)`，队列需比较 `(status,due_at,id)` 与能保持排序的方案。全部仅候选，不能一次全加。

写入成本：每次 relay_jobs 插入都维护新增索引；涉及 status、sent_at 的索引还增加每次状态流转写放大、页占用与锁时长。需要用代表性历史量与写入量比较查询 P95、插入/状态更新耗时、数据库体积。

迁移方案：获得确认后，每个索引独立、命名固定、可重复应用；先在临时库及一致性备份上验证；部署需备份、确认磁盘和 SQLite 写锁窗口；回退仅 DROP 对应新索引，不删除业务记录。当前禁止执行。

## SQLite 连接与事务

- 单个模块级 engine + SessionLocal；Session 用 finally close，作用域通常清楚。不是每请求创建 engine。
- `pool_size=0,max_overflow=0` 对 SQLAlchemy QueuePool 意味着**不限池大小**；已读取当前安装库 QueuePool.__init__，不是“零连接池”。生产短采样 FD 未增长，但突发并发后连接保留风险需压测。
- timeout=30 秒；同步 ORM 在 async handler/runtime 中执行，锁等待可能阻塞事件循环。不能机械给共享 Session 套线程，也不能随意缩小事务破坏去重/分配。
- 本地新连接 PRAGMA：journal_mode=delete、busy_timeout=30000、foreign_keys=0。线上只读确认 journal_mode=delete；未把只读检查连接的 timeout 当成服务配置。
- Python SQLite 驱动的预编译语句缓存未自定义；ORM使用绑定参数。没有证据需要引入新的 ORM、数据库或 Redis。
- read-only session_scope 也会 commit，通常无写不会产生数据写事务；实际 SQL 计数未计 BEGIN/COMMIT/pre-ping 驱动操作，已明确统计口径。
- 本轮没有对生产制造写锁、开启 SQL 全量日志或更改 WAL。WAL/FK/池上限/超时均需独立兼容性和并发验证。
