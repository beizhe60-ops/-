"""Repeatable updates for console tables created outside Alembic."""
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import BigInteger, inspect


def allow_unconfigured_relay_target(engine):
    """Allow paused drafts; preserve existing routes and referencing jobs."""
    with engine.connect() as conn:
        column = next(c for c in inspect(conn).get_columns('relay_tasks') if c['name'] == 'relay_chat')
        if column['nullable']:
            return
        sqlite = conn.dialect.name == 'sqlite'
        foreign_keys = conn.exec_driver_sql('PRAGMA foreign_keys').scalar() if sqlite else None
        conn.commit()
        if sqlite:
            conn.exec_driver_sql('PRAGMA foreign_keys=OFF')
            conn.commit()
        try:
            if sqlite:
                conn.exec_driver_sql('BEGIN IMMEDIATE')
            else:
                conn.begin()
            ops = Operations(MigrationContext.configure(conn))
            with ops.batch_alter_table('relay_tasks') as batch:
                batch.alter_column('relay_chat', existing_type=BigInteger(), nullable=True)
            if sqlite and conn.exec_driver_sql('PRAGMA foreign_key_check').first():
                raise RuntimeError('群组目标字段升级失败：数据库存在无效关联')
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if sqlite:
                conn.exec_driver_sql(f'PRAGMA foreign_keys={int(foreign_keys)}')
                conn.commit()


def add_task_deletion_marker(engine):
    """Keep task/job history while removing deleted bindings from active UI."""
    from sqlalchemy import Column, DateTime
    with engine.begin() as conn:
        if "deleted_at" not in {c["name"] for c in inspect(conn).get_columns("relay_tasks")}:
            Operations(MigrationContext.configure(conn)).add_column(
                "relay_tasks", Column("deleted_at", DateTime(), nullable=True)
            )


def add_message_copy_options(engine):
    from sqlalchemy import Column, Text
    with engine.begin() as conn:
        ops = Operations(MigrationContext.configure(conn))
        for table in ("sender_bindings", "relay_jobs"):
            if "copy_options" not in {c["name"] for c in inspect(conn).get_columns(table)}:
                ops.add_column(table, Column("copy_options", Text(), nullable=False, server_default="{}"))


def add_channel_automation(engine):
    """Additive migration: existing feeds stay manual and existing posts retain status."""
    from sqlalchemy import Column, Boolean, String, Integer
    columns = {
        'channel_feeds': [Column('auto_publish', Boolean(), nullable=False, server_default='0'),
                          Column('template_title', String(60), nullable=False, server_default='曝光档案')],
        'channel_drafts': [Column('automatic', Boolean(), nullable=False, server_default='0'),
                           Column('config_revision', Integer(), nullable=True)],
    }
    with engine.begin() as conn:
        ops = Operations(MigrationContext.configure(conn))
        for table, additions in columns.items():
            existing = {c['name'] for c in inspect(conn).get_columns(table)}
            for column in additions:
                if column.name not in existing:ops.add_column(table,column)
