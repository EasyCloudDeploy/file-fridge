## 2026-01-17 - Broken Security Decorator
**Vulnerability:** The `rate_limit` decorator in `app/utils/rate_limiter.py` instantiated the `RateLimiter` class *inside* the wrapper function. This meant a new limiter was created for every request, rendering the rate limit ineffective (state was never preserved).
**Learning:** It existed because the decorator pattern was implemented incorrectly, likely a copy-paste error or misunderstanding of closure scope in Python decorators. Tests were missing for the rate limiting functionality itself.
**Prevention:** Always verify security controls with negative tests (ensure they actually block traffic). Review decorator scope carefully when storing state.

## 2026-01-20 - [HIGH] Fix Path Traversal/Arbitrary File Move
**Vulnerability:** The `/api/v1/files/move` endpoint allowed users with `files:write` permission (like "manager" role) to move any file on the server filesystem to any other location, as it lacked validation that the source and destination paths were within the allowed monitored paths.
**Learning:** Endpoints that accept file paths as input must always validate them against a whitelist of allowed directories (Monitored Paths), even if the user is authenticated and has a high-privilege role. Relying solely on role-based permissions (`files:write`) is insufficient for filesystem operations.
**Prevention:** Always use `check_path_permission` (or similar validation logic) for any endpoint that touches the filesystem. This logic enforces that paths are contained within explicitly allowed roots (`MonitoredPath` and `ColdStorageLocation`).

## 2026-02-11 - [CRITICAL] Fix SQL Wildcard Injection in Path Queries

**Vulnerability:** The application used unescaped user input in SQL `LIKE` queries for file path filtering (e.g., `.like(f"{path}%")`). This allowed wildcard injection where `%` in a path could match unintended files. Additionally, missing trailing slashes in prefix queries allowed partial directory matches (e.g., `/data/cold` matching `/data/cold_backup`).
**Learning:** Even when using an ORM, logic involving `LIKE` operators requires careful handling of wildcards. SQLAlchemy's `startswith()` might not always behave as expected regarding escaping across all drivers/dialects, making explicit `LIKE ... ESCAPE` safer for security-critical logic.
**Prevention:** Always use `app.utils.db_utils.escape_like_string` when constructing `LIKE` queries with user input. Ensure directory prefix matches always include a trailing slash.

## 2026-02-12 - [MEDIUM] Fix Directory Enumeration Vulnerability
**Vulnerability:** The `/api/v1/browser/list` endpoint checked for file existence before checking user permissions. This allowed an attacker to distinguish between existing and non-existing files/directories outside their allowed scope by observing the difference between 403 Forbidden and 404 Not Found responses.
**Learning:** Security checks (authorization) must always be performed *before* any resource access or existence checks. The order of operations in API handlers is critical for preventing side-channel attacks like enumeration.
**Prevention:** Always place permission checks at the very beginning of the request handling logic, before interacting with the resource (database, filesystem, etc.). Ensure that access denied responses are identical regardless of resource existence.

## 2026-02-14 - [MEDIUM] Fix Sensitive Information Leak in Error Responses
**Vulnerability:** The `/api/v1/browser/list` and `/api/v1/files` endpoints were catching generic exceptions and returning the raw exception message (`str(e)`) to the client in the HTTP response. This could expose sensitive internal details like file paths or database errors.
**Learning:** Broad exception handling that returns the exception message is a common anti-pattern. Developers often do this for debugging convenience but forget to remove it for production.
**Prevention:** Always catch specific exceptions where possible. for generic `Exception`, log the full error with a stack trace server-side, but return a generic "Internal server error" message to the client.
