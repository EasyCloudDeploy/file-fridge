"""Utilities for input sanitization."""

import re


def sanitize_for_log(input_str: str) -> str:
    """
    Sanitize a string for logging to prevent Log Injection (CWE-117).

    Removes newlines and control characters.
    """
    if not input_str:
        return ""
    # Replace newlines and carriage returns with a space or escaped version
    # Also removing other control characters
    return re.sub(r"[\x00-\x1f\x7f]", lambda m: f"\\x{ord(m.group(0)):02x}", str(input_str))
