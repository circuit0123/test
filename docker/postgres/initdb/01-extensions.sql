-- Runs once on first start (empty data volume), as the superuser.
-- Enables the extensions in the main database and creates a separate test
-- database so the test suite never touches development data.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE DATABASE circuit_test;
\connect circuit_test
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
