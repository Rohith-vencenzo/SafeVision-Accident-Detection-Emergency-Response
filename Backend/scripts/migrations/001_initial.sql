CREATE TABLE IF NOT EXISTS users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    login_id varchar(100) NOT NULL UNIQUE,
    password_hash varchar(255) NOT NULL,
    role varchar(20) NOT NULL DEFAULT 'USER' CHECK (role IN ('ADMIN', 'USER')),
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS refresh_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash char(64) NOT NULL UNIQUE,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS terms_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version varchar(50) NOT NULL UNIQUE,
    content text NOT NULL,
    content_sha256 char(64) NOT NULL,
    is_current boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_one_current_terms ON terms_versions (is_current) WHERE is_current;

CREATE FUNCTION protect_terms_content() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.version <> OLD.version OR NEW.content <> OLD.content OR NEW.content_sha256 <> OLD.content_sha256 THEN
        RAISE EXCEPTION 'Published terms content is immutable; create a new version';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER immutable_terms BEFORE UPDATE ON terms_versions FOR EACH ROW EXECUTE FUNCTION protect_terms_content();

CREATE TABLE IF NOT EXISTS terms_acceptances (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    terms_version_id uuid NOT NULL REFERENCES terms_versions(id) ON DELETE RESTRICT,
    content_sha256 char(64) NOT NULL,
    accepted_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_terms_user_version UNIQUE (user_id, terms_version_id)
);

CREATE TABLE IF NOT EXISTS devices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash char(64) NOT NULL UNIQUE,
    token_ciphertext text NOT NULL,
    platform varchar(20) NOT NULL DEFAULT 'ANDROID',
    is_active boolean NOT NULL DEFAULT true,
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_device_user_token UNIQUE (user_id, token_hash)
);

CREATE TABLE IF NOT EXISTS analysis_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    status varchar(30) NOT NULL DEFAULT 'QUEUED',
    idempotency_key varchar(200) UNIQUE,
    input_reference text,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS incidents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid REFERENCES users(id) ON DELETE SET NULL,
    job_id uuid REFERENCES analysis_jobs(id) ON DELETE SET NULL,
    detector_run_id varchar(200) UNIQUE,
    status varchar(30) NOT NULL DEFAULT 'UNREAD',
    safe_summary text NOT NULL,
    result_reference text,
    evidence_reference text,
    occurred_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notification_outbox (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id uuid NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    device_id uuid NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    status varchar(20) NOT NULL DEFAULT 'PENDING',
    attempts integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_outbox_incident_device UNIQUE (incident_id, device_id)
);

CREATE TABLE IF NOT EXISTS notification_attempts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    outbox_id uuid NOT NULL REFERENCES notification_outbox(id) ON DELETE CASCADE,
    status varchar(20) NOT NULL,
    provider_message_id varchar(255),
    error_code varchar(100),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_actions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    incident_id uuid NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    action varchar(40) NOT NULL,
    call_method varchar(40),
    location_consent boolean NOT NULL DEFAULT false,
    location_result_reference text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_refresh_sessions_user_id ON refresh_sessions (user_id);
CREATE INDEX IF NOT EXISTS ix_terms_acceptances_user_id ON terms_acceptances (user_id);
CREATE INDEX IF NOT EXISTS ix_devices_user_id ON devices (user_id);
CREATE INDEX IF NOT EXISTS ix_analysis_jobs_owner_id ON analysis_jobs (owner_id);
CREATE INDEX IF NOT EXISTS ix_analysis_jobs_status ON analysis_jobs (status);
CREATE INDEX IF NOT EXISTS ix_incidents_owner_id ON incidents (owner_id);
CREATE INDEX IF NOT EXISTS ix_incidents_status ON incidents (status);
CREATE INDEX IF NOT EXISTS ix_user_actions_user_id ON user_actions (user_id);
CREATE INDEX IF NOT EXISTS ix_user_actions_incident_id ON user_actions (incident_id);
