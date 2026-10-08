import argparse
import hashlib
from pathlib import Path

from sqlalchemy import text

from app.db import get_engine


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a real terms version from a local text file")
    parser.add_argument("--version", required=True)
    parser.add_argument("--file", required=True, type=Path)
    args = parser.parse_args()
    content = args.file.read_text(encoding="utf-8").strip()
    if not content:
        raise SystemExit("terms file is empty")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if not 1 <= len(args.version) <= 50:
        raise SystemExit("version must contain 1–50 characters")
    with get_engine().begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(73682003)"))
        existing = connection.execute(text("SELECT content_sha256 FROM terms_versions WHERE version = :version"), {"version": args.version}).scalar_one_or_none()
        if existing is not None and existing != digest:
            raise SystemExit("published terms cannot change; supply a new version")
        connection.execute(text("UPDATE terms_versions SET is_current = false WHERE is_current"))
        connection.execute(text("""
            INSERT INTO terms_versions (version, content, content_sha256, is_current)
            VALUES (:version, :content, :digest, true)
            ON CONFLICT (version) DO UPDATE SET is_current = true
        """), {"version": args.version, "content": content, "digest": digest})
    print(f"seeded terms {args.version} ({digest})")


if __name__ == "__main__":
    from scripts.common import run_operator
    run_operator(main)
