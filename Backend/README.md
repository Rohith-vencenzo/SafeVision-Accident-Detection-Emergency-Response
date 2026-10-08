# CrashPulse Backend

Local-only FastAPI backend for the CrashPulse college demonstration. The backend
uses PostgreSQL; it does **not** fall back to SQLite. It is intentionally kept in
its own Python environment and does not import or modify the detector package.

The existing detector is one directory above this project. The detector root,
interpreter, and entry point are configured explicitly in `.env` only when the
analysis-job phase is approved.

## Implemented scope

Implemented in this phase:

- FastAPI package with `/api/v1` routes and OpenAPI documentation
- PostgreSQL SQL migration for users, sessions, terms, devices, jobs, incidents,
  notification outbox, delivery attempts, and user actions
- Argon2id password hashing
- Short-lived JWT access tokens and revocable refresh sessions
- Local login rate limiting
- Secure first-admin bootstrap CLI (interactive, no default credentials)
- Admin-only real user creation/listing/disabling API
- Plain HTML/CSS/JavaScript local `/admin` page
- Terms version/acceptance audit endpoints
- Encrypted-at-rest FCM token storage hook and token registration endpoints
- Liveness/readiness endpoints
- Protected detector subprocess adapter and v1.0 result/evidence validation
- Bounded private video jobs, incidents, user actions, and notification outbox
- Fake sender plus opt-in Firebase Admin sender

See `API.md` for endpoint contracts and `RUNBOOK.md` for operations. Android is a
separate project under `College_Proj\CrashPulse`; its alarm/call/location behavior
is documented in that project's `TESTING.md`.

## Phase 3 local configuration

The Backend validates the protected detector paths at startup. Fill these
non-secret values in `.env` before starting the API (the paths below are the
current Windows workspace layout):

```text
DETECTOR_ROOT=C:\Users\sivap\Downloads\College_Proj\Accident_Detection
DETECTOR_PYTHON=C:\Users\sivap\Downloads\College_Proj\Accident_Detection\.venv\Scripts\python.exe
DETECTOR_ENTRYPOINT=C:\Users\sivap\Downloads\College_Proj\Accident_Detection\main.py
DETECTOR_CONFIG=C:\Users\sivap\Downloads\College_Proj\Accident_Detection\config.json
DETECTOR_MODEL=C:\Users\sivap\Downloads\College_Proj\Accident_Detection\models\yolov11.pt
```

The adapter invokes `main.py` as a child process with an argument list, validates
the existing `docs/result.schema.json`, checks source/checkpoint hashes, and
requires the detector's published bundle/evidence integrity. It never imports
detector internals or modifies the detector project.

Uploads are stored under private `PRIVATE_DATA_DIR/jobs/<job-id>`, are bounded by
`MAX_UPLOAD_BYTES`, validated by media type/container header, and deleted after
terminal analysis. The durable PostgreSQL queue is bounded by
`MAX_PENDING_JOBS` and `DETECTOR_MAX_CONCURRENT_JOBS`; run the API with one Uvicorn
worker because the local queue owner uses a PostgreSQL advisory lock.

FCM defaults to `FCM_MODE=disabled`. Phase 3 includes a no-network fake sender and
durable outbox, plus an opt-in Firebase Admin sender. Firebase CLI registration
does not provide server Admin credentials. To enable real sending later, supply
local ADC/service-account credentials according to Firebase's server-environment
instructions, keep them outside Git, set `FCM_MODE=firebase`, and authorize a
specific test device before running `scripts.dispatch_notifications`.

The registered Firebase Android app is in project `crashpulse-clg` with package
`com.collegeproj.crashpulse`; the Android project and `google-services.json` belong
to Phase 4 and were not created in this phase.

## Local setup

1. Create a backend-specific environment. Do not reuse the detector `.venv`:

   ```powershell
   py -3.13 -m venv .venv_backend
   .\.venv_backend\Scripts\Activate.ps1
   python -m pip install -e ".[dev]"
   ```

2. Copy `.env.example` to `.env` and manually replace every `CHANGE_ME` value.
   Keep `.env` out of Git. The database password must stay local and must not be
   pasted into chat or written into reports.

   PostgreSQL folds unquoted names to lowercase: `CREATE DATABASE CrashPulse;`
   creates `crashpulse`. Use `/crashpulse` in the URL unless the database was
   deliberately created with a quoted, case-sensitive name. Percent-encode
   password characters that have special meaning in URLs.

3. Generate a token-encryption key locally:

   ```powershell
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Put the generated value in `TOKEN_ENCRYPTION_KEY` in `.env`.

   A Fernet key is not an arbitrary password or JWT secret. Generate it using the
   command above and keep the exact generated value, including trailing `=`.
   Verify local settings without displaying credentials:

   ```powershell
   python -m scripts.check_config
   ```

4. Apply migrations after confirming the local PostgreSQL database exists:

   ```powershell
   python -m scripts.migrate
   ```

5. Seed a real terms version from a local file:

   ```powershell
   python -m scripts.seed_terms --version 1.0 --file .\local-terms.txt
   ```

6. Bootstrap the first administrator. The password is prompted without echo and
   is never printed or stored in plaintext:

   ```powershell
   python -m scripts.bootstrap_admin --login-id admin
   ```

7. Start the local server:

   ```powershell
   uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

   Open `http://127.0.0.1:8000/admin`. This page is for a local trusted operator
   only and must not be exposed to the public internet.

## Required environment

See `.env.example`. At minimum, `DATABASE_URL`, `JWT_SECRET`, and
`TOKEN_ENCRYPTION_KEY` must be set for authenticated operation. The database URL
must use PostgreSQL (`postgresql+psycopg://...`); SQLite URLs are rejected.

## Tests

```powershell
python -m pytest -q
```

The PostgreSQL integration tests run only when `TEST_DATABASE_URL` is explicitly
provided in the process environment or local `.env`. Each test creates a random
`test_crashpulse_*` schema and drops it in cleanup; the configured role must have
permission to create schemas. Application tables outside those test schemas are
not modified by the tests. They never substitute SQLite. If PostgreSQL credentials are not
available, the test output reports the integration tests as skipped and the
pure unit/API contract tests still run.

## Security notes

- There is no public registration or password-reset endpoint.
- User passwords are hashed with Argon2id.
- Refresh sessions are stored as SHA-256 hashes and can be revoked.
- FCM tokens are encrypted before persistence; the encryption key is local config.
- Logs and API responses must not contain passwords, tokens, database URLs, or
  precise location data.
- This is a local demonstration backend. A later deployment requires TLS,
  hardened secret management, reverse-proxy controls, and operational monitoring.
