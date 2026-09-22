# PERFORMANCE BASELINE REPORT — PHASE 0

日期：2026-09-22。仅审计与测量，没有执行性能优化、业务代码修改、依赖升级、线上重启或配置变更。After baseline 尚不存在，不能声称获得性能提升。

## 版本、范围和方法

- 仓库 HEAD：`50852075d04b31f866d15fe366841c591a5dc0f0`；测量对象为包含前几轮未提交修改的工作目录，不是单独该 commit。文件校验值见 [inventory.json](performance-baseline/inventory.json)。46 个源码/模板/测试/迁移文件，合计 6749 行；另读依赖、容器与 Alembic 配置、AGENTS 和需求。
- 技术栈：Python/FastAPI/Uvicorn、现有 SQLAlchemy ORM、SQLite、Telethon、原生 JavaScript/CSS/Jinja2。没有 package.json、Bun scripts、TypeScript、tsconfig 或 lint 配置。Bun 内存、bun test、TS typecheck 不适用，不引入新技术栈。
- 本地与线上版本不同：目标可空、私有群提示与部分前端文案修改尚未上线；空格关键词后端已上线。本地证据不能冒充线上结果。线上源文件哈希只读核验；关键调度、查询、轮询实现仍存在上述问题。
- 本地 API：macOS，Python 3.12.13，SQLite 3.53.4；临时数据库、虚构数据、不启动 Telegram、不使用生产会话。FastAPI TestClient，每端点预热 1 次、串行测量 10 次。SQLAlchemy 事件记录语句数量与执行耗时，不记录参数。API 时间包含中间件和序列化，不包含真实网络。
- 浏览器：Headless Chrome 153.0.8010.53；真实本地 Uvicorn HTTP，桌面 1440×1000、手机模拟 390×844，无 CPU/网络降速。每页一次导航；首屏概览冷缓存，其他页复用缓存。不是实体手机或公网测速。保存只改变临时数据库。
- 线上：仅 `/proc`、健康接口、数据库只读连接、日志聚合和源文件哈希；没有读取或导出会话秘密，没有发送测试消息。日志仅统计错误出现次数。

## API 扩展性基线

数据量同时增长，不能把耗时增长全部归因于账号数量。查询次数中的按账号增长已由 SQL 轨迹确认。

|账号 / 绑定 / 群 / 记录|state 平均 ms|state P95 ms|SQL/请求（含鉴权）|JSON 字节|记录首页平均 ms|用户名搜索平均 ms|
|---|---:|---:|---:|---:|---:|---:|
|2 / 1 / 40 / 100|3.645|3.827|17|3919|2.697|1.561|
|20 / 100 / 1000 / 10000|26.404|26.341|89|103214|2.791|3.135|
|100 / 500 / 5000 / 50000|125.916|131.266|409|522708|2.62|9.854|

原始结果与完整查询形状见 [api.json](performance-baseline/api.json)。P95 为 10 次样本排序的索引 8，样本有限，不是服务等级承诺。SQL 时间是最后一次请求的语句执行耗时，不含 ORM 对象构造/全部结果取回；测试内最慢单条约 6.72 ms。未测并发吞吐或生产慢查询分布。

## Web 页面测量

DCL 与 load 只表示文档阶段；页面内容由异步 API 填充，不应将 load 当成内容全部可用时间。

|环境|页面|DCL ms|load ms|初始化 API 数|API 耗时 ms（按序）|长任务 >50ms|页面横向溢出|
|---|---|---:|---:|---:|---|---:|---|
|桌面|overview|42.6|42.7|1|16.3|0|无|
|桌面|accounts|6.8|7.1|1|8.3|0|无|
|桌面|tasks|9.8|10.2|1|12.7|0|无|
|桌面|records|8.5|8.7|2|9.3, 6.3|0|无|
|桌面|guards|12.6|13.0|2|6.8, 3|0|无|
|桌面|settings|7.4|7.4|1|7.6|0|无|
|390px 模拟|overview|29.4|29.5|1|11.5|0|无|
|390px 模拟|accounts|8.2|8.5|1|11.3|0|无|
|390px 模拟|tasks|6.6|6.9|1|8.1|0|无|
|390px 模拟|records|6.6|6.8|2|7.1, 4.1|0|无|
|390px 模拟|guards|9.2|9.2|2|8.5, 2.6|0|无|
|390px 模拟|settings|6.2|6.5|1|8|0|无|

