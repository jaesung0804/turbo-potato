-- Run interactively as ADMIN in SQLcl/SQL*Plus for EACH database.
-- This script creates a new application schema. Do not reuse ADMIN in the API.
-- Choose ESTATE_APP on ESTATE; INVESTMENT_APP on INVESTMENT.
SET VERIFY OFF
SET ECHO OFF
ACCEPT app_user CHAR PROMPT 'Application user (ESTATE_APP or INVESTMENT_APP): '
ACCEPT app_password CHAR PROMPT 'New application password (no double quote or ampersand): ' HIDE
CREATE USER &&app_user IDENTIFIED BY "&&app_password";
GRANT CREATE SESSION, CREATE TABLE TO &&app_user;
ALTER USER &&app_user QUOTA 15G ON DATA;
UNDEFINE app_password
UNDEFINE app_user
