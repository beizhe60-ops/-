# telegram-ops 🚀 (电报运维自动化工具)

`telegram-ops` 是一款专为跨境电商、私域运营、出海团队以及网络安全情报分析（OSINT）打造的 **Telegram 多账号全自动关键字监听与响应矩阵**。

基于 Python 顶级的异步客户端库 **Telethon (MTProto API)** 开发，自带美观的 Web 管理面板。无论是监控行业竞品消息、自动化社群引流，还是敏感词情报归档，它都能为你实现无人值守全天候运行。

---

## ✨ 核心功能

- 👥 **多账号矩阵管理**：Web 界面直观授权登录，支持批量导入。每个账号的事件循环（Event Loop）独立运行，互不干扰。
- 🌐 **全协议代理矩阵 (Multi-Protocol Proxy Matrix)**：
  - 支持 **Socks5、Socks4 以及 HTTP 代理**（支持用户名/密码鉴权）。
  - **动态分流架构**：支持为每个电报账号（Session）单独配置独立的代理 IP，有效防止因单 IP 多账号登录触发 Telegram 官方风控，实现高安全性的矩阵防封。
- 🔍 **毫秒级关键字监听**：基于异步架构，实时嗅探所有关联频道（Channels）、私聊（Private Chats）和大群（Supergroups）中的目标文本。
- 🤖 **智能反封号自动响应**：支持关键词命中后自动触发【私信对方】或【群内回复】。冷却队列、账号轮询，最大程度降低被官方限制的风险。
- 📦 **结构化消息归档**：命中历史完整记录，直接存储于 SQLite 数据库，方便对接后续的操作。
- 🖥️ **可视化配置面板**：无需修改代码，在网页端即可动态修改关键字规则、查看实时监听日志流。

---

## 🛠️ 典型应用场景

1. **跨境私域引流**：监控同行公开群，一旦有用户发“怎么买”、“求推荐”，系统自动私信或群回复你的产品链接。
2. **安全情报分析 (OSINT)**：全量监听特定暗网或技术情报频道，命中“漏洞”、“0day”、“Leak”等词汇时自动归档并触发报警。
3. **社群高效客服**：多账号冒充官方客服，在大型官方群里实时解答包含“手续费”、“充值”、“报错”等关键字的用户问题。

---

## 🚀 生产环境快速部署 (强烈推荐 Docker)

为了避免本地 Python 环境冲突（如系统包污染等问题），我们推荐直接使用 **Docker 一键运行**。
1. 安装docker，可使用```curl -fsSL https://get.docker.com | bash -s docker```一键安装
2. 使用```git clone https://github.com/kevenlemon/telegram-ops.git && cd telegram-ops```下载代码
3. 按要求修改.env文件中的APP_SECRET_KEY,
修改.env文件中的ADMIN_PASSWORD字段密码或按以下步骤设置密码HASH
```bash
python3 ./telegram-ops/scripts/hash_password.py 
```
按提示输入admin密码，将生成的密码hash填入.env文件的ADMIN_PASSWORD_HASH字段并将ADMIN_PASSWORD置空
4. ```docker-compose up -d```启动docker
# 操作说明
1. 登录telegram账户，加入要监听的群组（有的群组需要人工验证），可参考下方的群组。
2. 添加telegram账号,填入账号和app id及api hash,按需选择允许发送、允许自动私信和代理。(./IMAGES/telegram1.jpg)
3. 登录账户，账户状态为login_require时发送验证码，填写收到的验证码和密码（如果有的话），验证通过后状态栏显示为active，再进行同步群组。(./IMAGES/telegram2.jpg)
4. 在群组页面查看同步的群组,要开启监听，确保监听一栏为True，可通过启用/禁用切换状态。(./IMAGES/telegram3.jpg)
5. 在规则页面新增监听规则，模式可选keyword/regex，发送模式可选record_only/group_reply/private_message/both,新注册的号建议每天上限不超过5-10，老号建议不超过20-50，冷却时间为两次私信之间的时间间隔，默认为1天，自行修改，建议不少于3600（1小时）,不然容易被封号。(./IMAGES/telegram4.jpg)


# 安全措施
- 后台账号密码认证。
- `ADMIN_ALLOWED_IPS` 只允许指定公网 IP 访问。
- `ADMIN_COOKIE_SECURE=false`，保证 HTTP 下登录 Cookie 可用。
- 防火墙只开放 SSH 和应用端口。

# 注意

- 不用 HTTPS 时，登录密码和 Cookie 不加密传输；IP 白名单用于减少暴露面。
- 如果你的公网 IP 变化，会被系统拦截，需要 SSH 登录服务器修改 `.env` 后重启。
- 不要开放不需要的端口。

