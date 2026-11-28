-- migrate:up

-- Each tenant's data key (ADR 0025), wrapped by the key-encryption key in KMS (tenant-kek); the
-- plain key never touches the database. It encrypts the tenant's files on object storage, first the
-- score log (ADR 0024). One key per tenant, made once: the first writer to need it inserts it, and
-- ON CONFLICT keeps the first. Deleting the tenant deletes its key (cascade), which makes every copy
-- of its files unreadable (ADR 0028).
CREATE TABLE tenant_keys (
  tenant_id   text PRIMARY KEY REFERENCES tenants (tenant_id) ON DELETE CASCADE,
  wrapped_key bytea NOT NULL,    -- the 32-byte AES key, encrypted by kek_name with the tenant id as context
  kek_name    text NOT NULL,     -- the KMS key that wrapped it (projects/.../cryptoKeys/tenant-kek)
  created_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE tenant_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenant_keys
  USING (tenant_id = riffle_current_tenant()) WITH CHECK (tenant_id = riffle_current_tenant());

-- Made and read by services; never changed (a new key would orphan the files of the old one).
-- Removed only with the tenant (the cascade above).
GRANT SELECT, INSERT ON tenant_keys TO riffle_app;
-- People read every table through riffle_readonly's default privileges, but have no use for
-- wrapped keys.
REVOKE ALL ON tenant_keys FROM riffle_readonly;

-- migrate:down

DROP TABLE tenant_keys;