- 小样本首次导航没有重复的同路径 API；records/guards 先读 state，再读自身接口，存在可研究的数据读取冗余，而非同一请求发两遍。records 筛选/分页各 1 次 records 请求；“刷新记录”通过 load 发 state+records 共 2 次。
- 全部页面都有一个 15 秒轮询：可见且无弹窗时约 4 次 state/分钟/标签页；隐藏页面和打开弹窗时跳过。没有每个模块独立每秒轮询的问题。
- 概览实测 31 秒：state 共 3 次（首屏+2 次轮询），content 重建 3 次（含首屏），即静态数据仍每分钟约重建 4 次。其他页面轮询只更新顶部状态，不更新其列表；不能为了优化擅自改为更慢或把它误称实时列表。
- 定时器回调没有 in-flight 防护或请求超时。故障注入同时执行两个轮询回调，实际最大 2 个在途请求；正常网络样本没看到重叠。慢响应和旧响应覆盖风险需单独处理。
- 弹窗输入：打开 16 秒，没有新增 state 请求，关键词草稿原样保留。源码把弹窗放在 content 外；文案、群 ID、权重使用同一弹窗机制。仍需补“请求发出后才打开弹窗”和保存成功但刷新失败的回归。
- 61 个功能测试全部通过，正常浏览器路径 runtime error 为 0。故障注入：首次 records 失败有错误界面；点击筛选后接口 500 则产生未处理 Promise rejection，列表保持旧结果且无一致错误反馈。见 [faults.json](performance-baseline/faults.json)。
- 桌面 CLS：记录页 0.0438、设置页 0.0132、概览 0.0029；手机本样本 0。没有足够样本下结论“零布局跳动”。
- 操作耗时包含自动化点击等待和局部网络，不等价于 INP：桌面打开绑定 75ms、保存绑定 57ms、切换账号页签 24ms、编辑文案 34ms、保存文案 51ms、分页 51ms、搜索 38ms；手机分别 38/52/37/36/50/51/39ms。权重编辑保存也通过，无冻结观察。

## Server、启动与数据库运行状态

线上当前快照：3 个账号（2 监测、1 私信），2 个连接 worker，1 条绑定，49 条群信息，2 条历史发送记录，pending/waiting 为 0。

- 10 秒样本：RSS 108740 KiB（约 106.2 MiB）→108740 KiB；单核 CPU 约 0.40%；FD 20→20，线程 7→7。没有高负载或长期证明。
- 线上本机 healthz 10 次：平均 7.60ms、最大 59.22ms。不是鉴权 state API 的线上测量。
- SQLite 文件 60×4096=245760 字节，journal_mode=delete。30 分钟日志内 database locked/busy、traceback、error 文本各 0；没有慢 SQL instrumentation，不能据此断言没有慢查询或锁等待。
- 不为测启动而重启生产。systemd NRestarts=0 是服务管理器记录，不表示从未人工重启。

本地空库进程启动至 healthz 可用（3 次，无 Telegram）：[583.11, 585.33, 527.48] ms；探测粒度约 20ms。API 数据表中的热启动 lifecycle 时间不含 Python 模块导入，不能与冷启动混比。

## Queue / Async 实验

临时库与 mock Telegram 客户端，见 [runtime.json](performance-baseline/runtime.json)。

- 未绑定群的非空消息仍执行 3 次 SELECT（全部 enabled 任务、账号 profile、sender binding）。
- 2 账号场景一次 DM 入队记录 12 次 SQL（含写入）；重复事件仍先执行分配再依赖唯一约束回滚，逻辑正确但有重复工作。
- 模拟 A 发送等待 250ms：A 于 2.58ms 开始，独立 B 于 256.40ms 开始；20ms 后申请配置锁的操作等待 233.39ms。证明跨账号等待串行及锁耦合，不代表真实 Telegram 网络耗时。
- 最早 10 条 pending 属于离线账号，第 11 条属于可用账号：连续调用调度 3 次，第 11 条仍未发送。调度上限固定 10，可能出现队首阻塞。
- 两个 mock 账号、绑定修改+reload 10 次：创建 2 个客户端、注册 2 个业务 handler，没有变成 20 个。stop 后 0 个连接、0 worker、0 identity。不能替代真实断线压力与 session 验证。

## Timer / Listener / Memory

- 前端应用注册 1 个 setInterval，每个文档实例一份；toast 最多一个待执行 timeout，创建前清理旧值。页面退出由浏览器销毁。常驻 dialog 的显式 addEventListener 为 1，列表主要采用 onclick 赋值。
- 浏览器 CDP 统计包含测量脚本自身的监听器：概览 GC 后 31 秒前后 254 节点、19 个监听器不变，JS heap 1655521→1656945 字节（+1424）。
- 弹窗 1/40/80 次循环，GC 后均为 351 节点、26 个监听器。首次从空 modal 变为已关闭但保留内容的 modal 会增加节点，后续没有随循环增长。未证实前端泄漏。
- 活跃后端为 RelayEngine：一个 loop_task，循环 sleep(2)，每 10 tick reload；业务事件 handler 理论一条/连接账号，线上 2 个 worker 推断 2 个 handler，未注入生产堆读取实测。Telethon 内部定时器/任务未计数。
- 老 WorkerManager 有 queue/health 两个循环，TelegramWorker 每账号任务、Raw handler 内 create_task；当前 app 没有启动该 manager，不能把其计数加到实际主引擎。
- 潜在保留：account_locks 字典没有删除路径；SQLAlchemy pool_size=0 实为无界连接池（本机已安装库源码核实），不是禁用池；session entity 缓存和库内部任务需长期实测。当前没有无界 processedUsers 内存集合；全局联系去重保存在数据库，不能加 TTL 改业务。

## 未完成的指标与后续条件

生产鉴权 API 延迟、生产 SQL/秒与慢查询、Telethon 内部 timer/listener、真实重连内存曲线、24–72 小时驻留、实体手机触摸性能、公网网络、并发饱和吞吐均未测。不能用短窗口或推算填充实测值。优化阶段再按同一数据集、机器和脚本补 After；本轮没有 After。

下一步建议和风险分级见 [CODE_QUALITY_AUDIT.md](CODE_QUALITY_AUDIT.md)，SQL 详情见 [DATABASE_PERFORMANCE_REPORT.md](DATABASE_PERFORMANCE_REPORT.md)。等待确认后再修改。
