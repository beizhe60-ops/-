# Telegram Ops · 中文消息工作台

基于 [kevenlemon/telegram-ops](https://github.com/kevenlemon/telegram-ops) 的 A/B 群消息中转工作台。

```
A 监听指定群 → 关键词过滤 → 群组-发言内容-@用户名 → 中转群 → B 自动私信
```

网页配置账号登录、来源群、中转群、关键词、排除词、忽略用户与私信文案；提供不发送的模拟测试、持久化队列、用户去重、运行日志和暂停控制。

- [部署与使用说明](DEPLOYMENT.zh.md)
- [界面设计说明](DESIGN.md)
- [MIT 许可证](LICENSE)

当前版本已进行本地自动化与浏览器验证。真实账号联调和远程服务器部署尚未进行。
