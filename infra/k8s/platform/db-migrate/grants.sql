-- Database roles of the services, granted after the migrations have run (riffle_app is created
-- by the first migration, so it cannot be granted before). Re-running is safe: granting a role a
-- user already has only prints a notice.
--
-- Users are the Google service accounts of infra/terraform/envs/staging/09-identity.tf with
-- database = true, without ".gserviceaccount.com". Keep the two lists in step.

-- Services bound by row-level security (ADR 0025): riffle_app only, never cloudsqlsuperuser.
GRANT riffle_app TO "riffle-staging-scorer@riffle-infra-platform.iam";
GRANT riffle_app TO "riffle-staging-app-github@riffle-infra-platform.iam";
GRANT riffle_app TO "riffle-staging-app-dashboard@riffle-infra-platform.iam";
GRANT riffle_app TO "riffle-staging-outcome-labeler@riffle-infra-platform.iam";
GRANT riffle_app TO "riffle-staging-tenant-workflow@riffle-infra-platform.iam";
GRANT riffle_app TO "riffle-staging-scorelog-writer@riffle-infra-platform.iam";
GRANT riffle_app TO "riffle-staging-retrain@riffle-infra-platform.iam";
GRANT riffle_app TO "riffle-staging-trainer@riffle-infra-platform.iam";

-- MLflow keeps the model registry in its own database and creates its own tables there.
\connect mlflow
GRANT ALL ON DATABASE mlflow TO "riffle-staging-mlflow@riffle-infra-platform.iam";
GRANT ALL ON SCHEMA public TO "riffle-staging-mlflow@riffle-infra-platform.iam";
