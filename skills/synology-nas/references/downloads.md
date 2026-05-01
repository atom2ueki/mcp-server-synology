# Download Station

## Tools

| Tool | Purpose | Required |
|------|---------|----------|
| `ds_get_info` | Service info (version, total tasks, settings) | — |
| `ds_list_tasks` | List download tasks with status | — (optional `offset`, `limit`) |
| `ds_create_task` | Add a new task (HTTP, magnet, torrent URL) | `uri` |
| `ds_pause_tasks` | Pause one or more tasks | `task_ids` |
| `ds_resume_tasks` | Resume paused tasks | `task_ids` |
| `ds_delete_tasks` | Remove tasks | `task_ids` |
| `ds_get_statistics` | Current up/down throughput | — |
| `ds_list_downloaded_files` | Files completed by Download Station | — |

All accept `nas_name` / `base_url` for targeting.

## Task lifecycle

1. **Create** — `ds_create_task` with a URI (HTTP URL, magnet link, `https://…/foo.torrent`). Optionally specify `destination` (a folder path under `/volume1/`).
2. **List** — `ds_list_tasks` returns IDs (`dbid_123`), titles, sizes, progress, status (`downloading`, `paused`, `seeding`, `error`, `finished`).
3. **Manage** — pause/resume/delete by ID.
4. **Stats** — `ds_get_statistics` for current bandwidth; `ds_get_info` for total throughput counters.

## Workflow patterns

### Always list before mutating

If the user says "pause the big one" or "remove the failed downloads," call `ds_list_tasks` first, identify the IDs, summarize what you'll do, and confirm. Don't act on guessed IDs.

### Cleaning up completed tasks

`ds_delete_tasks` accepts `force_complete: true` to clean out finished/seeding tasks. Without it, finished tasks may need to stop seeding first. Surface this distinction to the user — "delete and stop seeding" vs. "remove from the list".

### Magnet links

Magnet URIs work as-is. Just paste the full `magnet:?xt=urn:btih:…` string into `uri`. No special handling needed.

### Default destination

If `destination` is omitted, Download Station uses its configured default (typically `/volume1/downloads` or whatever the user set in DSM). Don't override unless the user specified a path.

## Gotchas

- **Task IDs are strings**, not numbers. They look like `dbid_123` or `dbid_abc`. Pass them as JSON strings in the `task_ids` array.
- **`ds_delete_tasks` is permanent**. There's no recycle bin for tasks. Confirm before bulk delete, especially if any tasks have local files the user might want to keep.
- **Deleting a task ≠ deleting its files.** By default DSM keeps the downloaded files; the task entry just disappears. If the user wants files gone too, they need to also `delete` the file paths.
- **Magnet hash → torrent**: it can take 10–60s for DSM to fetch metadata for a magnet. A freshly-created magnet task will show progress 0% with no size for a bit — that's normal, don't report it as "stuck".
- **Authentication required for some sources**: HTTP downloads from sites needing login may fail silently. Check `ds_list_tasks` for `status: "error"`.

## Examples

### "Download this torrent"

```
ds_create_task(uri="magnet:?xt=urn:btih:abc...", destination="/volume1/downloads/movies")
```

### "What's downloading right now?"

```
ds_list_tasks
```

Filter by `status` in your summary — focus on `downloading` and `error`, not the full list.

### "Pause everything"

```
ds_list_tasks  # get IDs first
ds_pause_tasks(task_ids=["dbid_1", "dbid_2", ...])
```

Pause is idempotent — pausing an already-paused task is fine.

### "Clean up finished downloads"

```
ds_list_tasks  # find tasks with status "finished" or "seeding"
ds_delete_tasks(task_ids=[...], force_complete=true)
```

Confirm with the user first if the list is long or includes anything still seeding.
