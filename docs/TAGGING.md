# Tagging and Tag Rules

File Fridge allows you to organize your files using a flexible tagging system. Tags can be applied manually or automatically based on rules.

## Tags

A tag consists of:
- **Name**: A unique label (e.g., "Project-Alpha", "Archived").
- **Description**: An optional explanation of the tag's purpose.
- **Color**: A hex color code used for visual identification in the Web UI.

Tags are visible in the File Browser and can be used to filter and search for specific files.

## Manual Tagging

You can add or remove tags from individual files through the **File Browser** in the Web UI. Simply select a file and use the "Tags" menu to manage its labels.

## Automatic Tag Rules

Tag Rules allow you to automatically apply tags to files as they are scanned or indexed.

### Rule Criteria

Rules can be based on several file attributes:

1. **Extension**: Match files by their extension (e.g., `.pdf`, `.mp4`).
2. **Path Pattern**: Match files based on their directory path using glob patterns (e.g., `**/logs/**`) or Regular Expressions.
3. **MIME Type**: Match by content type (e.g., `image/*`, `application/zip`).
4. **Size**: Match based on file size (e.g., `> 1GB`, `< 10MB`).
5. **Name Pattern**: Match based on the filename only (e.g., `backup_*`).

### Operators

Depending on the criterion type, you can use various operators:
- Equals (`=`)
- Greater Than / Less Than (`>`, `<`, `>=`, `<=`)
- Contains
- Matches (Glob)
- Regex

### Priority

Rules have a **Priority** value. If multiple rules match a single file, they are applied in order of priority (higher numbers first). This is useful for complex tagging logic where one tag might imply or override another.

### Applying Rules

Rules are automatically evaluated during file scans. You can also manually trigger a "Re-tagging" process from the Tags page to apply new rules to existing files in the inventory.
