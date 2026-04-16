# Configuration Best Practices

## Scan Interval vs Criteria Threshold

### The Relationship

The **scan interval** and **time-based criteria thresholds** must be properly aligned for the system to work effectively.

### Rule of Thumb

**Scan interval should be ≤ 1/3 of your smallest time-based criterion threshold**

### Why This Matters

Files age between scans. If your scan interval is too long, files can age past your threshold before being detected.

### Examples

#### BAD Configuration
```
Criterion:      atime < 3 minutes (keep files accessed in last 3 min)
Scan Interval:  60 minutes
Problem:        Files age 60 minutes between scans
                A file accessed at T+2 won't be scanned until T+60
                By then it's 62 minutes old → moved to cold
                The 3-minute window is completely missed!
```

#### GOOD Configuration
```
Criterion:      atime < 3 minutes
Scan Interval:  1 minute (or less)
Result:         Files checked every minute
                Recently accessed files stay hot as expected
```

#### GOOD Configuration (longer timeframes)
```
Criterion:      atime < 1440 minutes (1 day)
Scan Interval:  360 minutes (6 hours)
Result:         Scan runs 4x per day
                Adequate coverage for daily threshold
```

### Configuration Matrix

| Criterion Threshold | Recommended Scan Interval | Maximum Scan Interval |
|---------------------|---------------------------|----------------------|
| 1-5 minutes         | 1 minute                  | 2 minutes            |
| 5-30 minutes        | 5 minutes                 | 10 minutes           |
| 30-180 minutes      | 10 minutes                | 60 minutes           |
| 3-12 hours          | 60 minutes                | 120 minutes          |
| 12-24 hours         | 120 minutes               | 360 minutes          |
| 1-7 days            | 360 minutes               | 1440 minutes         |
| 1+ weeks            | 1440 minutes              | 10080 minutes        |

## Direct Criteria Evaluation (No Hysteresis)

The system uses **simple, direct criteria evaluation** without hysteresis buffering.

### How It Works

For a criterion like `atime < X`:
- **Evaluation**: Is file age < X? YES or NO
- **Hot storage**: File matches → stays hot
- **Cold storage**: File doesn't match → stays cold
- **No buffer or complicated logic**

### Why No Hysteresis?

Files naturally don't oscillate because:
1. **Timestamps are stable** - They don't change unless files are accessed
2. **Files age in one direction** - They get older, not younger (without user action)
3. **Scanning doesn't modify timestamps** - We use read-only `stat()` operations
4. **Skip logic prevents unnecessary moves** - Files already correctly placed are skipped

### Example Lifecycle

**Criterion: atime < 3 minutes**

```
T+0:  File created (age 0) → matches → HOT
T+2:  Scan runs (age 2) → matches → stays HOT
T+4:  Scan runs (age 4) → doesn't match → moved to COLD
T+6:  Scan runs (age 6) → doesn't match → stays COLD (no oscillation!)
T+8:  User accesses file (age reset to 0)
T+9:  Scan runs (age 1) → matches → moved to HOT
T+11: Scan runs (age 3) → doesn't match → moved to COLD
```

**Key insight:** Without external timestamp changes, files move ONCE when crossing the threshold. They only move back if the user actually accesses them (intended behavior).

## Warnings

The application will log warnings when it detects potential misconfigurations:

```
WARNING: Path 'My Files': Scan interval (60 min) is 20.0x larger than
atime threshold (3 min). Files may age significantly between scans,
reducing effectiveness. Consider reducing scan interval to ~3 min or less.
```

## Network Storage Considerations

### Minimum Buffer for Network Mounts

For SMB/NFS network storage, consider using criteria with thresholds ≥ 30 minutes to ensure adequate buffer for:
- Protocol metadata translation delays
- Network latency
- Clock drift between client and server

### Very Small Thresholds (< 1 minute)

Buffer = 30 seconds may not be sufficient for network storage jitter.

**Recommendation:**
- For local storage: atime < 1 minute is OK
- For network storage: use atime < 30 minutes or longer

## Time Scale Recommendations

### Short-term Active Files (minutes to hours)
**Use case:** Development files, active logs, temporary processing
- **Criteria:** atime < 3 to 60 minutes
- **Scan interval:** 1-10 minutes
- **Operation:** Symlink (for instant access)

### Medium-term Active Files (hours to days)
**Use case:** Recent documents, current projects
- **Criteria:** atime < 360 to 1440 minutes
- **Scan interval:** 60-120 minutes
- **Operation:** Symlink or Move

### Long-term Archive (days to weeks)
**Use case:** Completed projects, old logs, archives
- **Criteria:** mtime > 10080 minutes (1 week)
- **Scan interval:** 360-1440 minutes
- **Operation:** Move or Copy

