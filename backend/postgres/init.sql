CREATE TABLE IF NOT EXISTS links (
    id           BIGSERIAL PRIMARY KEY,
    code         TEXT UNIQUE NOT NULL,
    original_url TEXT NOT NULL,
    clicks       INTEGER NOT NULL DEFAULT 0,
    expires_at   TIMESTAMPTZ NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS links_code_idx ON links (code);
