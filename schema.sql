-- PostgreSQL DDL compiled from the product models. Alembic owns migration history.

CREATE TABLE users (
	id VARCHAR(36) NOT NULL, 
	platform VARCHAR(20) NOT NULL, 
	workspace_id VARCHAR(64) NOT NULL, 
	external_user_id VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	display_name VARCHAR(80), 
	CONSTRAINT pk_users PRIMARY KEY (id), 
	CONSTRAINT uq_user_external UNIQUE (platform, workspace_id, external_user_id), 
	CONSTRAINT uq_user_scope UNIQUE (id, platform, workspace_id)
);

CREATE TABLE daily_digests (
	id VARCHAR(36) NOT NULL, 
	platform VARCHAR(20) NOT NULL, 
	workspace_id VARCHAR(64) NOT NULL, 
	channel_id VARCHAR(64) NOT NULL, 
	digest_date DATE NOT NULL, 
	timezone_name VARCHAR(64) NOT NULL, 
	cutoff_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	lease_token VARCHAR(36), 
	lease_until TIMESTAMP WITH TIME ZONE, 
	attempts INTEGER NOT NULL, 
	error_code VARCHAR(80), 
	next_attempt_at TIMESTAMP WITH TIME ZONE, 
	input_hash VARCHAR(64), 
	structured_output JSON, 
	rendered_text TEXT, 
	posted_message_id VARCHAR(128), 
	model_name VARCHAR(128), 
	prompt_version VARCHAR(40), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	published_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_daily_digests PRIMARY KEY (id), 
	CONSTRAINT uq_digest_day UNIQUE (platform, workspace_id, channel_id, digest_date), 
	CONSTRAINT uq_digest_scope UNIQUE (id, platform, workspace_id, channel_id), 
	CONSTRAINT ck_daily_digests_status CHECK (status IN ('pending', 'processing', 'ready', 'publishing', 'published', 'failed', 'review'))
);

CREATE TABLE standup_entries (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	platform VARCHAR(20) NOT NULL, 
	workspace_id VARCHAR(64) NOT NULL, 
	channel_id VARCHAR(64) NOT NULL, 
	input_kind VARCHAR(20) NOT NULL, 
	source_id VARCHAR(200) NOT NULL, 
	source_url TEXT, 
	raw_text TEXT NOT NULL, 
	standup_date DATE NOT NULL, 
	submitted_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_standup_entries PRIMARY KEY (id), 
	CONSTRAINT fk_standup_entries_user_id_users FOREIGN KEY(user_id, platform, workspace_id) REFERENCES users (id, platform, workspace_id) ON DELETE CASCADE, 
	CONSTRAINT uq_entry_source UNIQUE (platform, workspace_id, source_id), 
	CONSTRAINT uq_entry_scope UNIQUE (id, platform, workspace_id, channel_id), 
	CONSTRAINT ck_standup_entries_text_length CHECK (length(raw_text) BETWEEN 1 AND 4000), 
	CONSTRAINT ck_standup_entries_input_kind CHECK (input_kind IN ('command', 'message'))
);

CREATE INDEX ix_entries_daily_scope ON standup_entries (platform, workspace_id, channel_id, standup_date);

CREATE TABLE tracker_sync (
	id VARCHAR(36) NOT NULL, 
	digest_id VARCHAR(36) NOT NULL, 
	platform VARCHAR(20) NOT NULL, 
	workspace_id VARCHAR(64) NOT NULL, 
	channel_id VARCHAR(64) NOT NULL, 
	tracker_target VARCHAR(128) NOT NULL, 
	item_key VARCHAR(64) NOT NULL, 
	payload JSON NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	external_ticket_url TEXT, 
	board_status VARCHAR(20) NOT NULL, 
	error_code VARCHAR(80), 
	external_ticket_id VARCHAR(128), 
	attempts INTEGER NOT NULL, 
	lease_token VARCHAR(36), 
	lease_until TIMESTAMP WITH TIME ZONE, 
	next_attempt_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_tracker_sync PRIMARY KEY (id), 
	CONSTRAINT fk_tracker_sync_digest_id_daily_digests FOREIGN KEY(digest_id, platform, workspace_id, channel_id) REFERENCES daily_digests (id, platform, workspace_id, channel_id) ON DELETE CASCADE, 
	CONSTRAINT uq_tracker_item UNIQUE (platform, workspace_id, tracker_target, item_key), 
	CONSTRAINT ck_tracker_sync_status CHECK (status IN ('pending', 'processing', 'synced', 'failed', 'review'))
);

CREATE TABLE digest_sources (
	digest_id VARCHAR(36) NOT NULL, 
	entry_id VARCHAR(36) NOT NULL, 
	entry_platform VARCHAR(20) NOT NULL, 
	platform VARCHAR(20) NOT NULL, 
	workspace_id VARCHAR(64) NOT NULL, 
	channel_id VARCHAR(64) NOT NULL, 
	CONSTRAINT pk_digest_sources PRIMARY KEY (digest_id, entry_id), 
	CONSTRAINT fk_digest_sources_digest_id_daily_digests FOREIGN KEY(digest_id, platform, workspace_id, channel_id) REFERENCES daily_digests (id, platform, workspace_id, channel_id) ON DELETE CASCADE, 
	CONSTRAINT fk_digest_sources_entry_id_standup_entries FOREIGN KEY(entry_id, entry_platform, workspace_id, channel_id) REFERENCES standup_entries (id, platform, workspace_id, channel_id) ON DELETE CASCADE
);