## Operator Semantics

Remember: **Criteria define what to KEEP in hot storage**, not what to move to cold.

### Common Patterns

```
atime < 3       → Keep recently accessed files (< 3 min) in hot
atime > 1440    → Keep OLD files (> 1 day) in hot (unusual but valid)
mtime < 60      → Keep recently modified files (< 1 hour) in hot
size > 1G       → Keep LARGE files in hot (unusual - usually opposite)
```

### Typical Use Cases

**Keep active files hot:**
```
atime < 1440    (keep files accessed in last day)
mtime < 1440    (keep files modified in last day)
```

**Keep old/large files hot (less common):**
```
atime > 10080   (keep files NOT accessed in last week)
size > 1G       (keep large files in hot storage)
```

## Testing Your Configuration

1. **Set up test path** with short intervals
2. **Create test files** and note timestamps
3. **Wait for scan** or trigger manually
4. **Verify behavior** matches expectations
5. **Adjust intervals** as needed
6. **Check application logs** for warnings

---

## Storage Routing (Multi-Location)

When a monitored path has more than one cold storage location attached to it, File Fridge automatically selects the best destination for each file using a **scored candidate** algorithm. You never need to configure routing rules manually — the system adapts in real time to available space and recent health.

### How a Location Is Selected

For every file that is ready to freeze, the routing service runs these steps:

**Step 1 — Eliminate ineligible locations**

A location is skipped if any of the following are true:
- It fails backend validation (path does not exist, S3 credentials are invalid, Google Drive token cannot be refreshed, etc.)
- Available free space is below the **1 GB minimum threshold**
- Available free space is less than `file_size + 1 MB` (not enough room for this specific file)

**Step 2 — Score surviving candidates**

Each remaining location receives a composite score:

| Component | Formula | Max points |
|---|---|---|
| Space score | `min(50, 50 × (free_GB / 10) ^ 0.5)` | 50 |
| Load score | `max(0, 30 × (1 − file_count / 10 000))` | 30 |
| Error penalty | `−10 × recent_errors` (last 15 minutes) | unbounded |

**Space score** grows logarithmically: a 10 GB drive scores 50, a 40 GB drive also scores 50 (capped). A 2 GB drive scores ~22. This rewards locations with plenty of room without over-weighting enormous drives.

**Load score** spreads files evenly across locations. An empty location scores 30; one holding 5 000 files scores 15; at 10 000+ files the load score reaches zero.

**Error penalty** deprioritizes flaky locations automatically. A location that failed two transfers in the last 15 minutes loses 20 points, effectively steering traffic away until its error window clears.

**Step 3 — Pick the winner**

The location with the highest total score receives the file. Ties are broken by the order locations appear in the database.

### Remote Backends (S3 and Google Drive)

S3 and Google Drive do not expose reliable disk-usage statistics, so they receive a synthetic free-space value of `10^15 bytes` (roughly 1 petabyte). This means their **space score is always 50**. The load score and error penalty then act as the primary differentiators between two remote locations.

Practical consequence: a healthy, lightly-used S3 bucket will always beat a nearly-full local drive (space score ~5), but will lose to a healthy local drive with 50+ GB free (both score 50, but the local drive may win on load if it has fewer files).

### Example: Two Local Locations + One S3 Bucket

| Location | Free space | Files stored | Recent errors | Score |
|---|---|---|---|---|
| Local SSD (500 GB) | 80 GB | 200 | 0 | 50 + 29.4 = **79.4** |
| Local HDD (4 TB) | 2 TB | 50 | 0 | 50 + 29.9 = **79.9** |
| S3 bucket | synthetic | 150 | 1 | 50 + 29.6 − 10 = **69.6** |

The HDD wins by a small margin (fewer files). S3 is penalized for its recent error.

### Configuration Tips

- **Attach multiple locations** to spread the load across drives or mix local and cloud storage.
- **Let errors self-heal** — the 15-minute error grace period clears automatically; no manual intervention is needed.
- **Balance file counts** — if one location consistently wins on space, it will accumulate files until its load score drops and the others become competitive again.
- **Monitor health** via the API endpoint `GET /api/v1/storage/{id}/health` which exposes free space, file count, and recent error counts for each location.

---

## Summary

**DO:**
- Set scan interval ≤ 1/3 of your smallest criterion threshold
- Use realistic time scales (≥ 30 min for network storage)
- Monitor application logs for warnings
- Test with actual files before production

**DON'T:**
- Use scan intervals much larger than criteria thresholds
- Use very small thresholds (< 1 min) on network storage
- Ignore configuration warnings in logs
- Set criteria without understanding the semantics (what to KEEP hot)
