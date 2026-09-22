import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.passwords import hash_password


def main() -> None:
    password = getpass.getpass("Admin password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("passwords do not match")
    print(hash_password(password))


if __name__ == "__main__":
    main()
