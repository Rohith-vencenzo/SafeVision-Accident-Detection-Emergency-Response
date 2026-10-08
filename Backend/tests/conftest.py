import os

from cryptography.fernet import Fernet


# Safe, test-only values are set before any app module is imported. They are not
# usable production credentials and never describe a real database account.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@127.0.0.1:5432/CrashPulse")
os.environ.setdefault("JWT_SECRET", "unit-test-only-secret-" + "a" * 40)
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
