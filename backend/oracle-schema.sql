-- Generated for Oracle 19c+. Run as the project application schema, once per DB.


CREATE TABLE dataset_heads (
	dataset_key VARCHAR2(150 CHAR) NOT NULL, 
	version_id VARCHAR2(64 CHAR) NOT NULL, 
	row_count INTEGER NOT NULL, 
	updated_at VARCHAR2(32 CHAR) NOT NULL, 
	PRIMARY KEY (dataset_key)
)

;


CREATE TABLE dataset_versions (
	dataset_key VARCHAR2(150 CHAR) NOT NULL, 
	version_id VARCHAR2(64 CHAR) NOT NULL, 
	source_sha256 VARCHAR2(64 CHAR) NOT NULL, 
	status VARCHAR2(16 CHAR) NOT NULL, 
	row_count INTEGER NOT NULL, 
	format_name VARCHAR2(100 CHAR) NOT NULL, 
	created_at VARCHAR2(32 CHAR) NOT NULL, 
	PRIMARY KEY (dataset_key, version_id)
)

;


CREATE TABLE observations (
	dataset_key VARCHAR2(150 CHAR) NOT NULL, 
	version_id VARCHAR2(64 CHAR) NOT NULL, 
	row_no INTEGER NOT NULL, 
	entity_key VARCHAR2(200 CHAR), 
	observed_day VARCHAR2(10 CHAR), 
	region_code VARCHAR2(20 CHAR), 
	payload CLOB NOT NULL, 
	PRIMARY KEY (dataset_key, version_id, row_no)
)

;

CREATE INDEX obs_entity_day ON observations (dataset_key, version_id, entity_key, observed_day, row_no);

CREATE INDEX obs_region_day ON observations (dataset_key, version_id, region_code, observed_day, row_no);


CREATE TABLE record_revisions (
	kind VARCHAR2(32 CHAR) NOT NULL, 
	record_key VARCHAR2(200 CHAR) NOT NULL, 
	version INTEGER NOT NULL, 
	payload CLOB NOT NULL, 
	sha256 VARCHAR2(64 CHAR) NOT NULL, 
	observed_at VARCHAR2(32 CHAR) NOT NULL, 
	created_at VARCHAR2(32 CHAR) NOT NULL, 
	PRIMARY KEY (kind, record_key, version)
)

;


CREATE TABLE research_jobs (
	job_id VARCHAR2(64 CHAR) NOT NULL, 
	job_type VARCHAR2(64 CHAR) NOT NULL, 
	status VARCHAR2(16 CHAR) NOT NULL, 
	payload CLOB NOT NULL, 
	result_payload CLOB, 
	lease_token VARCHAR2(32 CHAR), 
	lease_until VARCHAR2(32 CHAR), 
	worker_id VARCHAR2(100 CHAR), 
	attempts INTEGER NOT NULL, 
	max_attempts INTEGER NOT NULL, 
	created_at VARCHAR2(32 CHAR) NOT NULL, 
	updated_at VARCHAR2(32 CHAR) NOT NULL, 
	PRIMARY KEY (job_id)
)

;

CREATE INDEX jobs_status_lease ON research_jobs (job_type, status, lease_until);


CREATE TABLE research_records (
	kind VARCHAR2(32 CHAR) NOT NULL, 
	record_key VARCHAR2(200 CHAR) NOT NULL, 
	version INTEGER NOT NULL, 
	summary VARCHAR2(2000 CHAR) NOT NULL, 
	payload CLOB NOT NULL, 
	sha256 VARCHAR2(64 CHAR) NOT NULL, 
	source_uri CLOB, 
	observed_at VARCHAR2(32 CHAR) NOT NULL, 
	created_at VARCHAR2(32 CHAR) NOT NULL, 
	updated_at VARCHAR2(32 CHAR) NOT NULL, 
	PRIMARY KEY (kind, record_key)
)

;


CREATE TABLE snapshot_files (
	snapshot_id VARCHAR2(32 CHAR) NOT NULL, 
	entry_id VARCHAR2(64 CHAR) NOT NULL, 
	relative_path VARCHAR2(1000 CHAR) NOT NULL, 
	sha256 VARCHAR2(64 CHAR) NOT NULL, 
	byte_size INTEGER NOT NULL, 
	PRIMARY KEY (snapshot_id, entry_id)
)

;


CREATE TABLE snapshot_heads (
	name VARCHAR2(100 CHAR) NOT NULL, 
	snapshot_id VARCHAR2(32 CHAR) NOT NULL, 
	updated_at VARCHAR2(32 CHAR) NOT NULL, 
	PRIMARY KEY (name)
)

;


CREATE TABLE snapshots (
	snapshot_id VARCHAR2(32 CHAR) NOT NULL, 
	name VARCHAR2(100 CHAR) NOT NULL, 
	previous_id VARCHAR2(32 CHAR), 
	status VARCHAR2(16 CHAR) NOT NULL, 
	file_count INTEGER NOT NULL, 
	created_at VARCHAR2(32 CHAR) NOT NULL, 
	PRIMARY KEY (snapshot_id)
)

;


CREATE TABLE stored_files (
	sha256 VARCHAR2(64 CHAR) NOT NULL, 
	byte_size INTEGER NOT NULL, 
	store_name VARCHAR2(16 CHAR) NOT NULL, 
	created_at VARCHAR2(32 CHAR) NOT NULL, 
	PRIMARY KEY (sha256)
)

;