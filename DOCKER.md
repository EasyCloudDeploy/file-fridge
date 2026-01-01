# Running File Fridge with Docker

This guide explains how to run File Fridge using Docker and Docker Compose.

## Quick Start

1. **Create necessary directories:**
   ```bash
   mkdir -p data hot-storage cold-storage
   ```

2. **Configure environment variables (optional):**
   ```bash
   cp .env.example .env
   # Edit .env with your preferred settings
   ```

3. **Start the application:**
   ```bash
   docker-compose up -d
   ```

4. **Access the web interface:**
   Open your browser and navigate to `http://localhost:8000`

5. **View logs:**
   ```bash
   docker-compose logs -f file-fridge
   ```

6. **Stop the application:**
   ```bash
   docker-compose down
   ```

## Configuration

### Environment Variables

You can configure File Fridge using environment variables in the `docker-compose.yml` file or by creating a `.env` file. See `.env.example` for available options.

Key environment variables:
- `DATABASE_URL`: Database connection string (default: SQLite)
- `LOG_LEVEL`: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `LOG_FILE_PATH`: Optional path to log file (if not set, logs only to stdout)
- `MAX_FILE_SIZE_MB`: Maximum file size to process (in MB)
- `DEFAULT_CHECK_INTERVAL`: Default scan interval (in seconds)
- `ALLOW_ATIME_OVER_NETWORK_MOUNTS`: Enable atime tracking on network mounts

### Volume Mounts

The docker-compose.yml includes several volume mounts:

1. **Database persistence** (`./data:/app/data`):
   - Stores the SQLite database file
   - Ensures data persists across container restarts

2. **Hot storage** (`./hot-storage:/hot-storage`):
   - Example mount for source directories to monitor
   - Customize paths based on your needs

3. **Cold storage** (`./cold-storage:/cold-storage`):
   - Example mount for destination directories
   - Customize paths based on your needs

#### Customizing Volume Mounts

Edit the `docker-compose.yml` file to add your own paths:

```yaml
volumes:
  - ./data:/app/data
  - /path/to/your/source:/source
  - /mnt/nas/cold-storage:/cold
```

When configuring paths in the File Fridge UI, use the container paths (e.g., `/source`, `/cold`).

### Logging Configuration

By default, File Fridge logs to stdout, which is captured by Docker and viewable via `docker-compose logs`.

To enable persistent file logging:

1. **Add LOG_FILE_PATH to docker-compose.yml:**
   ```yaml
   environment:
     - LOG_FILE_PATH=/app/logs/file-fridge.log
   ```

2. **Add a volume mount for the logs directory:**
   ```yaml
   volumes:
     - ./data:/app/data
     - ./logs:/app/logs
   ```

3. **Create the logs directory:**
   ```bash
   mkdir -p logs
   ```

Now logs will be written to both stdout and the file at `./logs/file-fridge.log`.

**Note:** For most Docker deployments, using stdout (default) is recommended as it integrates with Docker's logging system and external log collectors.

### Version Display

The application displays its version in two places:
- **Navbar**: Version badge in the top-right corner
- **Footer**: Version information at the bottom of each page

The version is read from the `VERSION` file in the project root and exposed via the `/health` endpoint. The UI automatically fetches and displays it on page load.

**Note:** When using the Docker image, the version is baked into the image at build time. To see your current version, visit any page in the web interface or check the `/health` endpoint.

## Using with Existing Directories

If you want to monitor existing directories on your host system:

1. **Add volume mounts to docker-compose.yml:**
   ```yaml
   volumes:
     - ./data:/app/data
     - /var/log:/var/log:ro  # Read-only mount for monitoring
     - /mnt/cold:/mnt/cold    # Read-write for cold storage
   ```

2. **Configure paths in File Fridge:**
   - Source Path: `/var/log`
   - Cold Storage Path: `/mnt/cold/logs`

## Advanced Configuration

### Running as Specific User

To run the container as a specific user (useful for permission management):

```yaml
services:
  file-fridge:
    user: "1000:1000"  # Replace with your user:group ID
```

### Resource Limits

To limit CPU and memory usage:

```yaml
services:
  file-fridge:
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 2G
```

### Using PostgreSQL Instead of SQLite

If you need a more robust database:

1. **Add PostgreSQL service to docker-compose.yml:**
   ```yaml
   services:
     file-fridge:
       # ... existing config ...
       depends_on:
         - postgres
       environment:
         - DATABASE_URL=postgresql://filefridge:password@postgres:5432/filefridge

     postgres:
       image: postgres:16-alpine
       restart: unless-stopped
       environment:
         - POSTGRES_USER=filefridge
         - POSTGRES_PASSWORD=password
         - POSTGRES_DB=filefridge
       volumes:
         - postgres-data:/var/lib/postgresql/data

   volumes:
     postgres-data:
   ```

## Troubleshooting

### Permission Issues

If you encounter permission errors:

1. Check file ownership on mounted volumes:
   ```bash
   ls -la data/ hot-storage/ cold-storage/
   ```

2. The container runs as user `filefridge` by default. You may need to:
   - Change ownership: `chown -R 1000:1000 data/`
   - Or run container as your user: Add `user: "$(id -u):$(id -g)"` to docker-compose.yml

### Database Locked Errors

If using SQLite and getting "database locked" errors:
- Ensure the database file is on a local filesystem, not NFS
- Consider using PostgreSQL for network storage scenarios

### Container Won't Start

Check logs:
```bash
docker-compose logs file-fridge
```

Common issues:
- Port 8000 already in use: Change port mapping to `"8001:8000"`
- Missing directories: Create data, hot-storage, and cold-storage directories

## Updating

To update to the latest version:

```bash
docker-compose pull
docker-compose up -d
```

## Backup

To backup your data:

```bash
# Stop the container
docker-compose down

# Backup the data directory
tar -czf file-fridge-backup-$(date +%Y%m%d).tar.gz data/

# Restart
docker-compose up -d
```

## Security Considerations

1. **Use HTTPS** if exposing to the internet (reverse proxy recommended)
2. **Limit network exposure** using Docker networks or firewall rules
3. **Regular backups** of the database
4. **Monitor logs** for suspicious activity
