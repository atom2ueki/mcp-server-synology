# Health monitoring

## Tools

| Tool | What it returns |
|------|-----------------|
| `synology_health_summary` | **Aggregate**: system info + utilization + disk health + volume status in one call |
| `synology_system_info` | Model, serial, DSM version, uptime, system temp |
| `synology_utilization` | Real-time CPU, memory, swap, disk I/O |
| `synology_disk_health` | Per-disk: SMART status, model, temp, size |
| `synology_disk_smart` | Detailed SMART attributes for a specific disk |
| `synology_volume_status` | Volumes: status, size, usage, filesystem |
| `synology_storage_pool` | RAID/SHR pools: level, status, member disks |
| `synology_network` | Interface status, transfer rates |
| `synology_ups` | UPS battery, power readings |
| `synology_services` | Installed packages + running status |
| `synology_system_log` | Recent system log entries |

All accept `nas_name` / `base_url`.

## The aggregate-first rule

If the user asks anything like:

- "How's the NAS doing?"
- "Is everything healthy?"
- "Give me a status report"
- "Check the NAS"

→ call `synology_health_summary` once. It returns system info, utilization, disk health, and volume status in a single payload. Anything finer is a follow-up call only if the summary surfaces something interesting.

Use the individual tools when:

- The user asked a specific question (e.g., "what's the CPU at?" → `synology_utilization`).
- You're drilling into something the summary flagged (e.g., disk shows warning → `synology_disk_smart` for that disk).
- The summary tool isn't enough — `storage_pool`, `network`, `ups`, `services`, `system_log` aren't included in the summary.

## Reading the summary

The summary returns four sections. When presenting to a user, scan for these red flags first:

- **System**: temperature unusually high (CPU > 80°C, system > 60°C is worth flagging), uptime extremely short (recent unplanned reboot?).
- **Utilization**: CPU sustained >80%, memory near 100% with high swap, disk I/O wait time elevated.
- **Disk health**: any disk with `status` other than `normal`. SMART warnings are pre-failure indicators — flag them clearly.
- **Volume status**: anything not `normal` (`degraded`, `crashed`, `repairing`). Usage >90% is worth mentioning.

Lead with problems. If everything is green, say so briefly — don't recite every metric.

## Drilling into a problem disk

When `synology_disk_health` shows a disk with warnings:

```
synology_disk_smart(disk_id="<id from disk_health>")
```

This returns the full SMART attribute table. Key attributes for layman explanations:
- `Reallocated_Sector_Ct` — bad sectors remapped. Non-zero is concerning, growing over time is more so.
- `Current_Pending_Sector` — sectors waiting for reallocation. Non-zero is concerning.
- `Offline_Uncorrectable` — sectors that failed to read. Non-zero means data loss risk.
- `Temperature_Celsius` — running hot reduces lifespan.

Don't speculate beyond the data. If a disk is degraded, recommend the user back up critical data and consider a replacement; don't try to "fix" it from inside DSM.

## Storage pools and RAID

`synology_storage_pool` shows the RAID/SHR layer underneath volumes. Useful when:

- A volume is degraded — check the pool to see which disk needs replacing.
- The user is planning capacity changes — pool-level free space matters.
- SHR (Synology Hybrid RAID) is in use and the user wants to understand the layout.

## Logs and services

- `synology_system_log` — recent entries. Useful for "what happened last night?" or "why did it reboot?". Don't fetch unless the user is investigating a specific event.
- `synology_services` — installed packages and running status. Useful when troubleshooting a missing feature ("Is Download Station running? Is Plex installed?").

## Gotchas

- **Don't fan out by default.** Calling 4 individual health tools when `synology_health_summary` would have done is wasteful and slow.
- **Don't speculate on temperature thresholds.** Drive-specific thresholds vary (a WD Red at 50°C is fine; a Seagate Ironwolf at 60°C might be flagged). Report the number and DSM's own status field; let the user decide whether it's concerning unless the value is clearly extreme.
- **`synology_system_log` can be large.** Default limits apply, but explicitly summarize for the user — don't dump raw logs to chat.
- **UPS not present**: `synology_ups` will return an empty/no-device response on NASes without a UPS attached. Don't treat that as an error.

## Examples

### "Health check the NAS"

```
synology_health_summary
```

Then summarize: green check on system/CPU/memory/disks/volumes, or call out anything not normal.

### "One disk is yellow — what's wrong?"

```
synology_disk_health  # find the disk with warning, note its ID
synology_disk_smart(disk_id="<id>")
```

Surface Reallocated_Sector_Ct, Current_Pending_Sector, Offline_Uncorrectable, and temp. Recommend backup + replacement if any of those are non-zero and growing.

### "Is the network slow?"

```
synology_network
```

Returns per-interface transfer rates. Compare against the link speed (1 Gbps = ~125 MB/s ceiling) before declaring a problem.
