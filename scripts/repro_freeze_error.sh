#!/usr/bin/env bash

set -euo pipefail

DB_PATH="${FILE_FRIDGE_DB_PATH:-data/file_fridge.db}"
PATH_ID="${FILE_FRIDGE_PATH_ID:-}"
STORAGE_ID="${FILE_FRIDGE_STORAGE_ID:-}"
_raw_test_name="${FILE_FRIDGE_TEST_NAME:-ff-test-repro-$(date +%Y%m%d-%H%M%S)}"
# Sanitize: allow only alphanumerics, dot, underscore, hyphen
TEST_NAME="${_raw_test_name//[^a-zA-Z0-9._-]/_}"
if [[ -z "$TEST_NAME" ]]; then
    TEST_NAME="ff-test-repro"
fi
REPRO_MODE="${FILE_FRIDGE_REPRO_MODE:-conflict}"

if [[ ! -f "$DB_PATH" ]]; then
    echo "Database not found: $DB_PATH" >&2
    exit 1
fi

if [[ -z "$PATH_ID" ]]; then
    PATH_ID="$(sqlite3 "$DB_PATH" "select id from monitored_paths order by id limit 1;")"
fi

if [[ -z "$PATH_ID" ]]; then
    echo "No monitored path found in $DB_PATH" >&2
    exit 1
fi

if [[ ! "$PATH_ID" =~ ^[0-9]+$ ]]; then
    echo "Invalid PATH_ID: must be a positive integer, got: $PATH_ID" >&2
    exit 1
fi

if [[ -z "$STORAGE_ID" ]]; then
    STORAGE_ID="$(
        sqlite3 "$DB_PATH" \
            "select storage_location_id from path_storage_location_association where path_id = $PATH_ID order by storage_location_id limit 1;"
    )"
fi

if [[ -z "$STORAGE_ID" ]]; then
    echo "No storage location associated with path_id=$PATH_ID" >&2
    exit 1
fi

if [[ ! "$STORAGE_ID" =~ ^[0-9]+$ ]]; then
    echo "Invalid STORAGE_ID: must be a positive integer, got: $STORAGE_ID" >&2
    exit 1
fi

PATH_NAME="$(
    sqlite3 "$DB_PATH" \
        "select name from monitored_paths where id = $PATH_ID;"
)"
SOURCE_PATH="$(
    sqlite3 "$DB_PATH" \
        "select source_path from monitored_paths where id = $PATH_ID;"
)"
STORAGE_NAME="$(
    sqlite3 "$DB_PATH" \
        "select name from cold_storage_locations where id = $STORAGE_ID;"
)"
STORAGE_PATH="$(
    sqlite3 "$DB_PATH" \
        "select path from cold_storage_locations where id = $STORAGE_ID;"
)"

if [[ -z "$SOURCE_PATH" || -z "$STORAGE_PATH" ]]; then
    echo "Failed to resolve source or storage path from the database." >&2
    exit 1
fi

HOT_DIR="$SOURCE_PATH/$TEST_NAME"
COLD_DIR="$STORAGE_PATH/$TEST_NAME"
HOT_FILE="$HOT_DIR/freeze-error.txt"
COLD_FILE="$COLD_DIR/freeze-error.txt"

if [[ -e "$HOT_DIR" || -e "$COLD_DIR" ]]; then
    echo "Directories already exist for TEST_NAME=$TEST_NAME; aborting to avoid overwriting." >&2
    echo "  HOT_DIR:  $HOT_DIR" >&2
    echo "  COLD_DIR: $COLD_DIR" >&2
    exit 1
fi
mkdir -p "$HOT_DIR" "$COLD_DIR"

cat > "$HOT_FILE" <<EOF
This is a disposable File Fridge freeze error repro file.
Created at: $(date)
EOF

WHY_FAILS=""
CLEANUP_NOTES=""
COLD_FILE_LINE=""

if [[ "$REPRO_MODE" == "conflict" ]]; then
    cat > "$COLD_FILE" <<EOF
This conflicting cold-storage file forces a freeze failure because the
destination path already exists.
Created at: $(date)
EOF
    WHY_FAILS="When File Fridge tries to freeze the hot file, it will compute the same
  relative destination under cold storage and should fail with:
  \"Destination already exists: $COLD_FILE\""
    COLD_FILE_LINE="  cold: $COLD_FILE"
elif [[ "$REPRO_MODE" == "permission_denied" ]]; then
    chmod 0555 "$COLD_DIR"
    WHY_FAILS="When File Fridge tries to freeze the hot file, the destination directory
  exists but is not writable, so the move/copy should fail with a permission error."
    CLEANUP_NOTES="  chmod 0755 \"$COLD_DIR\""
else
    echo "Unsupported FILE_FRIDGE_REPRO_MODE: $REPRO_MODE" >&2
    echo "Expected one of: conflict, permission_denied" >&2
    exit 1
fi

cat <<EOF
Prepared a local freeze-failure repro.

Mode:
  $REPRO_MODE

Path:
  id: $PATH_ID
  name: $PATH_NAME
  source: $SOURCE_PATH

Storage:
  id: $STORAGE_ID
  name: $STORAGE_NAME
  path: $STORAGE_PATH

Created files:
  hot:  $HOT_FILE
${COLD_FILE_LINE}

Why this fails:
  $WHY_FAILS

Next steps:
  1. Trigger a scan for path_id=$PATH_ID from the UI or API.
  2. Open /migrations and look for the freeze/thaw row for:
     $HOT_FILE
  3. The UI should now show the inline error message.

Optional API trigger:
  curl -X POST "http://127.0.0.1:8000/api/v1/paths/$PATH_ID/scan" \\
    -H "Authorization: Bearer <token>"

Optional inspection:
  curl "http://127.0.0.1:8000/api/v1/migrations/freezing" \\
    -H "Authorization: Bearer <token>"

Cleanup:
${CLEANUP_NOTES}
  rm -f "$HOT_FILE" "$COLD_FILE"
  rmdir "$HOT_DIR" "$COLD_DIR" 2>/dev/null || true
EOF
