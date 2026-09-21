"""Generate unique local admin credentials. Run from telegram-ops/ once."""

import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.auth import hash_password

env = Path(".env")
if env.exists():
    raise SystemExit("已有 .env，未覆盖。首次部署请移走上游示例 .env 后重试。")
password = secrets.token_urlsafe(18)
env.write_text(
    "APP_SECRET_KEY="
    + secrets.token_urlsafe(48)
    + "\nADMIN_USERNAME=admin\nADMIN_PASSWORD=\nADMIN_PASSWORD_HASH="
    + hash_password(password)
    + "\nDATABASE_URL=sqlite:///./telegram_ops.db\nAUTO_START_TELEGRAM_WORKERS=false\n"
)
env.chmod(0o600)
access = Path(".local-access.json")
access.write_text(
    json.dumps(
        {"username": "admin", "password": password}, ensure_ascii=False, indent=2
    )
)
access.chmod(0o600)
print("已生成独立后台凭据，保存在 .local-access.json；.env 已设置为仅当前用户可读。")
