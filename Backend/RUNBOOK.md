# CrashPulse local Backend runbook

## First-time setup

Run from:

```powershell
Set-Location "C:\Users\sivap\Downloads\College_Proj\Accident_Detection\Backend"
py -3.13 -m venv .venv_backend
.\.venv_backend\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Edit `.env` locally. Never paste it into chat or commit it. Required values are:

```text
DATABASE_URL=postgresql+psycopg://<role>:<password>@127.0.0.1:5432/crashpulse
JWT_SECRET=<long random local value>
TOKEN_ENCRYPTION_KEY=<Fernet.generate_key() value>
DETECTOR_ROOT=C:\Users\sivap\Downloads\College_Proj\Accident_Detection
DETECTOR_PYTHON=C:\Users\sivap\Downloads\College_Proj\Accident_Detection\.venv\Scripts\python.exe
DETECTOR_ENTRYPOINT=C:\Users\sivap\Downloads\College_Proj\Accident_Detection\main.py
DETECTOR_CONFIG=C:\Users\sivap\Downloads\College_Proj\Accident_Detection\config.json
DETECTOR_MODEL=C:\Users\sivap\Downloads\College_Proj\Accident_Detection\models\yolov11.pt
```

Use `scripts.check_config` to validate without printing secrets:

```powershell
.\.venv_backend\Scripts\python.exe -m scripts.check_config
```

## Database and initial operator

```powershell
.\.venv_backend\Scripts\python.exe -m scripts.migrate
.\.venv_backend\Scripts\python.exe -m scripts.seed_terms --version 1.0 --file .\local-terms.txt
.\.venv_backend\Scripts\python.exe -m scripts.bootstrap_admin --login-id <operator-id>
```

The bootstrap command is interactive and refuses non-interactive execution. It
does not create a default password. Create mobile accounts from the local page:

```text
http://127.0.0.1:8000/admin
```

## Start and stop

Start one worker owner only; the PostgreSQL advisory lock protects the detector
queue from duplicate workers:

```powershell
.\.venv_backend\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Stop with `Ctrl+C`. Check readiness at `http://127.0.0.1:8000/health/ready`.

## FCM modes

Default:

```text
FCM_MODE=disabled
```

Use the fake sender for local outbox demonstrations:

```powershell
.\.venv_backend\Scripts\python.exe -m scripts.dispatch_fake
```

Real FCM requires an authorized local ADC/service-account credential and an
explicitly authorized test device. Configure the local credential path without
printing it, set `FCM_MODE=firebase`, and run:

```powershell
.\.venv_backend\Scripts\python.exe -m scripts.dispatch_notifications
```

Provider acceptance is not guaranteed device delivery. Do not run this against a
real device/account without explicit test authorization.

## Backup and retention

- Back up PostgreSQL with an operator-approved `pg_dump` policy; do not include
  passwords in command history or reports.
- Private job artifacts are under `PRIVATE_DATA_DIR/jobs` and are not public files.
- Run terminal-artifact retention manually:

  ```powershell
  .\.venv_backend\Scripts\python.exe -m scripts.cleanup_private
  ```

- Retention checks terminal job state, age, symlinks, and the configured private
  root. Review retention policy before deleting the PostgreSQL rows or backups.

## LAN operation

The backend defaults to loopback. For a trusted local demonstration, bind it to a
specific LAN interface only after applying a Windows firewall rule limited to the
trusted private network:

```powershell
.\.venv_backend\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Enter `http://<Windows-LAN-IP>:8000/api/v1/` in the Android Settings screen. This
is debug-only local HTTP. Do not expose the service to the public internet; a
later deployment requires TLS, reverse-proxy authentication controls, secret
management, and monitoring.

## Troubleshooting

| Symptom | Check |
|---|---|
| `DATABASE_URL is required` | Copy `.env.example` to `.env` and fill local values |
| invalid Fernet key | Generate with `Fernet.generate_key()`; preserve the trailing `=` |
| database does not exist | PostgreSQL folds unquoted `CrashPulse` to `crashpulse` |
| readiness is 503 | Run `scripts.check_config` and `scripts.migrate`; inspect only local logs |
| detector startup fails | Verify all five explicit detector paths and the protected Windows `.venv` |
| Android cannot connect | Use Windows LAN IP, trusted Wi-Fi, firewall rule, and debug build |
| no push | Keep FCM disabled until credentials, device token, notification permission, and network are verified |
| no full-screen alarm | Check Android notification permission, channel settings, DND, and full-screen special access |
