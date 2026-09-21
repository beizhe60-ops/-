import os
import tempfile
from pathlib import Path
import pytest

_tmp = tempfile.TemporaryDirectory(prefix="tg-console-tests-")
os.environ.update(
    DATABASE_URL="sqlite:///" + str(Path(_tmp.name) / "test.db"),
    APP_SECRET_KEY="tests-only-secret-12345678901234567890",
    ADMIN_PASSWORD="test-password-long",
    ADMIN_PASSWORD_HASH="",
    AUTO_START_TELEGRAM_WORKERS="false",
)


@pytest.fixture(autouse=True)
def database():
    from app.database import Base, engine
    from app import models, relay_models

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
