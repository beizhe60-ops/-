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
