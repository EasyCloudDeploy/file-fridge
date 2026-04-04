CREATE TABLE monitored_paths (
    id INTEGER PRIMARY KEY,
    name VARCHAR NOT NULL,
    source_path VARCHAR NOT NULL,
    operation_type VARCHAR,
    check_interval_seconds INTEGER,
    enabled BOOLEAN,
    prevent_indexing BOOLEAN NOT NULL,
    max_concurrent_migrations INTEGER NOT NULL DEFAULT 3,
    error_message TEXT,
    last_scan_at DATETIME,
    last_scan_status VARCHAR,
    last_scan_error_log TEXT,
    created_at DATETIME,
    updated_at DATETIME
);

CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username VARCHAR NOT NULL,
    password_hash VARCHAR NOT NULL,
    roles JSON,
    created_at DATETIME
);

INSERT INTO monitored_paths (
    id, name, source_path, operation_type, check_interval_seconds,
    enabled, prevent_indexing, max_concurrent_migrations, created_at
) VALUES (
    1, 'Drifted Path', '/srv/hot/drifted', 'move', 3600,
    1, 1, 3, '2026-03-15 09:30:00'
);

INSERT INTO users (
    id, username, password_hash, roles, created_at
) VALUES (
    1, 'admin', 'legacy-hash', '["admin"]', '2026-03-15 09:30:00'
);
