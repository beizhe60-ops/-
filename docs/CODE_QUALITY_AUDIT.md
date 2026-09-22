# AI CODE QUALITY + PERFORMANCE AUDIT — PHASE 0

2026-09-22。本轮仅检查与基线测量。没有优化业务代码，所有下列事项待确认；保留监测、转发、DM、权重、关键词、绑定、格式与去重语义。

## 最高优先级的 10 个 Code Smell

|序号|发现与位置|证据 / 影响|处理边界|
|---|---|---|---|
|1|共享锁横跨网络 I/O：relay_engine.py:67、399；console_api.py:411|reload、队列发送、同步与配置操作共用 manager.lock；模拟慢发送让配置等待233ms|高风险，先固定顺序/取消/快照语义|
|2|跨账号全局串行队列+固定前10：relay_engine.py:399|离线前10条使第11条连续3轮未发送|已复现，调度修改先补公平性与限流测试|
|3|巨型混合职责：console.js、console_api.py、relay_engine.py、workers.py|渲染/表单/请求；认证操作/配置/查询；连接/分配/队列都集中|按边界小步整理，禁止整模块重写|
|4|N+1 和重复读取：console_api.py:35、279|每账号查profile/binding/weight，应用配置两次；17→89→409 SQL|请求内局部复用可先做；批量查需输出一致回归|
|5|异步上下文同步 ORM：relay_engine.py 与 console_api async routes|潜在事件循环阻塞；SQLite忙等30s|不能直接搬到线程或拆事务，先测锁等待|
|6|两套可达业务入口：main.py 旧HTML与新 console_api|旧登录/同步路由缺少新接口的部分锁/状态检查；两套队列模型|不是确认无用代码，不删路由、不合并业务|
|7|读取函数隐式迁移：account_settings.get_profile|缺profile会 migrate_profiles+flush；与启动迁移、Alembic、schema_updates 并存|需旧数据和GET幂等测试后整理|
|8|重复业务状态与多来源：profile.monitor_chat_ids + task.source_chats；template + binding.template；account_b占位|兼容规则明确存在，直接“去重”会改行为|高风险，不能作为纯清理删除|
|9|异常边界不一致：console.js:694 空catch；搜索/分页未兜底；enqueue全吞IntegrityError|故障注入筛选500产生未处理Promise；数据库真实约束错误也可能伪装重复事件|先增加可观察性，不改失败重试/发送规则|
|10|生命周期缺口：account_locks无清理；sync connect在try外；旧Raw create_task未跟踪|长期保留或异常清理风险；旧代码不是当前活动发送引擎|分类修补，真实重连与关闭测试后执行|

不能把所有重复都统一：relay_logic.keyword_words 与旧 rules.split_keywords 支持不同规则；words 用于忽略用户，不能随意改成空格分词。render_copy 的有限替换与旧 Jinja 模板也不是等价实现。

## 10 个性能瓶颈 / 扩展风险（按优先级）

|序号|问题|证据强度|
|---|---|---|
|1|全局锁导致账号网络等待阻塞其他发送与配置|模拟实测：B开始256ms，配置等待233ms|
|2|队首10条不可用时后续账号任务饥饿|模拟实测：3轮第11条未发送|
|3|state账号配置 N+1|SQL实测：2/20/100账号对应17/89/409条|
|4|state全量加载/序列化且所有页面轮询|100账号扩展集约523KB、126ms；真实UI每15秒读一次|
|5|每条非空群消息读所有启用任务并遍历|未绑定群实测仍3 SQL；O(绑定数)为源码推导|
|6|每日统计/用户名包含查询扫描历史记录|EXPLAIN确认；5万记录搜索约9.85ms，尚非线上故障|
|7|概览状态未变也整体重建DOM|31秒2次多余重建；小样本无>50ms长任务|
|8|定时器可重叠、records/guards串行依赖state|故障注入2个在途；记录页正常初始化2个API|
|9|账号初始化串行 get_dialogs、同步逐群查库|源码确认，线上启动网络耗时未测，不给虚构提升比例|
|10|无界连接池+同步DB忙等+历史表增长无保留策略|配置及库源码确认；线上短采样无FD/内存增长，属于长期风险|

不是所有项目都值得立即优化：当前线上低负载约106MiB、0.4%单核CPU，没有证据需要换库、分布式、React/Vue、Redis或大规模架构改造。

