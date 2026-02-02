## 2026-01-17 - Broken Security Decorator
**Vulnerability:** The `rate_limit` decorator in `app/utils/rate_limiter.py` instantiated the `RateLimiter` class *inside* the wrapper function. This meant a new limiter was created for every request, rendering the rate limit ineffective (state was never preserved).
**Learning:** It existed because the decorator pattern was implemented incorrectly, likely a copy-paste error or misunderstanding of closure scope in Python decorators. Tests were missing for the rate limiting functionality itself.
**Prevention:** Always verify security controls with negative tests (ensure they actually block traffic). Review decorator scope carefully when storing state.

## 2026-02-02 - Unrestricted Directory Traversal in Browser API
**Vulnerability:** The `/api/v1/browser/list` endpoint allowed any authenticated user (including read-only "viewers") to browse the entire server filesystem, including sensitive system directories like `/etc/`.
**Learning:** The endpoint relied solely on role-based permission (`browser:read`) but failed to implement data-level access control (validating that the path is within allowed boundaries). The docstring "unrestricted (admins can browse anywhere)" implied a design choice that dangerously ignored non-admin users.
**Prevention:** Always validate input parameters against an allowlist, especially for file paths. Ensure that RBAC checks are complemented by resource-level authorization checks.
