## 2026-01-17 - Broken Security Decorator
**Vulnerability:** The `rate_limit` decorator in `app/utils/rate_limiter.py` instantiated the `RateLimiter` class *inside* the wrapper function. This meant a new limiter was created for every request, rendering the rate limit ineffective (state was never preserved).
**Learning:** It existed because the decorator pattern was implemented incorrectly, likely a copy-paste error or misunderstanding of closure scope in Python decorators. Tests were missing for the rate limiting functionality itself.
**Prevention:** Always verify security controls with negative tests (ensure they actually block traffic). Review decorator scope carefully when storing state.

## 2026-01-20 - Unrestricted File Browser Access
**Vulnerability:** The `/api/v1/browser/list` endpoint allowed any authenticated user (e.g., 'viewer') to browse the entire server filesystem by manipulating the `path` query parameter. The endpoint relied on path resolution but lacked authorization checks restricting access to configured directories.
**Learning:** The vulnerability existed because the "viewer" role was granted `browser:read` permission globally, and the implementation assumed that "unrestricted" access for admins meant no checks were needed at all, inadvertently opening it for everyone.
**Prevention:** Implement "allowlist" logic for file access APIs. Ensure that "read" permissions are scoped to specific resources (e.g., Monitored Paths) rather than being global. Always test path traversal with non-admin users.
