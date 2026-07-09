# 公网直连部署：不用 Nginx

适合只有一个人使用、希望简单部署的场景：

```text
浏览器 -> 服务器公网 IP:8000 -> Uvicorn/FastAPI
```

安全措施：

- 后台账号密码认证。
- `ADMIN_ALLOWED_IPS` 只允许指定公网 IP 访问。
- `ADMIN_COOKIE_SECURE=false`，保证 HTTP 下登录 Cookie 可用。
- 防火墙只开放 SSH 和应用端口。

## 1. `.env` 配置

```env
DATABASE_URL="sqlite:///./telegram_ops.db"
AUTO_START_TELEGRAM_WORKERS=true

ADMIN_USERNAME="admin"
ADMIN_PASSWORD=""
ADMIN_PASSWORD_HASH="pbkdf2_sha256$..."
ADMIN_COOKIE_SECURE=false

# 填你的公网出口 IP；多个 IP 用英文逗号分隔，也支持 CIDR。
ADMIN_ALLOWED_IPS="1.2.3.4"
```

如果暂时不知道自己的公网出口 IP，可以先留空：

```env
ADMIN_ALLOWED_IPS=""
```

但公网长期运行不建议留空。

支持示例：

```env
ADMIN_ALLOWED_IPS="1.2.3.4"
ADMIN_ALLOWED_IPS="1.2.3.4,5.6.7.8"
ADMIN_ALLOWED_IPS="1.2.3.0/24"
```

## 2. 配置修改
按要求修改.env文件中的APP_SECRET_KEY
修改.env文件中的ADMIN_PASSWORD字段密码或按以下步骤设置密码HASH

```bash
python3 ./telegram-ops/scripts/hash_password.py 
```
按提示输入admin密码，将生成的密码hash填入.env文件的ADMIN_PASSWORD_HASH字段并将ADMIN_PASSWORD置空

## 3. 防火墙

如果使用 UFW：

```bash
sudo ufw allow OpenSSH
sudo ufw allow 8000/tcp
sudo ufw enable
```

访问：

```text
http://服务器公网IP:8000
```


## 4. 注意

- 不用 HTTPS 时，登录密码和 Cookie 不加密传输；IP 白名单用于减少暴露面。
- 如果你的公网 IP 变化，会被系统拦截，需要 SSH 登录服务器修改 `.env` 后重启。
- 不要开放不需要的端口。
