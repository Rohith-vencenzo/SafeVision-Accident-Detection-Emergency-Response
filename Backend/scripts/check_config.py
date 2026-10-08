"""Check local configuration without emitting credentials or driver messages."""
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db import get_engine
from scripts.common import run_operator


def main() -> None:
    settings = get_settings()
    settings.validate_database_url()
    auth_valid = True
    try:
        settings.validate_auth_secrets()
        print("authentication settings valid")
    except RuntimeError as error:
        auth_valid = False
        print(str(error))
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        code = getattr(error.orig, "sqlstate", None)
        categories = {
            "3D000": "configured database does not exist (check spelling/case)",
            "28P01": "database authentication failed",
            "28000": "database authorization failed",
            "42501": "database permission denied",
        }
        # libpq connection failures sometimes omit SQLSTATE. Classify locally
        # without emitting the original message, which may contain connection data.
        message = str(error.orig).lower()
        category = categories.get(code, "connection unavailable")
        if code is None:
            if "database" in message and "does not exist" in message:
                category = categories["3D000"]
            elif "password authentication failed" in message:
                category = categories["28P01"]
            elif "no password supplied" in message:
                category = "database password missing from local configuration"
        print("database connection failed: " + category)
        print("SQLSTATE: " + (code or "not supplied by driver"))
        if category == categories["3D000"]:
            maintenance = create_engine(make_url(settings.database_url).set(database="postgres"), hide_parameters=True, connect_args={"connect_timeout": 5})
            try:
                with maintenance.connect() as connection:
                    exists = connection.execute(text("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'crashpulse')")).scalar_one()
                    if exists:
                        print("Existing local project database found: crashpulse (lowercase)")
            except SQLAlchemyError:
                print("Could not verify lowercase project database using read-only discovery")
            finally:
                maintenance.dispose()
        raise SystemExit(1) from None
    print("PostgreSQL connection passed")
    if not auth_valid:
        raise SystemExit(1)


if __name__ == "__main__":
    run_operator(main)
