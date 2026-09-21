from fastapi.testclient import TestClient
from app.main import app
from app.database import session_scope
from app.models import Account, Chat
from app.relay_models import RelayJob

HEAD = {"X-Console-Request": "1"}


def login(c):
    assert (
        c.post(
            "/admin/login",
            data={"username": "admin", "password": "test-password-long"},
            follow_redirects=False,
        ).status_code
        == 303
    )


def seed():
    with session_scope() as db:
        for i in (1, 2):
            db.add(
                Account(
                    id=i,
                    name=f"A{i}",
                    phone=f"+1234567890{i}",
                    api_id=1,
                    api_hash_encrypted="test",
                    session_string_encrypted="test",
                    status="active",
                    send_enabled=True,
                    private_message_enabled=True,
                )
            )
            db.flush()
            db.add(
                Chat(
                    account_id=i,
                    telegram_chat_id=-1002,
                    title="中转群",
                    type="supergroup",
                )
            )
        db.add(
            Chat(
                account_id=1, telegram_chat_id=-1001, title="来源群", type="supergroup"
            )
        )


def test_auth_csrf():
    with TestClient(app) as c:
        assert c.get("/api/state").status_code == 401
        login(c)
        assert c.get("/api/state").status_code == 200
        assert c.post("/api/service/stop").status_code == 403
        assert (
            c.post(
                "/api/service/stop",
                headers={**HEAD, "Origin": "https://attacker.example"},
            ).status_code
            == 403
        )
        assert c.post("/api/service/stop", headers=HEAD).status_code == 200


def test_pages():
    with TestClient(app) as c:
        login(c)
        for p in ("", "/accounts", "/tasks", "/records", "/guards", "/settings"):
            assert c.get("/console" + p).status_code == 200
        d = c.get("/api/state").json()
        assert d["tasks"] == [] and d["accounts"] == [] and not d["running"]


def test_account_validation_secrets():
    with TestClient(app) as c:
        login(c)
        d = dict(name="监听A", phone="+12345678901", api_id=1234, api_hash="a" * 32)
        r = c.post("/api/accounts", json=d, headers=HEAD)
        assert r.status_code == 200
        assert "api_hash" not in r.text and "a" * 32 not in c.get("/api/state").text
        assert c.post("/api/accounts", json=d, headers=HEAD).status_code == 409
        r = c.post(
            "/api/accounts", json={**d, "api_hash": "SECRET_BAD_HASH"}, headers=HEAD
        )
        assert r.status_code == 422 and "SECRET_BAD_HASH" not in r.text


def test_task_validation_edit():
    with TestClient(app) as c:
        login(c)
        seed()
        d = dict(
            name="咨询任务",
            account_a=1,
            account_b=2,
            source_chats=[-1001],
            relay_chat=-1002,
            keywords="咨询",
            template="你好",
        )
        for bad in [
            {"account_b": 1},
            {"source_chats": [-999]},
            {"source_chats": [-1002]},
        ]:
            assert (
                c.post("/api/tasks", json={**d, **bad}, headers=HEAD).status_code == 422
            )
        r = c.post("/api/tasks", json=d, headers=HEAD)
        assert r.status_code == 200
        t = r.json()
        assert not t["enabled"]
        id = t["id"]
        assert c.post(f"/api/tasks/{id}/toggle", headers=HEAD).json()["enabled"]
        with session_scope() as db:
            db.add(
                RelayJob(
                    task_id=id,
                    stage="dm",
                    account_id=2,
                    chat_id=-1002,
                    message_id=50,
                    username="target_user",
                    original_text="咨询",
                    text="旧文案",
                )
            )
        r = c.put(f"/api/tasks/{id}", json={**d, "template": "新文案"}, headers=HEAD)
        assert not r.json()["enabled"]
        with session_scope() as db:
            assert db.query(RelayJob).one().status == "cancelled"


def test_preview_no_sends():
    with TestClient(app) as c:
        login(c)
        d = c.post(
            "/api/preview",
            headers=HEAD,
            json={
                "text": "咨询 @someone_else",
                "username": "actual_user",
                "keywords": "咨询",
                "template": "您好 {{ username }}",
            },
        ).json()
        assert (
            d["matched"]
            and d["relay"].endswith("-@actual_user")
            and d["dm"] == "您好 @actual_user"
        )
        with session_scope() as db:
            assert db.query(RelayJob).count() == 0


def test_password_change():
    with TestClient(app) as c:
        login(c)
        assert (
            c.post(
                "/api/password",
                headers=HEAD,
                json={"current_password": "wrong", "new_password": "new-long-password"},
            ).status_code
            == 400
        )
        assert (
            c.post(
                "/api/password",
                headers=HEAD,
                json={
                    "current_password": "test-password-long",
                    "new_password": "new-long-password",
                },
            ).status_code
            == 200
        )
        assert c.get("/api/state").status_code == 401
        assert (
            c.post(
                "/admin/login",
                data={"username": "admin", "password": "new-long-password"},
                follow_redirects=False,
            ).status_code
            == 303
        )
        assert c.get("/api/state").status_code == 200


def test_redirect_is_local():
    with TestClient(app) as c:
        r = c.post(
            "/admin/login",
            data={
                "username": "admin",
                "password": "test-password-long",
                "next": "//evil.example",
            },
            follow_redirects=False,
        )
        assert r.headers["location"] == "/console"
