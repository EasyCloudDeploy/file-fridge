# Access Time (atime) Verification

## Does File Fridge Update atime When Scanning?

**Short Answer: NO** - The application only uses metadata operations that do not update atime.

## File Operations Analysis

### Operations Used by File Fridge

1. **`stat()` / `Path.stat()`** - ✅ SAFE
   - Gets file metadata (size, timestamps, permissions)
   - Does NOT open or read the file
   - Does NOT update atime
   - Used in: `file_scanner.py`, `criteria_matcher.py`

2. **`scandir()`** - ✅ SAFE
   - Efficient directory listing
   - Returns DirEntry objects with cached stat info
   - Does NOT update atime
   - Used in: `file_scanner.py`

3. **`Path.resolve()`** - ✅ SAFE
   - Resolves symlinks to target path
   - Does NOT read file contents
   - Does NOT update atime
   - Used in: `file_scanner.py`

4. **`mdls` (macOS only)** - ✅ SAFE
   - Queries Spotlight metadata
   - Does NOT update atime
   - Used in: `criteria_matcher.py` for "Last Open" time

### Operations NOT Used (that would update atime)

- ❌ `open()` - NOT USED
- ❌ `read()` - NOT USED
- ❌ Reading file contents - NOT USED

## Verification Script

To verify that scanning doesn't update atime, run this test:

```bash
#!/bin/bash
# Save as: verify_atime.sh

# Create test file
TEST_FILE="/tmp/atime_test.txt"
echo "test content" > "$TEST_FILE"

# Wait a moment
sleep 2

# Get initial atime
ATIME_BEFORE=$(stat -f %a "$TEST_FILE" 2>/dev/null || stat -c %X "$TEST_FILE")
echo "atime before: $ATIME_BEFORE"

# Simulate what File Fridge does - stat the file
python3 -c "from pathlib import Path; Path('$TEST_FILE').stat()"

# Get atime after stat
ATIME_AFTER=$(stat -f %a "$TEST_FILE" 2>/dev/null || stat -c %X "$TEST_FILE")
echo "atime after:  $ATIME_AFTER"

# Compare
if [ "$ATIME_BEFORE" = "$ATIME_AFTER" ]; then
    echo "✅ PASS: atime NOT updated by stat()"
else
    echo "❌ FAIL: atime WAS updated (check filesystem mount options)"
fi

# Now read the file (which SHOULD update atime)
cat "$TEST_FILE" > /dev/null

ATIME_AFTER_READ=$(stat -f %a "$TEST_FILE" 2>/dev/null || stat -c %X "$TEST_FILE")
echo "atime after read: $ATIME_AFTER_READ"

if [ "$ATIME_AFTER" != "$ATIME_AFTER_READ" ]; then
    echo "✅ atime correctly updated by read operation"
else
    echo "⚠️  atime NOT updated by read (filesystem may be mounted with noatime/relatime)"
fi

# Cleanup
rm "$TEST_FILE"
```

## Filesystem Mount Options Impact

While File Fridge doesn't update atime, the **filesystem mount options** affect how atime is tracked:

### 1. `strictatime` (Rarely Used)
- Updates atime on EVERY file access
- Significant performance impact
- Most accurate for access tracking
- **Recommendation:** Only if precise atime tracking is critical

### 2. `relatime` (Default on Modern Linux)
- Updates atime only if:
  - Previous atime is older than mtime or ctime, OR
  - Previous atime is more than 24 hours old
- Good balance of performance and tracking
- **Recommendation:** ✅ GOOD for File Fridge

### 3. `noatime` (Best Performance)
- NEVER updates atime
- Best performance (no write overhead)
- **Recommendation:** ⚠️ DO NOT USE if you need atime-based criteria

### 4. `lazytime` (Linux 4.0+)
- Delays atime updates to memory
- Writes to disk less frequently
- **Recommendation:** ✅ GOOD for File Fridge

## macOS Considerations

macOS uses a different approach:

1. **Standard atime:** Updated according to mount options (similar to Linux)

2. **Spotlight "Last Open" Time:**
   - Tracked via extended attributes (kMDItemLastUsedDate)
   - Updated when files are opened by applications
   - File Fridge queries this via `mdls` (read-only operation)
   - More reliable than atime for user-initiated access

### How File Fridge Handles macOS Files

File Fridge on macOS uses **BOTH** atime and "Last Open" time with special logic:

1. **If "Last Open" exists:** Use the most recent of (atime, Last Open)
2. **If "Last Open" is `None`:** File has NEVER been opened by user
   - **Treated as "infinitely old"** (Unix epoch: Jan 1, 1970)
   - Does NOT fall back to `atime`
   - This ensures unopened files are moved to cold storage

### Why This Matters

**Problem:** Files that have never been opened can have recent `atime` values due to:
- Recent file creation or copy
- System processes (backup, indexing, anti-virus)
- Filesystem operations

**Solution:** When "Last Open" is `None`, File Fridge knows the file was never opened by a user and treats it as very old, ensuring it's moved to cold storage.

**Example:**
```
File: document.pdf
Created: Today
Last Open: None (never opened)
atime: Today (set at creation)

Without fix: atime = Today → Kept in hot storage ❌
With fix: Last Open = None → Treated as epoch → Moved to cold ✅
```

## Network Mounts (SMB/NFS)

For network mounts, atime behavior varies:

### SMB (Samba/CIFS)
- Server controls atime behavior
- Client may cache timestamps
- Check server mount options

### NFS
- Supports atime but may be disabled for performance
- Check both client and server mount options
- Common settings: `noatime`, `relatime`

### Recommendation for Network Mounts
If using atime-based criteria with network mounts:
1. Verify atime is enabled on the server
2. Test with the verification script above
3. Consider using mtime or ctime instead if atime is unreliable

## Best Practices

### For Reliable atime-based Criteria:

1. **Check Mount Options:**
   ```bash
   # Linux
   mount | grep your_mount_point

   # macOS
   mount | grep your_mount_point
   ```

2. **Verify atime Updates:**
   - Run the verification script above
   - Access a test file manually
   - Check if atime changes

3. **Alternative Criteria:**
   - If atime is unreliable, use `mtime` (modification time) instead
   - `mtime` is always updated when file content changes
   - Less susceptible to filesystem mount options

4. **macOS Users:**
   - File Fridge automatically uses "Last Open" time as fallback
   - More reliable for tracking actual user access
   - Works even if atime tracking is disabled

## Conclusion

✅ **File Fridge does NOT update atime during scanning**

The application only uses metadata operations (`stat()`) that do not trigger atime updates. However, verify your filesystem mount options to ensure atime is being tracked as expected for your use case.

For critical atime-based criteria, run the verification script to confirm your filesystem configuration is appropriate.
