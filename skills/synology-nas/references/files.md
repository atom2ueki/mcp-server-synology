# File Station: files & directories

## Tools

| Tool | Purpose | Required |
|------|---------|----------|
| `list_shares` | List top-level shares (Photos, video, homes, …) | — |
| `list_directory` | List contents of a path | `path` |
| `get_file_info` | Detailed metadata for one file/dir | `path` |
| `get_file_content` | Read strict UTF-8 text or lossless base64 | `path` |
| `search_files` | Case-insensitive name substring search under a path | `path`, `pattern` |
| `create_file` | Create a new file with content | `path` |
| `create_directory` | Make a new folder | `folder_path`, `name` |
| `rename_file` | Rename in-place | `path`, `new_name` |
| `move_file` | Move (or rename across paths) | `source_path`, `destination_path` |
| `copy_file` | Copy one regular file server-side into an existing folder | `source_path`, `destination_folder` |
| `delete` | Delete file or directory (auto-detects type) | `path` |

All accept the optional `nas_name` / `base_url` target.

## Path conventions

- Every path **must start with `/`**. Relative paths are rejected.
- The root `/` is empty for file operations — it's the share index. Use `list_shares` to enumerate.
- Real data lives under `/volume1/...` (or `/volume2/...`, etc., on multi-volume systems). The shares like `Photos`, `homes`, `video` are accessible at `/Photos`, `/homes`, `/video` — they're symlinks/mounts for the underlying `/volume1/Photos` etc.
- When in doubt, `list_directory` the parent before constructing a child path. NAS layouts vary by user.

## Workflow patterns

### Search before you operate

If the user says "delete all the .DS_Store files in Photos", don't try to walk the tree manually. Use `search_files`:

```
search_files(path="/Photos", pattern=".DS_Store")
```

Then summarize what you found and confirm before bulk-deleting.

### Create a directory tree

`create_directory` accepts `force_parent: true` to create intermediate parents. Use it when the user gives you a deep path that doesn't exist yet — saves multiple round-trips:

```
create_directory(folder_path="/volume1/projects", name="2026/q2/budgets", force_parent=true)
```

### Reading file content

`get_file_content` defaults to strict UTF-8 text with a 1 MiB limit. For binary data, use `encoding: "base64"`; the result includes the raw size, MIME type, SHA-256 and lossless base64 content. Raise `max_bytes` only when needed, up to the 8 MiB hard limit. For larger files, use `get_file_info` if the user only wants metadata, or `copy_file` when the bytes should stay on the NAS. Don't dump file content to chat unless the user asked to see it.

### Move vs. rename

- `rename_file` keeps the file in the same directory. Use for "rename foo.txt to bar.txt".
- `move_file` changes the path. It also handles renaming across directories — so for "move foo.txt to /archive/foo-old.txt", use `move_file`, not `rename_file` followed by `move_file`.
- `copy_file` preserves the source name and copies only regular files into an existing destination directory. It verifies the resulting path and byte count without sending the file through the model.

## Gotchas

- **`delete` is recursive on directories.** It auto-detects file vs. directory. Confirm with the user before deleting anything that looks like a folder, especially if it's not empty.
- **Overwrite is opt-in.** `create_file`, `move_file` and `copy_file` default `overwrite: false`. If the user says "replace the existing one", set `overwrite: true` explicitly — don't silently fail and retry.
- **A server-side copy is not a database snapshot.** `copy_file` is byte-correct after completion, but a live SQLite database can change while it is copied. Use SQLite's online backup mechanism when consistency matters.
- **Trash isn't automatic.** `delete` is permanent unless DSM Recycle Bin is enabled on the share. Mention this if the user is deleting something irreplaceable.
- **Case sensitivity** depends on the underlying filesystem (Btrfs/ext4 are case-sensitive, eCryptfs may differ). Don't assume.
- **Path encoding**: paths with spaces, accents, or non-ASCII characters work fine; just pass them as-is in the JSON. Don't URL-encode.

## Examples

### "What's in my Photos folder?"

```
list_directory(path="/Photos")
```

### "Find all PDFs in /volume1/documents"

```
search_files(path="/volume1/documents", pattern="*.pdf")
```

### "Save these notes to my homes folder"

```
create_file(
  path="/homes/<user>/notes/2026-04-29.md",
  content="...",
  overwrite=false
)
```

If the parent dir doesn't exist, you'll get an error — fall back to `create_directory(force_parent=true)` first, then retry.

### "Move last month's photos into the archive"

```
search_files(path="/Photos", pattern="*.jpg") # to find them
# then per-file:
move_file(source_path="/Photos/2026-03-01.jpg", destination_path="/Photos/archive/2026-03/2026-03-01.jpg", overwrite=false)
```

For batch moves, surface a summary first and confirm before iterating.
