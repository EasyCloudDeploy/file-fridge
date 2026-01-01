# File Fridge

File Fridge is a full-stack Python application for managing file cold storage. It allows Linux server administrators to automatically move files to cold storage based on configurable criteria, monitor file movements, and manage storage through a web interface.

## Features

- **Path Monitoring**: Configure multiple directories to monitor for files matching criteria
- **Flexible Criteria**: Use find-compatible criteria (mtime, size, name, type, permissions, etc.)
- **Multiple Operation Types**: Move, copy, or move with symlink creation
- **Scheduled Scans**: Automatic periodic scanning with configurable intervals
- **Web Dashboard**: Modern web UI for managing paths, viewing statistics, and browsing files
- **REST API**: Full REST API for programmatic access
- **Statistics**: Track files moved, storage usage, and activity over time

## Requirements

- Python 3.8+ (Python 3.11+ recommended)
- Linux/Unix system (for file operations)
- uv (recommended) or pip for installation

**Database:** Uses SQLite (included with Python) - no additional database server needed

## Installation

### Using Docker (Recommended for Production)

The easiest way to run File Fridge is with Docker:

1. Clone the repository:
```bash
git clone <repository-url>
cd file-fridge
```

2. Create storage directories:
```bash
mkdir -p hot-storage cold-storage
```

3. (Optional) Configure environment variables:
```bash
cp .env.example .env
# Edit .env with your settings
```

4. Start with Docker Compose:
```bash
docker-compose up -d
```

5. Access the web interface at `http://localhost:8000`

**Important for Symlink Operations**: If you're using the "move and symlink" operation type, you may need to configure path translation. See the [Docker Deployment Guide](DOCKER.md#symlink-operations-in-docker) for details.

For detailed Docker documentation, see [DOCKER.md](DOCKER.md).

### Using uv (Recommended for Development)

1. Install `uv` if you haven't already:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# Or on Windows: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

2. Clone the repository:
```bash
git clone <repository-url>
cd file-fridge
```

3. Install the project with uv:
```bash
uv sync
```

This will create a virtual environment and install all dependencies automatically.

4. (Optional) Configure via environment variables or `.env` file:

You can set configuration via environment variables:
```bash
export LOG_LEVEL=DEBUG
export DATABASE_PATH=./file_fridge.db
```

Or create a `.env` file in the project root:
```bash
LOG_LEVEL=INFO
DATABASE_PATH=./file_fridge.db
```

Available environment variables:
- `LOG_LEVEL`: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL). Default: INFO
- `LOG_FILE_PATH`: Optional path to log file. If not set, logs only to stdout. Default: None
- `DATABASE_PATH`: Database file path. Default: ./data/file_fridge.db
- `CONTAINER_PATH_PREFIX`: Path prefix inside container (for symlink operations in Docker). Default: None
- `HOST_PATH_PREFIX`: Path prefix on host system (for symlink operations in Docker). Default: None

