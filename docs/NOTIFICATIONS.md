# Notification System

File Fridge includes a robust notification system to keep you informed about automated tasks and system health.

## Supported Notifiers

### 1. Email (SMTP)
Send notifications directly to your inbox.

**Configuration Parameters:**
- **Address**: The recipient's email address.
- **SMTP Host**: The hostname of your SMTP server (e.g., `smtp.gmail.com`).
- **SMTP Port**: Usually `587` for TLS or `465` for SSL.
- **SMTP User/Password**: Authentication credentials for your mail server.
- **Sender Address**: The "From" email address.
- **Use TLS**: Enable/disable encryption.

### 2. Generic Webhook
Integrate with third-party services like Discord, Slack, or custom automation scripts.

**Configuration Parameters:**
- **URL**: The endpoint where the webhook payload will be sent (POST request).

**Payload Format:**
Webhooks send a JSON payload in the following format:
```json
{
  "level": "INFO",
  "message": "Successfully completed sync for path: Logs",
  "timestamp": "2023-10-27T10:00:00.000Z",
  "source": "File Fridge",
  "metadata": {
    "path_name": "Logs",
    "files_moved": 42,
    "total_size": 1048576
  }
}
```

## Notification Levels

You can configure each notifier to only trigger for specific severity levels:

- **INFO**: General operational updates (e.g., successful scans).
- **WARNING**: Potential issues that don't stop operation (e.g., low disk space, configuration warnings).
- **ERROR**: Critical failures that require attention (e.g., sync errors, permission denied).

A notifier set to `INFO` will receive all notifications. A notifier set to `ERROR` will only receive error-level messages.

## Monitored Events

The system automatically dispatches notifications for the following events:

1. **Sync Success**: Triggered after a monitored path completes a scan and move operation successfully.
2. **Sync Error**: Triggered if a scan or file operation fails.
3. **Low Disk Space**: Triggered when a storage location's free space falls below the configured threshold.

## Testing Notifiers

You can send a test notification from the "Notifiers" page in the Web UI to verify your configuration is correct.
