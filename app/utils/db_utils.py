"""Database utilities."""


def escape_like_string(value: str, escape: str = "\\") -> str:
    """
    Escape special characters in a string for use in a SQL LIKE query.

    Args:
        value: The string to escape.
        escape: The escape character to use (default: backslash).

    Returns:
        The escaped string.
    """
    return (
        value.replace(escape, escape + escape).replace("%", escape + "%").replace("_", escape + "_")
    )
