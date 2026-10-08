ALTER TABLE analysis_jobs ADD COLUMN IF NOT EXISTS result_reference text;
ALTER TABLE analysis_jobs ADD COLUMN IF NOT EXISTS incident_id uuid;
ALTER TABLE analysis_jobs ADD COLUMN IF NOT EXISTS completed_at timestamptz;
ALTER TABLE user_actions ADD COLUMN IF NOT EXISTS idempotency_key varchar(200);

UPDATE user_actions
SET idempotency_key = 'legacy-' || id::text
WHERE idempotency_key IS NULL;

ALTER TABLE user_actions ALTER COLUMN idempotency_key SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_action_idempotency ON user_actions (user_id, idempotency_key);
CREATE INDEX IF NOT EXISTS ix_analysis_jobs_incident_id ON analysis_jobs (incident_id);
