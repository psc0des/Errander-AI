-- Errander-AI PostgreSQL role setup
--
-- Run once as a superuser (e.g. postgres) before starting the agent.
-- Safe to re-run (uses IF NOT EXISTS / DO $$ guards).
--
-- Two roles:
--   errander_agent  full DML on all tables (used by the agent process)
--   errander_web    read-only + limited UPDATE on approval/settings tables
--                   explicit DENY on audit/AI-decision tables via no INSERT grant

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'errander_agent') THEN
    CREATE ROLE errander_agent WITH LOGIN PASSWORD 'CHANGE_ME_agent';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'errander_web') THEN
    CREATE ROLE errander_web   WITH LOGIN PASSWORD 'CHANGE_ME_web';
  END IF;
END
$$;

-- Schema usage
GRANT USAGE ON SCHEMA public TO errander_agent;
GRANT USAGE ON SCHEMA public TO errander_web;

-- errander_agent: full DML on all existing tables + sequences
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA public TO errander_agent;
GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA public TO errander_agent;

-- errander_web: SELECT everywhere
GRANT SELECT ON ALL TABLES IN SCHEMA public TO errander_web;

-- errander_web: targeted UPDATE on approval-flow and settings tables only
GRANT UPDATE ON approval_requests    TO errander_web;
GRANT UPDATE ON settings_overrides   TO errander_web;
GRANT UPDATE ON inventory_overrides  TO errander_web;

-- errander_web: explicitly NO write on audit/AI tables
-- (no INSERT/UPDATE/DELETE granted; SELECT-only from the GRANT above)
-- Listed here for documentation clarity, not functional effect:
--   audit_events, ai_decisions, ai_eval_runs, ai_eval_results  => SELECT only

-- DEFAULT PRIVILEGES: future tables created by the migration user inherit these grants.
-- Run this as the role that will CREATE tables (e.g. postgres or a migration role).
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES    TO errander_agent;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT                  ON SEQUENCES TO errander_agent;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT                         ON TABLES    TO errander_web;
