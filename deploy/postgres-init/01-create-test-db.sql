-- Runs once on first container start (docker-entrypoint-initdb.d).
-- Creates the test database used by the pytest suite.
CREATE DATABASE errander_test OWNER errander;