## Frontend 请求与输入

详见 [PERFORMANCE_BASELINE_REPORT.md](PERFORMANCE_BASELINE_REPORT.md)。概览/账号/任务/设置各1个初始化API；记录/不再联系各2个。单可见标签页约4次state/分钟，打开弹窗或隐藏则0。记录刷新state+records可减冗余，但要保留状态信息与原contract。单次加载未出现重复同路径请求。

`load→render→innerHTML` 每次完整创建页面内容并重绑 onclick；非SPA导航会创建新文档，旧定时器自然销毁。onclick替换本身不等于监听器泄漏。列表 find/filter 的重复扫描随任务/账号/群增长，后续可请求内构造映射，但不能改变同ID跨账号显示优先级。

编辑弹窗在content外且阻断新轮询，草稿正常保留；未发现在定时刷新中直接覆盖正在编辑的关键词。保存时按钮disabled已实现，非保存请求（搜索/分页）没有同等pending/error处理。`serviceHeader`无条件重新启用服务按钮可能干扰尚在进行的启动/停止请求；待补延迟竞争测试。

## Timer / Listener / Memory Leak 审计

- 前端1个interval、至多1个toast timeout，1个常驻modal点击监听器，动态按钮采用属性赋值。正常80次打开关闭后，GC后节点351/监听26稳定；这些计数包含检测脚本。
- 主引擎1个loop_task；每个在线账号一个NewMessage handler。mock两账号reload10次，仍2个client和2个handler。线上health看到2worker，生产进程内部listener/timer总数未测。
- stop会cancel并await主loop，断开worker、清空workers/identities；没有显式remove_event_handler，依赖丢弃client后回收。需验证异常断线与SDK内部引用，不直接宣称泄漏。
- account_locks没有释放；随被操作的不同账号ID增长，当前无删除账号API，因此不是每次登录都增加一个锁。
- Telethon事件并发任务/实体缓存需要实际群消息压力下测量。没有给库内部默认缓存杜撰TTL或上限。
- 历史relay_jobs、relay_contacts与轮询状态持久化增长属于数据库保留问题，不等于进程内存泄漏；不能改变永久去重保留语义。
- 旧WorkerManager的queue/health任务取消未await，Raw handler fire-and-forget，异常重连client管理较弱；当前入口不启动它，但workers中的登录/同步函数在使用，不能删除整个文件。
- Session作用域大体有finally close；engine退出没有显式dispose，进程退出由OS释放，不等价于每请求泄漏。pool_size=0可能长期保留并发峰值连接。

## 最大文件与函数

|文件|行数|判断|
|---|---:|---|
|static/console.css|1094|大量声明式样式；单凭行数不值得拆分|
|static/console.js|701|六页、弹窗、API、事件与全局状态，职责过宽|
|app/console_api.py|656|多个领域接口与序列化、校验集中|
|tests/test_console_api.py|582|测试按接口聚合，不是生产性能问题|
|app/relay_engine.py|555|最高运行时耦合|
|app/workers.py|526|旧worker与仍使用登录/同步混合|
|tests/test_relay.py|515|重要回归资产，保留|
|app/main.py|366|启动、认证、中间件和旧路由混合|

Python AST函数长度：send_job 133行（relay_engine:420）；on_message 100行（:154）；enqueue 69行（:303）；_reload_workers 66行（:70）；旧_send_job 63行（workers:332）。JS按顶层函数边界近似计数：bindPage 134行（:541）、taskDialog/loginDialog各54行、overview50行、accounts40行。模板巨长单行导致“行数少”不等于职责少。

## 重复、类型、配置、日志与依赖

