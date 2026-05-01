---
name: synology-nas
description: Use this skill for ANY task on a Synology NAS via the mcp-server-synology MCP — managing files and folders on the NAS, searching NAS shares, reading or creating NAS files, running Download Station tasks (torrents, magnets, downloads), checking NAS health (disks, SMART, volumes, RAID, SHR, UPS, CPU/memory utilization), creating shared folders, configuring NFS exports, and managing NAS users, groups, or share permissions. Triggers on "synology", "NAS", "DSM", "DiskStation", "DS220+/DS920+/etc", "Download Station", "/volume1", and any NAS-share path. Use even when the user just says "my NAS" or names a model number without saying "Synology". Distinct from the lifehacker-cert skill, which only handles SSL certs on one specific NAS — this skill is for general NAS operation.
---

# Synology NAS

This skill teaches you how to use the `mcp-server-synology` MCP effectively. The MCP exposes ~50 tools across six domains; the trick is picking the right tool, targeting the right NAS, and not exploding a single user request into a dozen redundant calls.

## Mental model

Three things shape almost every interaction:

1. **Targeting** — most setups have one NAS and you can omit the target argument. Multi-NAS setups require a `nas_name` (or `base_url`) on every tool call. Don't guess; discover first.
2. **State** — auth is session-based and usually auto-managed. You almost never need to call `synology_login` yourself. Check status first; only log in if status says you must.
3. **Aggregation** — several individual tools have a *_summary equivalent. When the user wants a "checkup" or "overview," reach for the aggregate. When they want a specific number, use the targeted tool.

Once those three are settled, the rest is picking the right domain.

## Always start here

Before any operation, run discovery — it's two cheap calls and answers "which NAS?" and "am I logged in?":

1. `synology_list_nas` — lists configured NAS units with names, URLs, connection status.
2. `synology_status` — shows which sessions are active.

If the user mentions a NAS by name (e.g. "the backup NAS", "nas2"), match it to a `nas_name` from `synology_list_nas`. If only one NAS is configured, you can omit `nas_name` on subsequent calls. If multiple are configured and the user is ambiguous, ask which one — don't pick.

## Tool call shape

Every operational tool accepts an optional target argument:

- `nas_name` (preferred) — the identifier from settings.json, e.g. `"nas1"`.
- `base_url` — fallback when the NAS isn't in settings.json.

Pass at most one. Omit both only when there's a single NAS configured.

Path arguments **must start with `/`**. User-facing data lives under `/volume1/` (or `/volume2/`, etc., for additional volumes). The root `/` lists shares; `/homes`, `/photo`, `/video` etc. are share-level mount points underneath. Don't invent paths — list the parent first if unsure.

## Domain map

When the user asks about… → read…

| User says | Domain | Reference |
|-----------|--------|-----------|
| login, logout, session, "who's logged in", multi-NAS, settings.json | auth | [references/auth.md](references/auth.md) |
| list/find/search files, read/create/delete/move/rename files & folders, shares | filestation | [references/files.md](references/files.md) |
| download, torrent, magnet, BT, transmission-style task | Download Station | [references/downloads.md](references/downloads.md) |
| disks, SMART, RAID/SHR, volume, UPS, CPU/memory utilization, system info, "is the NAS healthy" | health | [references/health.md](references/health.md) |
| NFS, share permissions, "let host X mount", create share | shares & NFS | [references/shares-nfs.md](references/shares-nfs.md) |
| users, groups, permissions, "add user", "remove from group" | user management | [references/users.md](references/users.md) |

Read the reference for a domain before doing non-trivial work in it. Each reference covers the tool list, the right call ordering, and known gotchas. Don't read every reference — only the ones you need.

## Cross-cutting patterns

### Prefer aggregate tools for overviews

If the user asks "is the NAS healthy?" or "give me a system report," call `synology_health_summary` once instead of fanning out to `synology_system_info` + `synology_utilization` + `synology_disk_health` + `synology_volume_status`. The summary is one round-trip and returns the same data. Reach for the individual tools only when the user wants one specific reading (e.g. "what's the CPU at right now?").

### Read-only first

When in doubt about a destructive action, list/inspect first:

- Before `delete`: `get_file_info` to confirm the path resolves to what you think.
- Before `ds_delete_tasks`: `ds_list_tasks` to confirm the IDs.
- Before `synology_delete_user`: `synology_get_user` to confirm the account.

This is a real NAS with real data — there is no "undo" for these tools.

### Don't paginate without need

Tools that accept `offset`/`limit` (e.g. `ds_list_tasks`, `search_files`) default to sensible values. Don't add pagination unless the user mentions a large dataset or a previous call returned suspiciously few results.

### Trust auto-login

Auto-login is on by default — the server logs in to every configured NAS at startup. You should expect operational tools to "just work" without calling `synology_login` first. If a tool returns an auth error, *then* call `synology_status` to diagnose, and only call `synology_login` if no session is active. See [references/auth.md](references/auth.md) for the full state machine.

### Multi-step workflows: minimize round-trips

If the user says "list shares and then list the photos folder," do those two calls — don't preface them with a discovery dance. Discovery (`synology_list_nas`/`synology_status`) is for when targeting or auth is genuinely unclear, not as a ritual before every request.

## Anti-patterns to avoid

- **Calling `synology_login` reflexively.** Auto-login almost always handled it.
- **Fanning out to 4 health tools when the user asked "how's the NAS doing?"** Use `synology_health_summary`.
- **Inventing paths like `/Documents/foo` or `~/photos`.** Real paths start with `/volume1/`. List the parent if unsure.
- **Picking a NAS in a multi-NAS setup without confirmation.** Ask which one.
- **Confusing "shares" with "files."** A *share* is the top-level mount (Photos, video, homes); creating one is an admin action and lives in `synology_create_share` (see shares-nfs.md). Creating a *folder under* a share is `create_directory`.
- **Bulk operations without verification.** If the user asks to delete "all old downloads," list them, summarize what you're about to delete, and confirm before calling `ds_delete_tasks` or `delete`.

## Example: a typical session

User says: "Check on my NAS — anything I should worry about?"

Good response:
1. `synology_list_nas` → confirm which NAS (or that there's only one).
2. `synology_health_summary` → one call, full picture.
3. Read the result, surface anything red (degraded volumes, failing disks, high temps), and tell the user.

Avoid:
1. `synology_login` (unnecessary).
2. `synology_system_info`, then `synology_utilization`, then `synology_disk_health`, then `synology_volume_status` (4 calls when 1 suffices).

## When tool descriptions disagree with this skill

The MCP's own tool descriptions sometimes say "from secrets.json" — settings actually live at `~/.config/synology-mcp/settings.json`. Trust this skill over those stale strings. If the user reports an actual auth/config bug, point them at the project README's "Configuration Options" section and don't try to "fix" it from inside Claude.
