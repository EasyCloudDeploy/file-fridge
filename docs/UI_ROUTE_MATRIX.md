# UI Route Verification Matrix

This matrix documents the expected frontend bootstrap, API usage, and UI state containers for each routed web page after the sidebar-shell revamp. It is the acceptance checklist for post-revamp stability work.

## Shared frontend contract

- `templates/base.html` defines an early `window.runWhenFileFridgeReady(...)` queue before page scripts run.
- `static/js/app.js` owns the final readiness signal, exports `authenticatedFetch`, and flushes queued page initializers exactly once.
- API-backed regions should follow the same state model:
  - `loading`
  - `content`
  - `empty`
  - `error`
- Shared helpers:
  - `runWhenFileFridgeReady(...)`
  - `authenticatedFetch(...)`
  - `setRegionState(...)`
  - `assertRequiredElements(...)`

## Route matrix

| Route | Template | Page scripts | Expected API calls | State containers / selectors |
| --- | --- | --- | --- | --- |
| `/` | `templates/dashboard.html` | `static/js/dashboard.js`, `static/js/storage.js` | `/api/v1/stats`, `/api/v1/paths`, `/api/v1/paths/stats`, `/api/v1/storage/stats`, `/health` | `#pathsList`, `#recentFilesList`, `#hotStorageStatusList`, `#storageStatusList` |
| `/files` | `templates/files.html` | `static/js/files.js`, Vite `grid` entry | `/api/v1/files/*`, `/api/v1/paths`, `/api/v1/tags`, `/api/v1/storage/locations`, `/api/v1/remote/*` | `#loading-state`, `#empty-state`, `#error-state`, modal loaders/selects inside files UI |
| `/paths` | `templates/paths/list.html` | `static/js/paths.js` | `/api/v1/paths` | `#paths-loading`, `#paths-content`, `#no-paths-message` |
| `/paths/new` | `templates/paths/form.html` | `static/js/file-browser.js`, `static/js/paths.js` | `/api/v1/storage/locations` | form validation feedback, file-browser modal state in `#browserLoading`, `#browserContent`, `#browserError` |
| `/paths/{path_id}` | `templates/paths/detail.html` | `static/js/paths.js` | `/api/v1/paths/{id}`, `/api/v1/criteria/path/{id}`, `/api/v1/storage/stats`, `/api/v1/paths/stats`, `/api/v1/paths/{id}/scan-errors`, `/api/v1/paths/{id}/scan/progress` | `#path-loading`, `#path-content`, `#path-error`, `#scan-errors-loading`, `#scan-errors-content`, `#scan-errors-empty` |
| `/paths/{path_id}/edit` | `templates/paths/form.html` | `static/js/file-browser.js`, `static/js/paths.js` | `/api/v1/paths/{id}`, `/api/v1/storage/locations` | form state, file-browser modal state |
| `/paths/{path_id}/criteria/new` | `templates/criteria/form.html` | `static/js/paths.js` | `/api/v1/paths/{id}`, `/api/v1/storage/locations` | form fields plus path summary placeholders `#path-name`, `#path-source` |
| `/criteria/{criteria_id}/edit` | `templates/criteria/form.html` | `static/js/paths.js` | `/api/v1/criteria/{id}`, `/api/v1/paths/{path_id}`, `/api/v1/storage/locations` | form fields plus path summary placeholders |
| `/storage-locations` | `templates/storage/list.html` | `static/js/storage-locations.js` | `/api/v1/storage/locations` | `#locations-loading`, `#locations-content`, `#no-locations-message`, `#alert-container` |
| `/storage-locations/new` | `templates/storage/form.html` | `static/js/file-browser.js`, `static/js/storage-location-form.js` | none required until save; optional file-browser requests | form state, file-browser modal state |
| `/storage-locations/{location_id}` | `templates/storage/detail.html` | no dedicated page script | server-rendered detail view | server-rendered only |
| `/storage-locations/{location_id}/edit` | `templates/storage/form.html` | `static/js/file-browser.js`, `static/js/storage-location-form.js` | `/api/v1/storage/locations/{id}` | form state, file-browser modal state |
| `/tags` | `templates/tags.html` | `static/js/tags.js` | `/api/v1/tags`, `/api/v1/tag-rules` | `#tags_loading`, `#tags_content`, `#tags_empty`, `#rules_loading`, `#rules_content`, `#rules_empty` |
| `/migrations` | `templates/migrations.html` | inline script in template (**exception**: boots on `DOMContentLoaded` directly, not `runWhenFileFridgeReady`) | migrations endpoints referenced by template script | loading rows inside each table body |
| `/notifiers` | `templates/notifiers.html` | `static/js/notifiers.js` | `/api/v1/notifiers` | `#notifiers_loading`, `#notifiers_content`, `#notifiers_empty`, `#alert_container` |
| `/stats` | `templates/stats.html` | `static/js/stats.js`, Vite `charts` entry | `/api/v1/stats/detailed` | `#daily-chart-loading`, `#storage-chart-loading`, `#top-files-chart-loading`, `#top-size-chart-loading` |
| `/settings` | `templates/settings.html` | `static/js/settings.js` | `/api/v1/auth/tokens`, `/api/v1/remote/*`, `/api/v1/encryption/keys`, `/api/v1/users`, `/health` | section-specific loaders in page, modal button spinners |
| `/remote-files/{connection_id}` | `templates/remote_files.html` | `static/js/remote_files.js`, Vite `grid` entry | `/api/v1/remote/connections/{id}`, `/api/v1/remote/connections/{id}/paths`, `/api/v1/paths/monitored`, `/api/v1/remote/connections/{id}/browse-files` | AG Grid overlay states plus header placeholders |
| `/login` | `templates/login.html` | inline login script | `/api/v1/auth/login`, `/api/v1/auth/me`, `/health` | login form, auth status, inline alerts |

## Verification checklist

For each route above, verify:

1. The page script initializes through `runWhenFileFridgeReady(...)`.
2. The expected API call fires after the page loads.
3. The loading UI exits on both success and failure.
4. Empty datasets render empty-state copy instead of a permanent spinner.
5. Errors render visible inline feedback or a toast without invalid table markup.
6. Mobile layout wraps text safely for tables, badges, code blocks, and page actions.
7. Desktop layout keeps page headers, cards, tables, and sidebar alignment readable.

## High-risk pages to re-check after shell changes

- `/tags`
- `/paths`
- `/storage-locations`
- `/files`
- `/remote-files/{connection_id}`
- `/settings`
- `/notifiers`
- `/stats`
