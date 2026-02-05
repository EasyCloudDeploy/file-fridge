## 2026-01-17 - Broken Security Decorator
**Vulnerability:** The `rate_limit` decorator in `app/utils/rate_limiter.py` instantiated the `RateLimiter` class *inside* the wrapper function. This meant a new limiter was created for every request, rendering the rate limit ineffective (state was never preserved).
**Learning:** It existed because the decorator pattern was implemented incorrectly, likely a copy-paste error or misunderstanding of closure scope in Python decorators. Tests were missing for the rate limiting functionality itself.
**Prevention:** Always verify security controls with negative tests (ensure they actually block traffic). Review decorator scope carefully when storing state.

## 2026-01-20 - Unrestricted File Browser
**Vulnerability:** The `list_directory` endpoint in `app/routers/api/browser.py` allowed any authenticated user (including "viewers") to browse the entire server filesystem, not just monitored paths.
**Learning:** The endpoint was documented as "unrestricted (admins can browse anywhere)" but failed to implement checks for non-admin users. It assumed authentication equals authorization for the whole system.
**Prevention:** Implement "deny by default" access control. Explicitly check roles and restrict file access to known/safe paths (allowlisting) for non-privileged users. Always verify access controls with tests that attempt unauthorized access.
