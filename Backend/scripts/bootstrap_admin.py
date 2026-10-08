import argparse
import getpass
import sys

from sqlalchemy import select, text
from app.schemas import CreateUserRequest

from app.db import get_session_factory
from app.models import User
from app.security import hash_password


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the first CrashPulse administrator")
    parser.add_argument("--login-id", required=True)
    args = parser.parse_args()
    if not sys.stdin.isatty():
        raise SystemExit("bootstrap requires an interactive terminal so passwords are not echoed")
    password = getpass.getpass("Administrator password (12+ characters): ")
    confirmation = getpass.getpass("Repeat administrator password: ")
    if password != confirmation:
        raise SystemExit("passwords do not match")
    try:
        CreateUserRequest(login_id=args.login_id, password=password)
    except ValueError:
        raise SystemExit("login ID must be 3–100 letters/digits/._- and password must be 12–256 characters") from None
    factory = get_session_factory()
    with factory() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(73682002)"))
        if db.scalar(select(User).where(User.role == "ADMIN")) is not None:
            raise SystemExit("an administrator already exists; refusing to create another bootstrap account")
        if db.scalar(select(User).where(User.login_id == args.login_id)) is not None:
            raise SystemExit("login ID already exists")
        user = User(login_id=args.login_id, password_hash=hash_password(password), role="ADMIN", is_active=True)
        db.add(user)
        db.commit()
    print("administrator created")


if __name__ == "__main__":
    from scripts.common import run_operator
    run_operator(main)
