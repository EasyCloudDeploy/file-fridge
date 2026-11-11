# FridgeFS — File Fridge virtual filesystem for Plex

FridgeFS is a FUSE filesystem that presents monitored media trees to Plex while File Fridge owns hot/cold bytes.

## Why

SYMLINK mode can still let the kernel follow a cold target if thaw is late. FridgeFS intercepts `open` / `read` / `seek` so:

- Directory listings always succeed (inventory + hot layout)
- Cold open starts a priority thaw
- Reads can come from a hot fd, a cold fd, or an LRU chunk cache
- After thaw completes, subsequent reads splice to the hot file

## Requirements

- Linux host with `/dev/fuse`
- Optional Python deps: `uv sync --extra fridgefs` (installs `pyfuse3` + `trio`)
- System package: `fuse3` (`fusermount3`)
- Capabilities: typically `CAP_SYS_ADMIN` **or** `user_allow_other` in `/etc/fuse.conf` when using `allow_other`

## Docker / Compose

Critical mount propagation (same class of issues as Riven VFS):

1. On the **host**, mark the FridgeFS parent as shared, e.g. `mount --make-rshared /mnt`
2. File Fridge container: bind `/mnt/fridgefs` and `/dev/fuse`, plus `CAP_SYS_ADMIN` (or equivalent)
3. Plex container volume: `:rslave` so it sees the FUSE mount

Example Compose profile:

```yaml
services:
  file-fridge:
    devices:
      - /dev/fuse
    cap_add:
      - SYS_ADMIN
    security_opt:
      - apparmor:unconfined
    volumes:
      - /mnt/fridgefs:/mnt/fridgefs:rshared
      - ./data:/data

  plex:
    volumes:
      - /mnt/fridgefs:/mnt/fridgefs:rslave
    depends_on:
      - file-fridge
```

Startup order: File Fridge mounts FridgeFS → then start Plex.

## Configuration

Settings → Plex / FridgeFS, or API:

- `fridgefs_enabled`
- `fridgefs_mount_path` (default `/mnt/fridgefs`)
- `fridgefs_cache_max_gb`
- `fridgefs_allow_other`

Mount/unmount:

- `POST /api/v1/plex/fridgefs/mount`
- `POST /api/v1/plex/fridgefs/unmount`
- `GET /api/v1/plex/fridgefs/status`

Library maps with `"mode": "fridgefs"` select which monitored paths appear under the mount.

## Playback policy (default)

1. `open` on a cold title → async thaw to hot
2. Serve ranged reads from cold / chunk cache until thaw completes
3. `release` updates `last_plex_played_at`

## Recovery

Stale mounts after a crash:

```bash
fusermount3 -u /mnt/fridgefs
# or
fusermount -u /mnt/fridgefs
```

File Fridge also attempts cleanup on mount and shutdown.

## Kubernetes

FridgeFS needs privileged (or carefully scoped) pods with `/dev/fuse` and hostPath mounts. Prefer Mode A (`symlink_webhook`) on constrained clusters; use FridgeFS on nodes where FUSE is allowed.

## What FridgeFS is not

- Not a full SMB/NFS editing share
- Not required for non-Plex paths
- Not a replacement for Plex’s metadata database
