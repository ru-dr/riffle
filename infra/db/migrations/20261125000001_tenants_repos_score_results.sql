-- migrate:up

-- The role every service connects through (ADR 0025). It cannot log in, cannot bypass
-- row-level security and owns nothing; each environment grants it to its own login (the
-- local stack's init script, Terraform on GCP). Created here only if the environment has
-- not already made it, because roles belong to the whole server, not to one database.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'riffle_app') THEN
    CREATE ROLE riffle_app NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
END
$$;

-- The tenant a connection works for, set by the service per connection or transaction:
--   SET app.tenant_id = '<installation id>'
-- Unset or empty gives NULL, and NULL matches no row, so a service that forgets to set it
-- sees nothing and can write nothing (it fails closed, never open).
CREATE FUNCTION riffle_current_tenant() RETURNS text
  LANGUAGE sql STABLE
  AS $$ SELECT NULLIF(current_setting('app.tenant_id', true), '') $$;

-- One row per GitHub App installation. tenant_id is the installation id, as in PrEvent.
CREATE TABLE tenants (
  tenant_id     text PRIMARY KEY,
  account_login text NOT NULL,
  account_type  text NOT NULL CHECK (account_type IN ('Organization', 'User')),
  status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'deleting')),
  installed_at  timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- The repositories an installation gave Riffle access to.
CREATE TABLE repos (
  tenant_id      text NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
  repo_id        bigint NOT NULL,
  full_name      text NOT NULL,
  default_branch text,
  added_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, repo_id),
  UNIQUE (tenant_id, full_name)
);

-- One row per score the scorer writes. Append-only: a rescore is a new delivery and a new
-- row, never an update. delivery_id is the key, so scoring is idempotent per delivery.
-- A PR is either ranked (score, band and model_version all set) or unranked with a
-- reason (fail open). Null means "not ranked", never 0.
CREATE TABLE score_results (
  delivery_id     text PRIMARY KEY,
  tenant_id       text NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
  repo            text NOT NULL,
  pr_number       integer NOT NULL CHECK (pr_number > 0),
  head_sha        text NOT NULL,
  risk_score      double precision CHECK (risk_score BETWEEN 0 AND 1),
  rank_band       text CHECK (rank_band IN ('review_first', 'standard', 'senior_recommended')),
  model_version   text,
  layer_weight    double precision CHECK (layer_weight BETWEEN 0 AND 1),
  features        jsonb,
  explanation     text,
  unranked_reason text,
  scored_at       timestamptz NOT NULL,
  CONSTRAINT ranked_or_unranked_with_reason CHECK (
    (risk_score IS NOT NULL AND rank_band IS NOT NULL AND model_version IS NOT NULL AND unranked_reason IS NULL)
    OR (risk_score IS NULL AND rank_band IS NULL AND unranked_reason IS NOT NULL)
  )
);

-- The dashboard and the app read the latest score of each PR.
CREATE INDEX score_results_latest_per_pr ON score_results (tenant_id, repo, pr_number, scored_at DESC);

-- Row-level security on every table. FORCE applies it to the table owner too, so only a
-- real superuser (never a service) can read across tenants.
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenants
  USING (tenant_id = riffle_current_tenant()) WITH CHECK (tenant_id = riffle_current_tenant());

ALTER TABLE repos ENABLE ROW LEVEL SECURITY;
ALTER TABLE repos FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON repos
  USING (tenant_id = riffle_current_tenant()) WITH CHECK (tenant_id = riffle_current_tenant());

ALTER TABLE score_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE score_results FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON score_results
  USING (tenant_id = riffle_current_tenant()) WITH CHECK (tenant_id = riffle_current_tenant());

GRANT USAGE ON SCHEMA public TO riffle_app;
GRANT EXECUTE ON FUNCTION riffle_current_tenant() TO riffle_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON tenants, repos TO riffle_app;
GRANT SELECT, INSERT ON score_results TO riffle_app;

-- migrate:down

DROP TABLE score_results;
DROP TABLE repos;
DROP TABLE tenants;
DROP FUNCTION riffle_current_tenant();
REVOKE USAGE ON SCHEMA public FROM riffle_app;
