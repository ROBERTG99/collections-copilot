-- Local Postgres bootstrap (docker-entrypoint-initdb.d). Synthetic, dev-only credentials.
CREATE SCHEMA IF NOT EXISTS checkpoints AUTHORIZATION cc;
CREATE SCHEMA IF NOT EXISTS ledger AUTHORIZATION cc;
CREATE SCHEMA IF NOT EXISTS mcpgw AUTHORIZATION cc;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cc_readonly') THEN
    CREATE ROLE cc_readonly LOGIN PASSWORD 'cc_readonly';
  END IF;
END $$;
GRANT CONNECT ON DATABASE cc TO cc_readonly;
GRANT USAGE ON SCHEMA public TO cc_readonly;
ALTER DEFAULT PRIVILEGES FOR ROLE cc IN SCHEMA public GRANT SELECT ON TABLES TO cc_readonly;
