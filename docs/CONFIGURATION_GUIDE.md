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