- 明确重复：auth.py 与 scripts/hash_password.py 的密码哈希及Base64 helper。后者独立脚本导入行为需测试，不能只减少行数导致脚本导入运行服务。
- GroupID注解与MonitorGroupsInput手写类型/范围检查部分重复；错误消息、bool拒绝、去重边界要保留。
- 状态字符串、2秒/10tick/10条/50条/15秒散落；config中旧queue_poll_seconds=3并不控制新引擎2秒。只集中已有值，不能借机改默认或让旧设置改变新业务。
- Python业务DTO多数靠dict/SimpleNamespace、部分函数缺返回类型；可用轻量TypedDict/Protocol说明API与客户端边界。没有TypeScript any/as/非空断言问题，不需要凭空建立TS工程。
- globals：manager、engine、settings缓存以及JS state/page/recordOffset等；单实例约束下不等于错误，但测试隔离和依赖注入成本高。
- TODO/FIXME/TEMP/HACK/DEBUG扫描无对应待办标记；旧workers有5处print调用，包含消息截断内容与异常。当前主引擎没有高频tick日志。CLI生成密码的print是命令行输出用途，不按debug删除。
- `main.dashboard` return redirect之后旧渲染块为静态不可达，可列为小范围删除候选；旧路由和模板仍注册/引用，不能标记整个legacy为死代码。
- Python存在局部延迟导入回路（database初始化→models/account_settings→database，auth读取ConsoleState），当前测试/import正常；没有发现需要立即重写的启动循环依赖错误。
- dependency均固定版本；PyMySQL是原有可选连接适配、Jinja2用于模板、Alembic用于Schema兼容、PySocks用于代理，不能凭SQLite部署就全部删。pytest混在运行requirements可后续拆安装分组。没有升级或安装项目依赖。

## 可以安全优先优化的项目（本轮未执行）

1. 请求内复用 application_settings 和 SenderWeight 查询，先固定API JSON与查询计数测试。
2. 相同密码hash实现小范围复用；脚本行为和密码兼容必须保持。
3. 集中纯分页/轮询显示常量，值完全不变；不合并新旧状态机。
4. 为API响应/客户端接口补轻量类型；避免复杂泛型系统。
5. 删除确认不可达的dashboard return后代码及无用import，保留旧端点。
6. 在正常/失败请求的UI测试基础上统一搜索分页pending/error入口，不能隐藏失败。
7. 概览无变化时跳过内容DOM重建，服务状态/指标真正变化仍及时更新；补焦点、滚动和按钮状态测试后做。

## 高风险优化（先报告，不执行）

- 全局锁改为每账号锁、并发连接/发送、队列公平性调整、取消及发送中断语义。
- 权重分配候选批量化以外的算法/事务改变，去重前置与索引唯一约束调整。
- 绑定/账号配置跨事件缓存，必须覆盖所有修改入口的失效和发送前校验。
- 修改SQLite池大小/WAL/FK/超时、索引和迁移体系；索引须获得确认。
- 删除旧账号/群/规则路由或legacy表字段，拆Session生命周期，迁移account_b。
- 修改API contract、历史保留、分页游标、跨标签页共享状态。

## 推荐执行顺序与缺失测试

PHASE 1：先记录当前版本差异，补请求计数与输出相等测试，局部重复读取/无用代码/类型清理。

PHASE 2：前端无变化跳过DOM重建；覆盖焦点、滚动、关键词/文案/群ID/权重草稿；错误与pending一致。

PHASE 3：轮询单请求在途、旧响应隔离、records刷新冗余；对隐藏/恢复、跨页面导航、慢请求明确测试，保持刷新时效。

PHASE 4：批量读取profile/binding/weight、同步群批量读取，做输出/事务等价性验证；索引作为单独获批变更。

PHASE 5：连接失败清理、重连listener数量、shutdown取消；模拟重复start/stop、connect/get_dialogs超时、同步失败和账号隔离。

PHASE 6：用户确认后再处理队首阻塞和跨账号串行，先补同账号顺序、跨账号隔离、限流不换号、配置修改与发送竞争、崩溃unknown、去重/分配事务回归。现有单元测试覆盖正常链路和多种边界，但缺上述并发压力场景。

PHASE 7：24–72小时模拟高消息量与反复断线；记录RSS、FD、pool连接数、async tasks、listeners、DB体积。当前31秒/80次弹窗不能替代长期测试。

PHASE 8：同机器同fixture重测基线，输出Before/After与误差；真实Telegram只在指定测试群单独验证。每阶段独立运行pytest、JS语法和浏览器操作，不运行不存在的bun/TS/lint命令。

## 本轮验证与结束条件

61 tests passed，688条警告（现有datetime.utcnow、测试/依赖弃用等，未顺手改时间语义）。JS语法检查、git diff --check通过。桌面/手机模拟正常流程通过；故障注入发现的Promise错误如实记录，没有修复。

只新增审计报告和脱敏测量证据。原有未提交业务代码改动全部保留，不代表此次做了优化。PHASE 0到此停止，等待用户确认下一阶段。
