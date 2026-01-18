# Usage Guide

This guide walks you through the common tasks and configurations in File Fridge.

## Core Concepts

Before you start, remember the golden rule of File Fridge:
**Criteria define what to KEEP in hot storage, not what to move to cold.**

If a file matches your criteria, it stays in Hot Storage. If it does NOT match any criteria, it is moved to Cold Storage.

## Setting Up Your First Monitored Path

### 1. Add a Storage Location
Before adding a monitored path, you need at least one cold storage location.
1. Navigate to **Storage Locations** in the Web UI.
2. Click **Add Location**.
3. Provide a name and the absolute path to your cold storage directory.

### 2. Add a Monitored Path
1. Navigate to **Paths** and click **Add Path**.
2. **Name**: A descriptive name for the monitored directory.
3. **Source Path**: The absolute path to the directory you want to monitor.
4. **Operation Type**:
    - **Move**: Standard move.
    - **Copy**: Keep a copy in hot storage.
    - **Symlink**: Move and leave a symbolic link.
5. **Check Interval**: How often to scan (in seconds).
    - *Tip*: Consult the [Configuration Best Practices](CONFIGURATION_GUIDE.md) for help choosing the right interval.

## Configuring Criteria

Criteria are added to specific paths to control file movement.

### Adding Criteria via Web UI
1. Go to the **Path Details** page for your path.
2. Click **Add Criteria**.
3. Choose the **Type** (e.g., `atime`), **Operator** (e.g., `<`), and **Value** (e.g., `1440` for 1 day).

### Adding Criteria via API
You can also manage criteria using `curl`.

**Example: Keep files accessed in the last 30 minutes:**
```bash
curl -X POST "http://localhost:8000/api/v1/criteria/path/1" \
  -H "Content-Type: application/json" \
  -d '{
    "criterion_type": "atime",
    "operator": "<",
    "value": "30",
    "enabled": true
  }'
```

## Running Scans

### Automated Scans
Scans run automatically based on the **Check Interval** configured for each path. Ensure the path is **Enabled** for this to happen.

### Manual Scans
You can trigger a scan at any time:
- **Web UI**: Click **Run Scan** on the Path Details page.
- **API**:
  ```bash
  curl -X POST "http://localhost:8000/api/v1/paths/1/scan"
  ```

## Advanced Topics

- **Access Time nuances**: Learn how different filesystems handle `atime` in the [atime Verification Guide](ATIME_VERIFICATION.md).
- **Scan Interval vs. Thresholds**: Ensure your scans run often enough to catch files before they age out in the [Configuration Best Practices](CONFIGURATION_GUIDE.md).
- **Automated Tagging**: Automatically categorize files as they are scanned. See [Tagging and Rules](TAGGING.md).
- **Notifications**: Get alerts for sync successes or errors. See [Notifications](NOTIFICATIONS.md).
