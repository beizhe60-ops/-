from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

# SQLite 特殊配置
if settings.database_url.startswith("sqlite"):
    connect_args["timeout"] = 30
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
        connect_args=connect_args,
        pool_size=0,
        max_overflow=0,
    )
else:
    engine = create_engine(settings.database_url, pool_pre_ping=True, future=True, connect_args=connect_args)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    from app import models, relay_models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    from app.schema_updates import allow_unconfigured_relay_target
    allow_unconfigured_relay_target(engine)
    from app.account_settings import migrate_profiles
    with session_scope() as db:
        migrate_profiles(db)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Session:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
