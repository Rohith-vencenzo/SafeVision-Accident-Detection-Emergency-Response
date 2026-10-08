from collections.abc import Callable

from sqlalchemy.exc import SQLAlchemyError


def run_operator(command: Callable[[], None]) -> None:
    try:
        command()
    except SQLAlchemyError as error:
        code = getattr(error.orig, "sqlstate", None)
        detail = f" (SQLSTATE {code})" if code else ""
        raise SystemExit(f"Database operation failed{detail}. Check local PostgreSQL access and migrations; no credentials have been printed.") from None
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