For Docker deployments using symlink operations, see [DOCKER.md](DOCKER.md#symlink-operations-in-docker) for path translation configuration.

### Using pip (Alternative)

1. Clone the repository:
```bash
git clone <repository-url>
cd file-fridge
```

2. Create a virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. (Optional) Configure via environment variables or `.env` file (see above).

## Usage

### Starting the Application

#### Using uv (Recommended)

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Or activate the virtual environment first:
```bash
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

#### Using pip

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Or use the built-in runner:

```bash
python -m app.main
```

The web interface will be available at `http://localhost:8000`

### Configuration

**Important:** The scan interval must be properly aligned with your criteria thresholds. See [Configuration Best Practices](docs/CONFIGURATION_GUIDE.md) for details.

**Rule of Thumb:** Scan interval should be ≤ 1/3 of your smallest time-based criterion threshold.

1. **Add a Monitored Path**:
   - Navigate to "Paths" in the web UI
   - Click "Add Path"
   - Configure:
     - Name: Descriptive name
     - Source Path: Directory to monitor
     - Cold Storage Path: Destination for moved files
     - Operation Type: Move, Copy, or Symlink
     - Check Interval: How often to scan (in seconds, minimum 60)
       - **For atime/mtime < 3 min:** Use 60 seconds (1 min)
       - **For atime/mtime < 60 min:** Use 300-600 seconds (5-10 min)
       - **For atime/mtime < 1440 min (1 day):** Use 1800-3600 seconds (30-60 min)
     - Enabled: Enable/disable automatic scanning

2. **Add Criteria** (via API):
   - Use the REST API to add criteria for each path
   - Example: Move files older than 30 minutes (or 1440 for 1 day)
   - Example: Move files larger than 1GB

3. **Manual Scans**:
   - Trigger manual scans from the path detail page
   - Or use the API endpoint: `POST /api/v1/paths/{id}/scan`

### API Examples

**Create a monitored path:**
```bash
curl -X POST "http://localhost:8000/api/v1/paths" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Log Files",
    "source_path": "/var/log",
    "cold_storage_path": "/cold/logs",
    "operation_type": "move",
    "check_interval_seconds": 3600,
    "enabled": true
  }'
```

**Add a criteria (files older than 30 minutes):**
```bash
curl -X POST "http://localhost:8000/api/v1/criteria/path/1" \
  -H "Content-Type: application/json" \
  -d '{
    "criterion_type": "mtime",
    "operator": ">",
    "value": "30",
    "enabled": true
  }'
```

**Note:** Time-based criteria (mtime, atime, ctime) use minutes, not days. For example:
- 30 minutes = `"30"`
- 1 hour = `"60"`
- 1 day = `"1440"`
- 1 week = `"10080"`

**Get statistics:**
```bash
curl "http://localhost:8000/api/v1/stats"
```

## Criteria Types

File Fridge supports find-compatible criteria:

- **mtime**: Modification time (minutes)
- **atime**: Access time (minutes)
- **ctime**: Change time (minutes)
- **size**: File size (supports suffixes: c, k, M, G)
- **name**: Filename (glob patterns)
- **iname**: Case-insensitive filename
- **type**: File type (f=file, d=directory, l=link)
- **perm**: Permissions (octal or symbolic)
- **user**: File owner (username or UID)
- **group**: File group (groupname or GID)

### Important: How Criteria Work

**Criteria define what files to KEEP in hot storage (active files), not what to move to cold.**

Examples:
- `atime < 3` → "Keep files accessed in the last 3 minutes in hot storage"
- `mtime > 1440` → "Keep files older than 1 day in hot storage"
- Files that DON'T match criteria are moved to cold storage

### Access Time (atime) Considerations

**Does File Fridge update atime when scanning?**
- **NO** - File Fridge only uses metadata operations (`stat()`) that do not update atime
- Scanning will not affect your files' access times

**Filesystem mount options affect atime tracking:**
- `relatime` (default on modern Linux) - Recommended: Updates atime intelligently
- `strictatime` - Updates atime on every access (performance impact)
- `noatime` - Never updates atime (DO NOT USE if you need atime-based criteria)

**For macOS users:**
- File Fridge uses both `atime` and Spotlight's "Last Open" time
- Takes the most recent of the two for better accuracy
- **Important:** If "Last Open" is `None` (file never opened), it's treated as "infinitely old" (epoch time)
- This ensures files that have never been opened are moved to cold storage
- More reliable for tracking actual user file access than `atime` alone

**For network mounts (SMB/NFS):**
- Verify atime is enabled on the server
- Test with a few files before relying on atime-based criteria
- Consider using `mtime` instead if atime is unreliable

**Verification:**
See `docs/ATIME_VERIFICATION.md` for a script to verify atime behavior on your filesystem.

## Operation Types

- **move**: Move file to cold storage (delete original)
- **copy**: Copy file to cold storage (keep original)
- **symlink**: Move file and create symlink at original location

## Project Structure

```
file-fridge/
├── app/
│   ├── main.py              # Application entry point
│   ├── config.py            # Configuration
│   ├── database.py          # Database setup
│   ├── models.py            # SQLAlchemy models
│   ├── schemas.py           # Pydantic schemas
│   ├── services/            # Core services
│   └── routers/             # API and web routes
├── static/                  # Static files (CSS, JS, HTML)
├── requirements.txt         # Python dependencies
└── README.md               # This file
```

## Security Considerations

- The application does not include authentication by default (assumes trusted network)
- All path inputs are validated to prevent directory traversal
- File operations respect system permissions
- Consider adding authentication for production use

## Development

### Running Tests

```bash
# Add tests to tests/ directory
pytest
```

### Database Migrations

The application uses SQLAlchemy with automatic table creation. For production, consider using Alembic for migrations.

## License

Apache License 2.0 - See LICENSE file for details.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.

## Support

For issues and questions, please open an issue on the project repository.

