# Docker Deployment Guide

Running File Fridge in Docker is the recommended approach for most users. This guide covers advanced Docker configuration, including path translation and symlink handling.

## Basic Setup

Refer to the [Installation Guide](INSTALLATION.md) for the basic Docker Compose setup.

## Symlink Operations in Docker

When using the `symlink` operation type within a Docker container, symbolic links are created using paths **internal** to the container. If these links are accessed from the host system or another container, they will likely be broken because the paths won't match.

### Path Translation

To solve this, File Fridge supports path translation. You can configure the application to write symlinks using host-relative paths even while running inside a container.

**Environment Variables:**
- `CONTAINER_PATH_PREFIX`: The base path used inside the container (e.g., `/storage/hot`).
- `HOST_PATH_PREFIX`: The corresponding base path on your host system (e.g., `/mnt/data/hot`).

**How it works:**
If a file is moved from `/storage/hot/project/file.txt` to cold storage, and path translation is configured, the symlink created at `/storage/hot/project/file.txt` will point to the cold storage location using the `HOST_PATH_PREFIX` instead of the internal container path.

### Example Configuration

```yaml
services:
  file-fridge:
    image: filefridge/file-fridge:latest
    volumes:
      - /mnt/user/hot:/storage/hot
      - /mnt/user/cold:/storage/cold
    environment:
      - CONTAINER_PATH_PREFIX=/storage/hot
      - HOST_PATH_PREFIX=/mnt/user/hot
```

## Volume Mounts and Permissions

Ensure the user running the Docker container has appropriate permissions to read and write to the mounted volumes.

By default, the container runs as root, which is usually necessary for certain file operations, but you can specify a specific user:

```yaml
services:
  file-fridge:
    user: "1000:1000"
    # ...
```

> **Note**: If you run as a non-root user, ensure that user has UID/GID permissions on the host directories mounted as volumes.

## Persisting Data

Always ensure the directory containing the SQLite database is mounted as a persistent volume. If you don't, your configuration, file inventory, and statistics will be lost whenever the container is recreated.

```yaml
    volumes:
      - ./data:/app/data
    environment:
      - DATABASE_PATH=/app/data/file_fridge.db
```
