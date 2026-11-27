-- migrate:up

-- A read-only role for people debugging data (team members get it through db_people in
-- infra/terraform/envs/staging/variables.tf, database_roles = ["riffle_readonly"]). It reads
-- every table but is still bound by row-level security: set the company first,
--   SET app.tenant_id = '<installation id>';
-- and only that company's rows are visible. It can never write.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'riffle_readonly') THEN
    CREATE ROLE riffle_readonly NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO riffle_readonly;
GRANT EXECUTE ON FUNCTION riffle_current_tenant() TO riffle_readonly;
GRANT SELECT ON tenants, repos, score_results TO riffle_readonly;
-- Tables added by later migrations (all run by the migration Job's user) are readable too.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO riffle_readonly;

-- migrate:down

ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM riffle_readonly;
REVOKE SELECT ON tenants, repos, score_results FROM riffle_readonly;
REVOKE EXECUTE ON FUNCTION riffle_current_tenant() FROM riffle_readonly;
REVOKE USAGE ON SCHEMA public FROM riffle_readonly;
