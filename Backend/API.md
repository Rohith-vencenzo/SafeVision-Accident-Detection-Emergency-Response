# CrashPulse Backend API handoff

The API is local-first and versioned under `/api/v1`. It requires PostgreSQL and
does not fall back to SQLite. The OpenAPI document is available at `/openapi.json`
when the FastAPI server is running; Swagger UI is `/docs`.

## Authentication

```text
POST /api/v1/auth/login
Body: {"login_id":"...","password":"..."}
Returns: short-lived access token and refresh token

POST /api/v1/auth/refresh
Body: {"refresh_token":"..."}

POST /api/v1/auth/logout
Body: {"refresh_token":"..."}

GET /api/v1/auth/me
Authorization: Bearer <access token>
```

There is no public registration or password-reset route. Mobile accounts are
created by an authenticated administrator through the loopback-only `/admin` page
or the admin API.

## Terms gate

```text
GET  /api/v1/terms/current
GET  /api/v1/terms/acceptance
POST /api/v1/terms/acceptance
Body: {"version":"1.0","content_sha256":"<64 hex>"}
```

The current version/hash must be accepted before user-owned jobs, incidents,
devices, or actions can be used.

## Devices

```text
POST   /api/v1/devices/fcm-token
Body: {"token":"<FCM token>","platform":"ANDROID"}
PATCH  /api/v1/devices/fcm-token/{device_id}
DELETE /api/v1/devices/fcm-token/{device_id}
```

FCM tokens are encrypted before database persistence and never returned in API
responses. Token registration is scoped to the authenticated user.

## Video jobs

```text
POST /api/v1/videos/analyze
Authorization: Bearer <access token>
Header: Idempotency-Key: <stable client key>
Multipart field: video
Returns: 202 with job ID

GET  /api/v1/jobs/{job_id}
GET  /api/v1/jobs/{job_id}/result
POST /api/v1/jobs/{job_id}/cancel
```

Uploads are size/type/header checked, privately stored, and processed through the
existing detector CLI. The job worker validates the existing v1.0 result schema,
source/checkpoint hashes, published bundle, and evidence hashes before incident
persistence. Job IDs and idempotency keys are user-scoped.

## Incidents and actions

```text
GET  /api/v1/incidents?limit=50&offset=0
GET  /api/v1/incidents/{incident_id}
GET  /api/v1/incidents/{incident_id}/result
GET  /api/v1/incidents/{incident_id}/report
GET  /api/v1/incidents/{incident_id}/evidence/{before.jpg|strongest.jpg|after.jpg}

POST /api/v1/incidents/{incident_id}/actions
Header: Idempotency-Key: <stable action key>
Body: {"action":"IGNORED|MARKED_REVIEWED|PROCEEDED", "call_method":null|"DIALER"|"CALL_NOW", "location_consent":false}

POST /api/v1/incidents/{incident_id}/location
Header: Idempotency-Key: <stable location key>
Body: {"latitude":0,"longitude":0,"accuracy_meters":10,"captured_at":"...Z","consent":true}
```

Location is accepted only after an explicit `PROCEEDED` action, is one-time
foreground phone location, and is labeled/stored as phone current location. It is
not camera/crash location.

## Health

```text
GET /health/live
GET /health/ready
```

Readiness checks PostgreSQL, authentication configuration, migration checksums,
storage configuration, and explicit detector paths during application startup.

## Error policy

- `401`: missing, expired, revoked, or invalid session
- `403`: terms not accepted, wrong role, or unauthorized device
- `409`: reused idempotency key with different data or duplicate resource
- `413`: upload exceeds configured limit
- `415`: unsupported video type/container
- `429`: login or queue rate/size limit
- `503`: database/configuration/provider unavailable

Responses and logs do not include passwords, JWTs, FCM tokens, database URLs,
service-account material, video bytes, or precise location unless explicitly
authorized through the protected location endpoint.
