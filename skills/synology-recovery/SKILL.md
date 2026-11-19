---
name: synology-recovery
description: Use this skill when files or folders on a Synology NAS have been deleted or overwritten and need to come back — accidental deletes, a runaway rm/rsync, a failed bulk move, a container that wiped a share, or an agent that deleted through a mounted SMB path. Covers Btrfs share snapshots (Snapshot Replication), snapshot diffing, missing-only restore with reflink clones, verification, and the stale-mount trap that makes a damaged share look emptier than it is. Triggers on "restore from snapshot", "recover deleted files", "undelete", "roll back a share", "accidental delete on the NAS", "rm -rf deleted my files", and "files missing from the NAS". Not for volume-level disasters (dead disk, failed RAID): use Synology's own restore or a backup product for those.
---

# Synology snapshot recovery

Restore what was deleted, prove it with counts and checksums, and leave the
share no worse than you found it. The whole procedure is read-only until one
explicit copy step, so it is safe to run on a live NAS.

## Before anything: measure twice

Two traps make a damaged share look worse — or better — than it is:

1. **Stale mounts.** A macOS SMB mount whose server-side handle is in a bad
   state returns contradictory listings (the same folder showed 12 files, then
   774, then 1011). If the incident involved a mounted path, run
   `diskutil unmount force <mountpoint>` and remount before counting anything.
   Never inventory through a mount you do not trust.
2. **Snapshots are not the live share.** Read them from the NAS itself, not
   through SMB. Snapshot directories are root-only (`d---------`), so plain
   `ls` "proves" they are empty. Use `sudo`.

Also check the obvious first: **the MCP trash and the Recycle Bin**.

- If the delete went through the MCP server's `delete` tool, look in
  `<share>/#trash/<YYYY-MM-DD>/` first. The tool result reports
  `soft_delete: true` and `trash_path`, and the audit trail's
  `delete_placement` lines name the exact location. A file there is a plain
  move away from being restored — no snapshot needed. Use the recorded
  `original_path`, not a path inferred from the layout: basename collisions
  use a unique subdirectory. A custom trash location may also be configured.
- If the share had DSM's Recycle Bin enabled, deleted files are in
  `<share>/#recycle/<path>`. The MCP's `list_directory` on `#recycle` answers
  error 408 when the bin does not exist.

## Step 1 — inventory the snapshots

Snapshots live under `/volume1/@sharesnap/<share>/` and are named by local time:

```bash
sudo ls /volume1/@sharesnap/<share>/
sudo /usr/syno/sbin/synosharesnapshot list <share>
```

Pick the **newest snapshot taken before the damage**. Read the timezone from
each `GMT±HH-…` name; a daily schedule at 00:00 means the snapshot from
the incident day contains the state from the start of that day.

If the newest snapshot is *after* the deletion, walk back one day at a time
until the entry count matches what the user expects. Do not restore from a
snapshot that already contains the loss.

## Step 2 — diff before you copy

Counts first, then names. This is the step that tells you whether you are
looking at 12 lost files or 2 TB.

```bash
S=/volume1/@sharesnap/<share>/<snapshot>
# file lists (paths are relative, so sort/comm line up)
sudo bash -c 'cd -- "$1" && find . -type f | sort' bash "$S" > /tmp/snap.txt
sudo bash -c 'cd -- "$1" && find . -type f | sort' bash "/volume1/<share>" > /tmp/live.txt
wc -l /tmp/snap.txt /tmp/live.txt
comm -23 /tmp/snap.txt /tmp/live.txt | wc -l   # missing from live
comm -23 /tmp/snap.txt /tmp/live.txt | head    # what exactly
```

Interpret before acting:

- **Only Synology metadata differs** (`@eaDir/`, `SYNO@.fileindexdb`)? Then no
  user data was lost — stop, and look elsewhere.
- **Missing files that the user intentionally removed or renamed** (a
  conversion, a re-upload) must not be restored: check the names against recent
  work before copying. A blanket restore resurrects deleted-but-intended files.
- **Everything missing at once** points at a recursive delete, which is the
  case this skill is for.

Size the job with the same paths:

```bash
sudo du -sh --apparent-size "$S/<subpath>"
```

## Step 3 — restore missing-only, with reflink clones

On Btrfs a snapshot and its live share can support copy-on-write clones.
Use `cp --reflink=always` to require a clone: an unsupported or cross-filesystem
copy fails instead of silently allocating the full dataset. Clones share data
blocks initially; metadata and later writes still consume space.

Restore **only what is missing**. Review the exact diff against the user's
requested recovery scope before writing. Live files may be newer. Run while
other agents and library jobs are not changing the affected paths. `-n` prevents
clobbering an existing destination; reject symlink parents before copying.

