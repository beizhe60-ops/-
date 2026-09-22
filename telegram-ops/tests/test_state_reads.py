"""State queries preserve defaults, fresh configuration and secret redaction."""
import json
import pytest
from sqlalchemy.orm import Session
from app.console_api import account_json, state
from app.models import Account
from app.relay_models import AccountProfile, SenderBinding, RelayTask
from fastapi.testclient import TestClient
from sqlalchemy import event
from app.database import engine, session_scope
from app.main import app
from app.relay_models import ConsoleState, SenderWeight
from test_console_api import login, seed


def test_state_reads_configuration_once_and_stays_fresh():
    with TestClient(app) as client:
        seed()
        with session_scope() as db:
            db.add(ConsoleState(key="telegram_application", value=json.dumps({
                "api_id": 123, "api_hash_encrypted": "fictional-secret"})))
            db.add(SenderWeight(account_id=2, weight=7))
        login(client)
        statements = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append((statement, parameters))
        event.listen(engine, "before_cursor_execute", capture)
        try:
            response = client.get("/api/state")
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert response.status_code == 200
        data = response.json()
        assert data["application"] == {"configured": True, "api_id": 123}
        assert [a["rotation_weight"] for a in data["accounts"]] == [1, 7]
        assert data["accounts"][0]["monitor_chat_ids"] == [-1001]
        assert data["accounts"][1]["receive_chat_ids"] == []
        assert "fictional-secret" not in response.text
        assert len([s for s, p in statements if "FROM sender_weights" in s]) == 1
        assert len([s for s, p in statements if "FROM console_state" in s
                    and "telegram_application" in p]) == 1
        with session_scope() as db:
            db.get(SenderWeight, 2).weight = 9
            db.get(ConsoleState, "telegram_application").value = json.dumps({"api_id": 456})
        updated = client.get("/api/state").json()
        assert updated["application"] == {"configured": True, "api_id": 456}
        assert updated["accounts"][1]["rotation_weight"] == 9
        with session_scope() as db:
            db.delete(db.get(ConsoleState, "telegram_application"))
            db.delete(db.get(SenderWeight, 2))
        cleared = client.get("/api/state").json()
        assert cleared["application"] == {"configured": False, "api_id": None}
        assert cleared["accounts"][1]["rotation_weight"] == 1


def test_dashboard_still_redirects_to_console():
    with TestClient(app) as client:
        login(client)
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/console"


@pytest.mark.parametrize("count", [2, 100])
def test_state_account_queries_are_bounded(count):
    seed()
    with session_scope() as db:
        for i in range(3, count + 1):
            db.add(Account(id=i, name=f"Account {i}", phone=f"+1202555{i:04}",
                           api_id=1, api_hash_encrypted="fictional"))
            db.add(AccountProfile(account_id=i, role="sender", monitor_chat_ids="[]"))
        db.add(SenderWeight(account_id=2, weight=7))
        db.add(SenderBinding(account_id=2, chat_ids="[-1002]", template="测试文案"))
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with Session(engine) as db:
            result = state(db)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(result["accounts"]) == count
    assert result["accounts"][1]["rotation_weight"] == 7
    assert result["accounts"][1]["dm_template"] == "测试文案"
    for table in ("account_profiles", "sender_bindings", "sender_weights"):
        assert len([s for s in statements if "FROM " + table in s]) == 1


@pytest.mark.parametrize("missing_profile", [False, True])
def test_batch_state_preserves_account_payload_and_legacy_fallback(missing_profile):
    seed()
    with session_scope() as db:
        db.add(SenderBinding(account_id=1, chat_ids="[]", template=""))
        db.add(SenderWeight(account_id=2, weight=19))
        db.add(RelayTask(name="旧任务", account_a=1, account_b=2,
                         source_chats="[-1001]", relay_chat=-1002,
                         keywords="咨询", template="旧文案"))
        if missing_profile:
            db.delete(db.get(AccountProfile, 2))
    # Roll back any incidental legacy migration before checking the new path.
    with Session(engine) as db:
        expected = [account_json(a, db) for a in db.query(Account).order_by(Account.id).all()]
    with Session(engine) as db:
        actual = state(db)["accounts"]
    assert actual == expected
    assert actual[0]["receive_chat_ids"] == []
    assert actual[1]["receive_chat_ids"] == ([-1002] if missing_profile else [])
    assert actual[1]["dm_template"] == ("旧文案" if missing_profile else "")
