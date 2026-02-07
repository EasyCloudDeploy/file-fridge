## 2026-01-17 - Broken Security Decorator
**Vulnerability:** The `rate_limit` decorator in `app/utils/rate_limiter.py` instantiated the `RateLimiter` class *inside* the wrapper function. This meant a new limiter was created for every request, rendering the rate limit ineffective (state was never preserved).
**Learning:** It existed because the decorator pattern was implemented incorrectly, likely a copy-paste error or misunderstanding of closure scope in Python decorators. Tests were missing for the rate limiting functionality itself.
**Prevention:** Always verify security controls with negative tests (ensure they actually block traffic). Review decorator scope carefully when storing state.

## 2026-01-20 - Unrestricted File Browser Access
**Vulnerability:** The `/api/v1/browser/list` endpoint allowed any authenticated user (e.g., 'viewer') to browse the entire server filesystem by manipulating the `path` query parameter. The endpoint relied on path resolution but lacked authorization checks restricting access to configured directories.
**Learning:** The vulnerability existed because the "viewer" role was granted `browser:read` permission globally, and the implementation assumed that "unrestricted" access for admins meant no checks were needed at all, inadvertently opening it for everyone.
**Prevention:** Implement "allowlist" logic for file access APIs. Ensure that "read" permissions are scoped to specific resources (e.g., Monitored Paths) rather than being global. Always test path traversal with non-admin users.

## 2026-01-20 - Log Injection in Security Controls
**Vulnerability:** The fix for path traversal introduced a potential Log Injection vulnerability by logging unsanitized user input (username and file path).
**Learning:** Security controls themselves can introduce new vulnerabilities if they log attacker-controlled data without sanitization. SonarCloud correctly flagged this as a security hotspot.
**Prevention:** Always sanitize user input before logging. Use safe logging practices or explicit sanitization (e.g., removing newlines) for untrusted data.

## 2026-01-20 - Robust Log Sanitization
**Vulnerability:** Simple string replacement () was flagged as insufficient for log injection prevention by SonarCloud.
**Learning:**  is a safer and more robust way to sanitize inputs for logging because it escapes all control characters and provides a clear visual indication of the input type (string representation). Using standard logging formatting () instead of f-strings is also preferred for security and performance.
**Prevention:** Use  for logging untrusted user input.

## 2026-01-20 - Robust Log Sanitization
**Vulnerability:** Simple string replacement was flagged as insufficient for log injection prevention by SonarCloud.
**Learning:**  is a safer and more robust way to sanitize inputs for logging because it escapes all control characters and provides a clear visual indication of the input type (string representation). Using standard logging formatting instead of f-strings is also preferred for security and performance.
**Prevention:** Use  for logging untrusted user input.
## 2026-01-20 - Robust Log Sanitization
**Vulnerability:** Simple string replacement was flagged as insufficient for log injection prevention by SonarCloud.
**Learning:** repr() is a safer and more robust way to sanitize inputs for logging because it escapes all control characters. Using standard logging formatting instead of f-strings is also preferred.
**Prevention:** Use repr() for logging untrusted user input.
