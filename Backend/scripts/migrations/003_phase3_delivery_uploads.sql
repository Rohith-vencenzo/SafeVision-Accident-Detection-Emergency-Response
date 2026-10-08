ALTER TABLE analysis_jobs DROP CONSTRAINT IF EXISTS analysis_jobs_idempotency_key_key;
ALTER TABLE analysis_jobs ADD CONSTRAINT uq_job_owner_key UNIQUE (owner_id, idempotency_key);
ALTER TABLE analysis_jobs ADD COLUMN input_sha256 varchar(64);
ALTER TABLE analysis_jobs ADD COLUMN decision varchar(40);
ALTER TABLE notification_outbox ADD COLUMN next_attempt_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE user_actions ADD COLUMN location_data jsonb;
