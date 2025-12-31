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
- SQLite (included with Python)
- Linux/Unix system (for file operations)
- uv (recommended) or pip for installation

## Installation

### Using uv (Recommended)

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

4. (Optional) Create a `.env` file for configuration:
```bash
cp .env.example .env
# Edit .env with your settings
```

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

4. (Optional) Create a `.env` file for configuration:
```bash
cp .env.example .env
# Edit .env with your settings
```

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

1. **Add a Monitored Path**:
   - Navigate to "Paths" in the web UI
   - Click "Add Path"
   - Configure:
     - Name: Descriptive name
     - Source Path: Directory to monitor
     - Cold Storage Path: Destination for moved files
     - Operation Type: Move, Copy, or Symlink
     - Check Interval: How often to scan (in seconds, minimum 60)
     - Enabled: Enable/disable automatic scanning

2. **Add Criteria** (via API):
   - Use the REST API to add criteria for each path
   - Example: Move files older than 30 days
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

**Add a criteria (files older than 30 days):**
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

**Get statistics:**
```bash
curl "http://localhost:8000/api/v1/stats"
```

## Criteria Types

File Fridge supports find-compatible criteria:

- **mtime**: Modification time (days)
- **atime**: Access time (days)
- **ctime**: Change time (days)
- **size**: File size (supports suffixes: c, k, M, G)
- **name**: Filename (glob patterns)
- **iname**: Case-insensitive filename
- **type**: File type (f=file, d=directory, l=link)
- **perm**: Permissions (octal or symbolic)
- **user**: File owner (username or UID)
- **group**: File group (groupname or GID)

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
│   ├── routers/             # API and web routes
│   └── templates/           # HTML templates
├── static/                  # Static files (CSS, JS)
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

