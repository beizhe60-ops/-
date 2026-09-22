import os
import subprocess
import sys
from pathlib import Path

from app.auth import verify_password


def test_standalone_password_script_works_without_app_configuration(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/hash_password.py"
    env = dict(os.environ, DATABASE_URL="invalid://unused", APP_SECRET_KEY="")
    result = subprocess.run([sys.executable, "-c", "import runpy, getpass; "
                             "getpass.getpass=lambda prompt: 'fixture-password'; "
                             "runpy.run_path(__import__('sys').argv[1], run_name='__main__')", str(script)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, check=True)
    encoded = result.stdout.strip()
    assert encoded.startswith("pbkdf2_sha256$260000$")
    assert verify_password("fixture-password", encoded)
    assert not verify_password("wrong", encoded)
    assert not result.stderr
