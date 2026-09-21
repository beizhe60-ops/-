from fastapi.testclient import TestClient
from app.main import app
from app.database import session_scope
from app.models import Account, Chat
from app.relay_models import RelayJob, AccountProfile

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
                AccountProfile(
                    account_id=i,
                    role="monitor" if i == 1 else "sender",
                    monitor_chat_ids="[-1001]" if i == 1 else "[]",
                )
            )
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
        d = dict(phone="+12345678901", role="monitor")
        assert c.post("/api/accounts", json=d, headers=HEAD).status_code == 400
        assert (
            c.put(
                "/api/application",
                json={"api_id": 1234, "api_hash": "a" * 32},
                headers=HEAD,
            ).status_code
            == 200
        )
        r = c.post("/api/accounts", json=d, headers=HEAD)
        assert r.status_code == 200
        assert r.json()["role"] == "monitor" and r.json()["monitor_chat_ids"] == []
        assert "api_hash" not in r.text and "a" * 32 not in c.get("/api/state").text
        assert c.post("/api/accounts", json=d, headers=HEAD).status_code == 409
        r = c.put(
            "/api/application",
            json={"api_id": 1234, "api_hash": "SECRET_BAD_HASH"},
            headers=HEAD,
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


def test_shared_application_credentials_and_role_defaults():
    from app.crypto import decrypt_text
    from app.relay_models import ConsoleState

    with TestClient(app) as c:
        login(c)
        config = {"api_id": 12345, "api_hash": "b" * 32}
        assert c.put("/api/application", headers=HEAD, json=config).status_code == 200
        for phone, role in [("+12345678901", "monitor"), ("+12345678902", "sender")]:
            r = c.post(
                "/api/accounts", headers=HEAD, json={"phone": phone, "role": role}
            )
            assert r.status_code == 200 and r.json()["role"] == role
            assert r.json()["private_message_enabled"] == (role == "sender")
        with session_scope() as db:
            assert "b" * 32 not in db.get(ConsoleState, "telegram_application").value
            for a in db.query(Account):
                assert (
                    a.api_id == 12345 and decrypt_text(a.api_hash_encrypted) == "b" * 32
                )
        assert (
            c.put(
                "/api/application", headers=HEAD, json={"api_id": 12345, "api_hash": ""}
            ).status_code
            == 200
        )
        assert (
            c.put(
                "/api/application", headers=HEAD, json={"api_id": 6789, "api_hash": ""}
            ).status_code
            == 422
        )
        assert (
            c.post(
                "/api/accounts",
                headers=HEAD,
                json={"phone": "+12345678903", "role": "other"},
            ).status_code
            == 422
        )


def test_monitor_groups_exact_ids_validation_and_task_pause():
    from app.relay_models import RelayTask

    with TestClient(app) as c:
        login(c)
        seed()
        for invalid in [[100], ["-1001"], [True], [-1.5]]:
            assert (
                c.put(
                    "/api/accounts/1/monitor-groups",
                    headers=HEAD,
                    json={"chat_ids": invalid},
                ).status_code
                == 422
            )
        assert (
            c.put(
                "/api/accounts/2/monitor-groups",
                headers=HEAD,
                json={"chat_ids": [-1001]},
            ).status_code
            == 422
        )
        assert c.put(
            "/api/accounts/1/monitor-groups",
            headers=HEAD,
            json={"chat_ids": [-1001, -1003, -1001]},
        ).json()["chat_ids"] == [-1003, -1001]
        data = dict(
            name="指定群任务",
            account_a=1,
            account_b=2,
            source_chats=[-1003],
            relay_chat=-1002,
            keywords="咨询",
            template="你好",
        )
        # Explicit IDs do not require source dialogs to have been synced.
        task = c.post("/api/tasks", headers=HEAD, json=data).json()
        id = task["id"]
        assert c.post(f"/api/tasks/{id}/toggle", headers=HEAD).json()["enabled"]
        with session_scope() as db:
            db.add(
                RelayJob(
                    task_id=id,
                    stage="relay",
                    account_id=1,
                    chat_id=-1003,
                    message_id=1,
                    username="target_user",
                    original_text="咨询",
                    text="群-咨询-@target_user",
                )
            )
        r = c.put(
            "/api/accounts/1/monitor-groups", headers=HEAD, json={"chat_ids": [-1001]}
        )
        assert r.json()["paused_tasks"] == 1
        with session_scope() as db:
            assert not db.get(RelayTask, id).enabled
            assert db.get(RelayTask, id).source_chats == "[]"
            assert db.query(RelayJob).one().status == "cancelled"


def test_legacy_profiles_migration_is_idempotent():
    from app.account_settings import migrate_profiles
    from app.relay_models import RelayTask

    with session_scope() as db:
        db.add(
            Account(
                id=1,
                name="oldA",
                phone="+12345678901",
                api_id=1,
                api_hash_encrypted="test",
                private_message_enabled=False,
            )
        )
        db.add(
            Account(
                id=2,
                name="oldB",
                phone="+12345678902",
                api_id=1,
                api_hash_encrypted="test",
                private_message_enabled=True,
            )
        )
        db.add(
            RelayTask(
                name="old",
                account_a=1,
                account_b=2,
                source_chats="[-1001]",
                relay_chat=-1002,
                keywords="咨询",
                template="你好",
            )
        )
        db.flush()
        migrate_profiles(db)
        migrate_profiles(db)
        assert db.query(AccountProfile).count() == 2
        assert db.get(AccountProfile, 1).monitor_chat_ids == "[-1001]"
        assert db.get(AccountProfile, 2).role == "sender"


def test_sender_weight_validation_and_persistence():
    seed()
    with TestClient(app) as c:
        login(c)
        assert (
            c.put(
                "/api/accounts/1/weight", headers=HEAD, json={"weight": 3}
            ).status_code
            == 422
        )
        for bad in (0, -1, 1001, 1.5, True, "3"):
            assert (
                c.put(
                    "/api/accounts/2/weight", headers=HEAD, json={"weight": bad}
                ).status_code
                == 422
            )
        assert (
            c.put(
                "/api/accounts/2/weight", headers=HEAD, json={"weight": 3}
            ).status_code
            == 200
        )
        accounts = c.get("/api/state").json()["accounts"]
        assert next(a for a in accounts if a["id"] == 2)["rotation_weight"] == 3
