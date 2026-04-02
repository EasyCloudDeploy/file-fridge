import re


def validate_hex_color(value: str) -> str:
    if value is None:
        return value
    if not re.fullmatch(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$", value):
        msg = "Invalid hex color format"
        raise ValueError(msg)
    return value

try:
    print(validate_hex_color("#123456"))
    print(validate_hex_color("#FFF"))
    print(validate_hex_color("#123456<script>"))
except ValueError as e:
    print(f"Error: {e}")
