# Plex Integration (Bidirectional)

File Fridge can keep Plex libraries online while moving cold media off hot storage.

## Modes

| Mode | Description |
|------|-------------|
| `symlink_webhook` (Mode A) | Point Plex at real hot paths. File Fridge freezes with **SYMLINK** placeholders. Play webhooks thaw cold titles. |
| `fridgefs` (Mode B) | Point Plex libraries at a FridgeFS FUSE mount. File Fridge owns open/read and thaws on demand. See [FRIDGEFS.md](FRIDGEFS.md). |

Ship Mode A first. FridgeFS is the deeper integration path for seekable cold reads and remote backends.

## Settings

Configure under **Settings → Plex / FridgeFS**, or via API:

- `GET/PUT /api/v1/plex/settings`
- `POST /api/v1/plex/test`
- `POST /api/v1/plex/prethaw-on-deck`
- `POST /api/v1/plex/sync-watch-state`

Required fields: `enabled`, `base_url` (e.g. `http://plex:32400`), Plex token.

### Path maps

Translate Plex container paths to File Fridge paths:

```json
[{"plex_prefix": "/data/media", "fridge_prefix": "/hot/media"}]
```

### Library maps

```json
[{"section_id": "1", "path_id": 3, "mode": "symlink_webhook"}]
```

## Inbound webhooks

- Native Plex: `POST /api/v1/plex/webhook?token=<webhook_token>`
- Tautulli: same URL, or `POST /api/v1/plex/tautulli?token=...`

Events handled: `media.play`, `media.resume`, `media.stop`, `media.scrobble` (Tautulli `play`/`resume`/`stop`/`watched` normalized).

On play/resume:

1. Resolve `ratingKey` → media part file via PMS
2. Map path → `FileInventory`
3. Update `last_plex_played_at` / `plex_rating_key`
4. Priority thaw if cold
5. Hold a playback lock so scans cannot freeze the active title

## Outbound PMS actions

| File Fridge event | Plex action |
|-------------------|-------------|
| Freeze completed | Optional section/item refresh; optional “Cold Storage” label |
| Thaw completed | Item refresh |
| Scheduled | On Deck / Continue Watching pre-thaw; `lastViewedAt` poll |

## Keep-hot criterion

Add criterion type **`plex_last_played`** (value in **minutes**), same inverse keep-hot semantics as `atime`:

- `plex_last_played < 43200` → keep titles played in the last 30 days hot

Inventory fields: `last_plex_played_at`, `plex_rating_key`, `plex_play_count`.

## Recommended Monitored Path setup (Mode A)

1. Create a monitored path on the Plex library root.
2. Use operation type **SYMLINK** (or storage location operation mode SYMLINK).
3. Add `plex_last_played` (and optionally `mtime` for brand-new downloads).
4. Enable Plex integration + webhook in Plex/Tautulli.

## Security

Webhook endpoints are unauthenticated except for the shared `webhook_token`. Treat the token like a secret and prefer an internal Docker network.