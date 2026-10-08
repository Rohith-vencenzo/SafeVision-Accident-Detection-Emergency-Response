from pathlib import Path
import hashlib

from sqlalchemy import text

from app.db import get_engine


def main() -> None:
    migration_dir = Path(__file__).parent / "migrations"
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(73682001)"))
        connection.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (version varchar(100) PRIMARY KEY, checksum char(64) NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"))
        applied = dict(connection.execute(text("SELECT version, checksum FROM schema_migrations")).all())
        for path in sorted(migration_dir.glob("*.sql")):
            content = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if path.name in applied:
                if applied[path.name] != checksum:
                    raise RuntimeError("applied migration checksum mismatch; do not edit applied migrations")
                continue
            connection.exec_driver_sql(content)
            connection.execute(text("INSERT INTO schema_migrations (version, checksum) VALUES (:version, :checksum)"), {"version": path.name, "checksum": checksum})
            print(f"applied {path.name}")


if __name__ == "__main__":
    from scripts.common import run_operator
    run_operator(main)
