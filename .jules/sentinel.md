## 2026-01-17 - Broken Security Decorator
**Vulnerability:** The `rate_limit` decorator in `app/utils/rate_limiter.py` instantiated the `RateLimiter` class *inside* the wrapper function. This meant a new limiter was created for every request, rendering the rate limit ineffective (state was never preserved).
**Learning:** It existed because the decorator pattern was implemented incorrectly, likely a copy-paste error or misunderstanding of closure scope in Python decorators. Tests were missing for the rate limiting functionality itself.
**Prevention:** Always verify security controls with negative tests (ensure they actually block traffic). Review decorator scope carefully when storing state.

## 2026-02-01 - Unrestricted File Browser
**Vulnerability:** The `/api/v1/browser/list` endpoint allowed any authenticated user (including "viewers") to browse the entire server filesystem, leading to Path Traversal and Information Disclosure.
**Learning:** The endpoint relied on the caller to provide a safe path but didn't enforce restrictions based on user roles, unlike other endpoints like `/api/v1/files/browse`. The `PermissionChecker` middleware authorized the request but didn't restrict the scope of the action.
**Prevention:** Ensure "browser" capabilities are either restricted to admins or strictly scoped to allowed application paths (MonitoredPaths, ColdStorageLocations). Use centralized logic for path validation.
