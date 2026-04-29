## 2026-04-29 - [CRITICAL] Command-line Argument Injection via Subprocess

**Vulnerability:** Shell utilities called via `subprocess.run` (like `xattr` or `diskutil`) were interpolating file paths without safeguards. An attacker who controls a filename (e.g. creating a file named `--delete`) could trick these utilities into interpreting the filename as command-line flags (CWE-88), potentially executing unintended actions or exposing information.
**Learning:** Even when `shell=False` is used, arguments that look like options (e.g. starting with `-`) can be parsed as such by the target binary.
**Prevention:** Always use the `--` delimiter if supported (e.g. `['xattr', '-p', 'name', '--', str(file_path)]`) to signal the end of options, or force the path to be absolute (e.g. `str(path.absolute())` for `diskutil`) so it begins with `/` instead of `-`.
