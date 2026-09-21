"""Role and precise monitoring scope, separate from private account credentials."""

import json
from app.models import Account
from app.relay_models import AccountProfile, RelayTask, SenderBinding


def migrate_profiles(db):
    for account in db.query(Account).all():
        if db.get(AccountProfile, account.id):
            continue
        tasks = db.query(RelayTask).filter(RelayTask.account_a == account.id).all()
        groups = sorted({gid for t in tasks for gid in json.loads(t.source_chats)})
        role = "monitor" if tasks or not account.private_message_enabled else "sender"
        db.add(
            AccountProfile(
                account_id=account.id, role=role, monitor_chat_ids=json.dumps(groups)
            )
        )
    db.flush()
    # Only explicit legacy task B bindings migrate. A synced dialog never grants scope.
    for profile in db.query(AccountProfile).filter_by(role="sender").all():
        if db.get(SenderBinding, profile.account_id):
            continue
        tasks = (
            db.query(RelayTask)
            .filter_by(account_b=profile.account_id)
            .order_by(RelayTask.id)
            .all()
        )
        db.add(
            SenderBinding(
                account_id=profile.account_id,
                chat_ids=json.dumps(sorted({t.relay_chat for t in tasks})),
                template=next((t.template for t in tasks if t.template.strip()), ""),
            )
        )
    db.flush()


def get_profile(db, account):
    profile = db.get(AccountProfile, account.id)
    if not profile:
        migrate_profiles(db)
        profile = db.get(AccountProfile, account.id)
    return profile