# 搜索群组推荐
| 名字       | 链接                                                                 | 功能描述                                   |
| :--------- | :------------------------------------------------------------------: | :---------------------------------------- |
| **新币搜索** | [@xbso](https://t.me/xbso1?start=a_7202424896) | 搜索群、频道、影视、音乐、新闻等内容 |
| **SOSO 机器人** | [@soso](https://t.me/soso?start=a_6294881820) | 先改成中文用户名再搜索群组/频道/视频，带 “SOSO” 后缀可赚取 0.5 USDT |
| **极搜 JiSou** | [@jisou](https://t.me/jisou2?start=a_7202424896) | 搜索群、频道、影视、音乐、新闻等内容 |
| **中文导航 中文搜索** | [@GGYHX](https://t.me/GGYHX) ｜ 搜索群 |
| **神马搜索（签到送 USDT）** | [@smss](https://t.me/smss?start=spread_7202424896) | 搜索群组资源，每日签到，连续 7 天送 3 USDT |
| 超级索引  | [@CJSY](https://t.me/CJSY?start=7202424896)                  | 发送词语即可搜索关联群组与频道资源     |
| **搜啦** | [@soula](https://t.me/soula?start=a_7202424896) | 可以轻松搜索Telegram群组、频道，以及视频、音乐等各种资源 |
| **快搜** | [@kuai](https://t.me/kuai?start=a_3B44YPB) | 帮你发现有趣群组、频道、视频、音乐、电影、新闻 |
| **🚀 免费节点** | [vpnnav.github.io](https://vpnnav.github.io) | 每天整点更新高速节点 |
| **🚀 机场推荐** | [@jichangtuijian](https://github.com/vpnnav/jichangtuijian) | 2026年最新低价高速机场推荐、机场大全、VPN导航、机场导航 |
| 赔钱机场      |       [官网](https://xn--mes358aby2apfg.com/register?code=ZiP66w57)      | 全网最便宜机场，18块1000G不限时流量 |
| **币圈学习资料** | [awesome-crypto](https://github.com/itgoyo/awesome-crypto) | 币圈学习导航 💰 推荐注册 [币安](https://accounts.binance.com/zh-CN/register?ref=896983517) 或 [欧易](https://www.chouyi.pro/zh-hans/join/50253981) 交易所 |
| **加密货币交流群** | [@jmhbgroup](https://t.me/jmhbgroup) | 加密货币交流 → [币圈导航](https://www.0xnav.com) |
| ⚡️能量闪租     |       **`TGuXv6H1s84cmQZk7akvWHC6P789999999`**      | 🟩1笔USDT转帐能量: 3TRX </br> 🟨2笔USDT转帐能量: 6TRX |
| ⚡️TRX闪兑     |       **`TY4etzSftahyH5DYDMq5kDuPs93VVVVVVV`**      | TRX-USDT24小时自动兑换，1U起兑 |
| ⚡️能量机器人   | [@trxsosobot](https://t.me/trxsosobot)            | 电报导航、能量闪兑、能量租赁、地址监听、ID查询、实时U价、自助开通电报会员(全网最便宜)   |
| **💎 电报会员机器人** | [@tg2vipbot](https://t.me/tg2vipbot)或[@vip2tgbot](https://t.me/vip2tgbot) | 自助开通 Telegram 会员，支持 USDT/微信/支付宝，1 秒克隆同款机器人打造被动收入,支持闪对、会员星星 → [通知群](https://t.me/nenglianghuiyuan) |
| **💎 手动充值会员** | [https://faka.tg10000.com](https://faka.tg10000.com) | 手动开通会员（109–259 元），支持交易所红包支付 → [通知群](https://t.me/tgviptongzhi) |
| **🌈 彩虹群发器** | [购买链接](https://faka.tg10000.com/item/15#buy) |多账号管理·一键群发·自动加群·用户采集·智能炒群·适合推广|
| **免费频道搬运机器人** | [@xnby08bot](https://t.me/xnby08bot?start=invite_8105886270) | 频道搬运、备份、模仿 → [教程](https://www.youtube.com/watch?v=rV6vIMFTAPA) · 支持自定义广告按钮 [通知群](https://t.me/xiunvyewu)|
| **搜索群①** | [@sousuo20w](https://t.me/sousuo20w) | 搜索任意资源(加群要过人机校验，防止刷子) |
| **搜索群②** | [@jiso5173](https://t.me/jiso5173) | 搜索任意资源(加群要过人机校验，防止刷子) |
| **搜索群③** | [@jisoubar](https://t.me/jisoubar) | “极搜吧”，支持资源搜索(加群要过人机校验，防止刷子) |
| **搜索群④** | [@sepiansousuo](https://t.me/sepiansousuo) | 搜索任意资源(加群要过人机校验，防止刷子) |
| **搜索群⑤** | [@kuaisou20w](https://t.me/kuaisou20w) | 搜索任意资源(加群要过人机校验，防止刷子) |
| 👚AI换装机器人   | [@Xai1314bot](https://t.me/Xai1314bot?start=NJOTH8D6MF7PYIL8)       | 黑科技ai智能机器人，一键去衣换装换脸/视频换脸采用最新ai模型无需建模|
| **🍉 吃瓜无限** | [@chiguawuxian](https://t.me/chiguawuxian) | 实时分享全网最新热门瓜 |


# 免责声明
本工具仅用于合规的社群运维、DevOps 自动化监控及网络安全学术研究。请严格遵守 Telegram 服务条款。因违反相关法律法规或滥用导致账号被封禁、引发法律纠纷的，责任由使用者自行承担。