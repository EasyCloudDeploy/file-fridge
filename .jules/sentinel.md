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

## 2026-02-12 - [HIGH] Fix Rate Limit Bypass via Header Spoofing
**Vulnerability:** The rate limiting logic in `app/utils/rate_limiter.py` relied on `X-Forwarded-For` and `X-Instance-UUID` headers to identify clients. This allowed attackers to bypass rate limits (e.g., on the login endpoint) by spoofing these headers, as the application trusted them blindly without verifying they came from a trusted proxy.
**Learning:** Never trust client-provided headers for security-critical controls like rate limiting or authentication unless they are verified. Headers like `X-Forwarded-For` are easily spoofed. Application logic should rely on the `request.client.host` which is populated by the ASGI server (Uvicorn), and proper proxy configuration should be handled at the infrastructure/server level, not the application level.
**Prevention:** Use `request.client.host` exclusively for IP-based identification in application logic. Configure the ASGI server to handle trusted proxies if necessary.

## 2026-03-08 - [HIGH] Fix Username Enumeration via Timing Attack

**Vulnerability:** The `authenticate_user` function returned early if a username was not found in the database, skipping the bcrypt password verification. This allowed an attacker to enumerate valid usernames by measuring the response time (which would be significantly faster for invalid usernames).
**Learning:** Security-critical functions like login must execute in near-constant time regardless of whether the provided identity exists or not.
**Prevention:** Always perform a dummy bcrypt hash verification (or similar computationally expensive operation) when a user is not found, ensuring the total processing time remains consistent.

## 2026-03-18 - [HIGH] Fix Stored XSS / Information Disclosure in API Error Responses
**Vulnerability:** The tag creation endpoint (`/api/v1/tags`) returned an HTTP error detail containing unsanitized user input (`tag.name`) when attempting to create a duplicate tag, which could result in reflected/stored XSS and information disclosure.
**Learning:** Any user input reflected in an API response, even within an error message or exception detail, can pose an injection or XSS risk. Input should not be echoed back to the user blindly.
**Prevention:** Avoid reflecting user input in error messages. Log the event server-side with proper sanitization (e.g., using `sanitize_for_log`) while returning generic error messages to the client.
## 2026-05-03 - [HIGH] Fix Command-Line Argument/Option Injection
**Vulnerability:** The application executed a system call to `xattr` via `subprocess.run` passing an arbitrary `file_path` as the last argument without the POSIX `--` delimiter (`["xattr", "-p", "com.apple.lastuseddate#PS", str(file_path)]`). A filename beginning with `-` could be interpreted as an unintended command-line option flag instead of a file path, leading to Command-Line Argument/Option Injection (CWE-88).
**Learning:** Utilities operating on filenames might interpret those starting with `-` as flags/options. This can result in denial of service, errors, or unexpected behavior.
**Prevention:** Always insert the `--` delimiter before positional filename arguments in shell/subprocess commands (e.g., `["xattr", "-p", "com.apple.lastuseddate#PS", "--", str(file_path)]`) to ensure everything that follows is treated strictly as an argument/operand.
