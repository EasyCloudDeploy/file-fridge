from app.utils.db_utils import escape_like_string

print(escape_like_string("%"))
print(escape_like_string("_"))
print(escape_like_string("\\"))
