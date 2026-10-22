"""Read-only record filters and exception totals; no queue state changes."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from app.relay_models import RelayJob, RelayTask


def period_start(period, now=None):
    now = now or datetime.utcnow()
    if period == "today":
        # Stored timestamps are UTC; the console's day is Beijing time.
        local = now.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=8)))
        return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
    if period == "7d":
        return now - timedelta(days=7)
    return None


def exception_totals(db):
    counts = dict(db.query(RelayJob.status, func.count(RelayJob.id))
                  .filter(RelayJob.status.in_(("failed", "unknown")))
                  .group_by(RelayJob.status).all())
    return {"failed": counts.get("failed", 0), "unknown": counts.get("unknown", 0)}


def filtered_records(db, status, search, period, task_id):
    query = db.query(RelayJob)
    if status == "exceptions":
        query = query.filter(RelayJob.status.in_(("failed", "unknown")))
    elif status:
        query = query.filter(RelayJob.status == status)
    if search:
        query = query.filter(RelayJob.username.contains(search.lstrip("@"), autoescape=True))
    start = period_start(period)
    if start is not None:
        query = query.filter(RelayJob.created_at >= start)
    if task_id is not None:
        query = query.filter(RelayJob.task_id == task_id)
    return query


def record_tasks(db):
    # Include deleted tasks so their historical results remain discoverable.
    return [dict(id=t.id, name=t.name, deleted=t.deleted_at is not None)
            for t in db.query(RelayTask).order_by(RelayTask.id.desc()).all()]
