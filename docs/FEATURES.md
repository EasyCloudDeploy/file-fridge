# Feature Rundown

File Fridge is a comprehensive file lifecycle management tool designed to optimize storage usage by intelligently moving files between "hot" (expensive/fast) and "cold" (cheap/slow) storage.

## Core Features

### 1. Path Monitoring
Monitor specific directories on your filesystem. Each monitored path can have its own configuration, including:
- **Scan Interval**: How often the system checks for files that meet movement criteria.
- **Operation Type**: Choose how files are moved (Move, Copy, or Symlink).
- **Indexing Prevention**: Option to create `.noindex` files to prevent macOS Spotlight from updating access times during scans.

### 2. Intelligent Criteria System
Define what files should **STAY** in hot storage. Anything that doesn't match your criteria is considered "cold" and eligible for movement.

File Fridge supports find-compatible criteria:
- **Time-based**: `mtime` (modification), `atime` (access), and `ctime` (change) times in minutes.
- **Size-based**: Filter by file size (supports suffixes: `c` for bytes, `k` for kilobytes, `M` for megabytes, `G` for gigabytes).
- **Pattern Matching**:
    - `name`: Filename (glob patterns)
    - `iname`: Case-insensitive filename
    - `regex`: Regular expression matching on full path
- **Metadata**:
    - `type`: File type (`f`=file, `d`=directory, `l`=link)
    - `perm`: Permissions (octal or symbolic)
    - `user`: File owner (username or UID)
    - `group`: File group (groupname or GID)

### 3. Multiple Operation Types
- **Move**: Standard relocation from hot to cold storage.
- **Copy**: Keeps the original file in hot storage while creating a copy in cold storage.
- **Symlink**: Moves the file to cold storage and leaves a symbolic link at the original location. This allows applications to still "see" the file while the actual data resides on cheaper storage.

### 4. Storage Management
- **Tiered Storage**: Support for multiple cold storage locations.
- **File Inventory**: A centralized database tracking every file across all monitored paths and storage locations.
- **Status Tracking**: Monitor file states: Active, Moved, Deleted, Missing, or Migrating.
- **Checksums**: SHA256 hashing for file integrity and deduplication.

### 5. Advanced Tagging
Organize and categorize your files with a flexible tagging system.
- **Manual Tagging**: Assign tags to files via the Web UI or API.
- **Automated Tag Rules**: Create rules to automatically tag files based on extension, MIME type, size, or name patterns. Rules can be prioritized to handle complex categorization.

### 6. Notifications
Stay informed about the state of your storage system.
- **Providers**: Built-in support for **Email (SMTP)** and **Generic Webhooks**.
- **Event-driven**: Get notified on successful syncs, errors, or when storage space is running low.
- **Filter Levels**: Configure notifications based on severity (Info, Warning, Error).

### 7. Statistics and Visualization
The Dashboard provides insights into your storage efficiency:
- **Capacity Saved**: Total disk space saved by moving files to cold storage.
- **Activity Trends**: Graphs showing file movement over time.
- **Storage Distribution**: Breakdown of hot vs. cold storage usage.

### 8. File Pinning
Need to ensure specific files never leave hot storage regardless of criteria? Use the **Pin** feature to exclude individual files from the automated movement process.

### 9. Management Tools
- **Stats Cleanup**: Automatically prune old statistics and missing file records to keep the database lean.
- **Web UI**: A modern, responsive interface for managing all aspects of the application.
- **REST API**: Fully documented API for integration with other scripts or tools.
- **User Authentication**: Secure access to the management interface.
