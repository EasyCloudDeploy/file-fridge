# Dynamic UI Configuration Summary

## Overview

All HTML pages in the File Fridge application now dynamically load the application name and version from the `/health` API endpoint.

## Changes Made

### 1. Backend - Health API Endpoint

**File:** `app/main.py`

The `/health` endpoint now returns:
```json
{
    "status": "healthy",
    "version": "0.0.5",
    "app_name": "File Fridge"
}
```

Values are pulled from:
- `settings.app_version` - Read from `VERSION` file (defaults to "0.0.0")
- `settings.app_name` - From environment variable `APP_NAME` (defaults to "File Fridge")

### 2. Frontend - JavaScript Function

**File:** `static/js/app.js`

Added `loadAppInfo()` function that:
- Fetches data from `/health` endpoint
- Updates all elements with these IDs:
  - `app-name-title` - App name in page title
  - `app-name-navbar` - App name in navbar brand
  - `app-name-footer` - App name in footer
  - `app-version` - Version in navbar badge (with 'v' prefix)
  - `footer-app-version` - Version in footer (without prefix)

### 3. HTML Pages Updated

All HTML pages now include:

1. **Dynamic App Name** in three locations:
   - Page title: `<title>Page Name - <span id="app-name-title">File Fridge</span></title>`
   - Navbar brand: `<span id="app-name-navbar">File Fridge</span>`
   - Footer: `<span id="app-name-footer">File Fridge</span>`

2. **Dynamic Version** in two locations:
   - Navbar badge: `<span id="app-version">Loading...</span>`
   - Footer: `v<span id="footer-app-version">Loading...</span>`

3. **Page Load Script**:
   ```javascript
   document.addEventListener('DOMContentLoaded', loadAppInfo);
   ```

**Updated Pages:**
- `static/html/base.html`
- `static/html/dashboard.html`
- `static/html/files.html`
- `static/html/stats.html`
- `static/html/paths/list.html`
- `static/html/paths/form.html`
- `static/html/paths/detail.html`
- `static/html/criteria/form.html`

## Configuration

### Changing the App Name

Set the `APP_NAME` environment variable:

```bash
# In .env file
APP_NAME=My Custom Storage App

# Or in docker-compose.yml
environment:
  - APP_NAME=My Custom Storage App
```

### Changing the Version

Update the `VERSION` file in the project root:

```bash
echo "1.0.0" > VERSION
```

Or set via environment (overrides VERSION file):

```bash
# In docker-compose.yml
environment:
  - APP_VERSION=1.0.0
```

## Verification

1. **Check health endpoint:**
   ```bash
   curl http://localhost:8000/health
   ```

2. **Verify UI:** Visit any page and check:
   - Navbar shows correct app name and version badge
   - Footer shows correct app name and version
   - Page title includes app name

## Benefits

- **Centralized Configuration**: Single source of truth for app name and version
- **Environment-Specific Branding**: Different names for dev/staging/production
- **Automatic Updates**: Changes to VERSION file or APP_NAME env var are reflected immediately
- **Consistent UI**: All pages display the same information
- **Docker-Friendly**: Configuration via environment variables
