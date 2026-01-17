# Installation Guide

File Fridge can be installed using Docker (recommended) or manually on a Linux/Unix system.

## System Requirements

- **Operating System**: Linux or Unix-like system (required for certain file operations and atime tracking)
- **Python**: 3.8+ (3.11+ recommended) if installing manually
- **Package Manager**: [uv](https://astral.sh/uv) (recommended) or pip

## Docker Installation (Recommended)

Docker is the easiest and most reliable way to run File Fridge, as it handles all dependencies and environment setup.

### 1. Prerequisites

Ensure you have Docker and Docker Compose installed on your system.

### 2. Prepare Directories

Create the necessary directories for your storage and data:

```bash
mkdir -p file-fridge/data
mkdir -p file-fridge/hot-storage
mkdir -p file-fridge/cold-storage
cd file-fridge
```

### 3. Create Docker Compose File

Create a `docker-compose.yml` file:

```yaml
version: '3.8'

services:
  file-fridge:
    image: filefridge/file-fridge:latest
    container_name: file-fridge
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
      - ./hot-storage:/storage/hot
      - ./cold-storage:/storage/cold
    environment:
      - DATABASE_PATH=/app/data/file_fridge.db
      - LOG_LEVEL=INFO
    restart: unless-stopped
```

### 4. Start the Application

```bash
docker-compose up -d
```

Access the web interface at `http://localhost:8000`.

---

## Manual Installation (using uv)

If you prefer to run the application directly on your host system:

### 1. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Clone the Repository

```bash
git clone <repository-url>
cd file-fridge
```

### 3. Install Dependencies

```bash
uv sync
```

### 4. Configure Environment

Create a `.env` file in the project root:

```bash
LOG_LEVEL=INFO
DATABASE_PATH=./data/file_fridge.db
```

### 5. Run the Application

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## Manual Installation (using pip)

### 1. Clone the Repository

```bash
git clone <repository-url>
cd file-fridge
```

### 2. Create Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the Application

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Next Steps

Once installed, refer to the **[Usage Guide](USAGE.md)** to configure your storage locations, monitored paths, and criteria.

### Creating an Initial User
If you have authentication enabled, you'll need to create a user using the `manage_user.py` script:

**Using uv:**
```bash
uv run python manage_user.py create --username admin --password secret
```

**Using Docker:**
```bash
docker exec -it file-fridge python manage_user.py create --username admin --password secret
```
