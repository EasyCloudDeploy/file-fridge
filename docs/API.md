# REST API Documentation

File Fridge provides a comprehensive REST API that allows for programmatic management of all application features.

## Interactive API Docs

The application includes interactive API documentation powered by Swagger UI and ReDoc.

- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

These interfaces provide detailed information about every endpoint, including request/response schemas and the ability to test calls directly from your browser.

## API Structure

All API endpoints are prefixed with `/api/v1/`.

### Core Resource Groups

| Group | Description |
|-------|-------------|
| `/paths` | Manage monitored source paths and their configurations. |
| `/criteria` | Configure movement criteria for monitored paths. |
| `/storage` | Manage cold storage locations. |
| `/files` | Browse and manage the file inventory, including pinning and manual movement. |
| `/tags` | Create and manage tags and manual file tagging. |
| `/tag-rules` | Configure and apply automated tagging rules. |
| `/notifiers` | Configure and test notification destinations. |
| `/stats` | Access system performance and storage metrics. |
| `/auth` | Authentication and user management. |

## Authentication

If authentication is enabled, most API endpoints require a Bearer Token.

1. **Obtain Token**: Send a POST request to `/api/v1/auth/login` with your credentials.
2. **Use Token**: Include the token in the `Authorization` header of subsequent requests:
   `Authorization: Bearer <your-token>`

## Example Usage

### List all monitored paths
```bash
curl http://localhost:8000/api/v1/paths
```

### Trigger a manual scan
```bash
curl -X POST http://localhost:8000/api/v1/paths/1/scan
```

### Get storage statistics
```bash
curl http://localhost:8000/api/v1/stats/detailed
```
