# 服务器部署记录

部署日期：2026-09-22（北京时间）。应用版本：c655fa1。
服务器：113.20.3.218，Ubuntu 24.04 / Python 3.12。

## 访问

服务器后台通过 SSH 加密隧道映射到本机：http://127.0.0.1:18765/console/accounts。
此地址访问服务器上的服务；原本机开发服务仍在 8765。隧道仅绑定本机回环地址，其他电脑/手机不能直接使用本机地址访问。

后台用户名为 admin，独立随机密码保存在本机 `artifacts/server-access.json`（0600），没有存入版本控制。
服务器原件位于 `/var/lib/telegram-ops/.local-access.json`。修改后台密码后，该初始密码文件不会更新。

当前电脑重启或隧道断开后，可双击 `artifacts/打开服务器后台.command`，输入服务器 SSH 密码重建连接；脚本不保存 SSH 密码。

公网 HTTPS 尚未配置。若需要多设备直接访问，可后续绑定域名并配置 HTTPS。

## 服务与数据

- systemd 服务：telegram-ops-console.service，开机启动、异常退出重启，单进程。
- 程序：`/opt/telegram-ops-console/telegram-ops`。
- 独立运行用户：telegram-ops；运行环境：`/opt/telegram-ops-console/.venv`。
- 数据库：`/var/lib/telegram-ops/telegram_ops.db`。
- 配置和加密密钥：`/var/lib/telegram-ops/.env`，权限 0600；务必与数据库一起备份。
- 监听：127.0.0.1:8765；后台 Cookie 名称与本机开发版分离。
- 内存软阈值 512 MiB、硬上限 768 MiB；日志由 systemd journal 管理。
- 自动备份：telegram-ops-backup.timer，每日 04:15 UTC 附加最多 5 分钟随机延迟，保留最近 7 份。
- 备份目录：`/var/backups/telegram-ops`，含一致性 SQLite 备份、加密配置、初始后台凭据，目录 0700、归档 0600。此为同机备份，不是异地备份。

## 验证结果

- Linux 隔离测试数据库上 41 项测试通过，没有调用真实 Telegram 发信。
- 健康检查成功；服务器网页登录和六个页面验证通过，无浏览器脚本错误。
- 首次备份完成，备份数据库 quick_check 通过，定时器已启用。
- 初始 Telegram 业务服务暂停；尚未填写 API 凭据、登录 Telegram 账号或创建群组绑定。
- 完成时新服务内存约 77 MiB，服务器可用内存约 1.3 GiB。
- 原 telegram-forwarder 服务保持运行、PID 13451 未变化；其程序、数据库、服务配置均未修改。
- 安装 Python 虚拟环境组件时，包管理器同步更新了匹配的 Python 3.12 系统依赖；未执行系统整体升级或重启。

常用管理：

```sh
systemctl status telegram-ops-console
journalctl -u telegram-ops-console -n 100 --no-pager
systemctl restart telegram-ops-console
systemctl start telegram-ops-backup
```

没有复制本机开发数据库、Telegram 会话或开发版后台密码到服务器。真实账号收发、群组权限和生产消息量需在配置后另行联调。