```bash
S=/volume1/@sharesnap/<share>/<snapshot>
L=/volume1/<share>

# Validate the live destination and every existing parent before either pass.
check="$L/<subpath>"
while [ "$check" != / ]; do
  [ ! -L "$check" ] || { echo "symlink parent: $check" >&2; exit 1; }
  check=$(dirname "$check")
done
mkdir -p "$L/<subpath>" || exit 1

# Pass A: top-level entries that are gone entirely (preserves dir metadata,
# ownership and permissions, and clones every file inside in one go).
find "$S/<subpath>" -mindepth 1 -maxdepth 1 -print0 |
while IFS= read -r -d '' entry; do
  name=$(basename "$entry")
  [ -e "$L/<subpath>/$name" ] || [ -L "$L/<subpath>/$name" ] ||
    cp -an --reflink=always "$entry" "$L/<subpath>/" || exit 1
done

# Pass B: files missing inside directories that still exist (merge, never
# overwrite). -print0/read -d '' keeps CJK, spaces and emoji intact.
find "$S/<subpath>" -mindepth 1 -type f -print0 |
while IFS= read -r -d '' file; do
  rel="${file#$S/<subpath>/}"
  [ -e "$L/<subpath>/$rel" ] || [ -L "$L/<subpath>/$rel" ] || {
    destination="$L/<subpath>/$rel"
    parent=$(dirname "$destination")
    check="$parent"
    while [ "$check" != "$L" ]; do
      [ ! -L "$check" ] || { echo "symlink parent: $check" >&2; exit 1; }
      check=$(dirname "$check")
    done
    mkdir -p "$parent" || exit 1
    cp -an --reflink=always "$file" "$destination" || exit 1
  }
done
```

Run it under `sudo` (snapshots are root-only) and keep the log:

```bash
sudo bash /tmp/restore.sh >> /tmp/restore.log 2>&1
```

Check free space before starting: `df -h /volume1`. If reflink is unavailable,
stop and plan a regular copy with sufficient free space before retrying.

## Step 4 — verify with numbers, not with `ls`

Three checks, cheapest first:

Regenerate `/tmp/live.txt` using the inventory command after the restore;
otherwise the comparison below still measures the pre-restore state. These
newline-delimited reports assume filenames contain no newline characters;
inspect those separately or use a NUL-delimited inventory.

```bash
# 1. same file count, both directions (extra files are as suspicious as missing)
comm -23 /tmp/snap.txt /tmp/live.txt | wc -l
comm -13 /tmp/snap.txt /tmp/live.txt | wc -l

# 2. same apparent size
sudo du -sh --apparent-size "$S/<subpath>" "$L/<subpath>"

# 3. content, on a sample of large files or a whole folder
sudo md5sum "$S/<subpath>/file.mkv" "/volume1/<share>/<subpath>/file.mkv"
```

`@eaDir` differences are normal after a restore: Synology regenerates
thumbnails and its file index, so an aggregate checksum over a whole tree can
differ while every media file matches. Compare media files, not trees, when
both sides contain `@eaDir`.

Finish by re-indexing the media server (Jellyfin `POST /Library/Refresh`, Plex
"Scan Library Files"). Items the server dropped during the outage come back
with the next scan; metadata stored in NFO files inside the folders survives.

## Failure modes

| Symptom | Cause | Action |
|---|---|---|
| `ls` on the snapshot says empty | snapshot dirs are root-only | use `sudo` |
| Contradictory file counts | stale SMB mount or stale handle | `diskutil unmount force`, remount, recount |
| No snapshots for the share | Snapshot Replication not enabled | recover from backup; enable daily snapshots afterwards |
| Restore fills the volume | no reflink (non-Btrfs, or cross-volume) | restore in batches, largest first, or restore only what matters |
| Restored files owned by root | `cp -a` run as root without the source's ownership | `chown -R` to the original owner, or restore with `cp -a` from a snapshot that has the right owner |
| Files reappear that the user deleted on purpose | blanket restore | diff names against recent work first; restore per-path, not per-share |

## Hard rules

- **Never `rm -rf` a mount point.** `~/…/nas`, `/tmp/nasmount`, `/Volumes/…` —
  if something is mounted there, `rm -rf` recurses into the server. Unmount
  first (`diskutil unmount`), then remove the now-empty directory with
  `rmdir`. Never chain `umount …; rm -rf …` — `;` runs the delete even when
  the unmount failed, which is exactly how a share gets wiped.
- **Never restore over existing files.** Missing-only is the whole safety
  argument.
- **Prefer snapshots over the recycle bin when both exist**, and prefer either
  over any "undelete" tool that writes to the same volume.
- Keep the snapshot you restored from until the user confirms the result.

## Optional local mount guard

`scripts/mount-guard-rm` is a macOS/zsh wrapper installed ahead of `/bin/rm` in
PATH. Recursive removal refuses mounts, their parents, and critical system
paths; an unreadable mount table fails closed. It validates every target before
forwarding the command. Direct `/bin/rm`, Python and other deletion tools bypass
it. Use it as a backstop alongside scoped cleanup helpers and client approvals,
not as filesystem isolation.
