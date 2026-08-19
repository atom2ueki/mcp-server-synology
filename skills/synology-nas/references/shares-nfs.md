# Shares & NFS

## Tools

| Tool | Purpose | Required |
|------|---------|----------|
| `synology_create_share` | Create a new shared folder | `share_name`, `vol_path` |
| `synology_nfs_status` | NFS service status & config | — |
| `synology_nfs_enable` | Enable/disable the NFS service | — (`enable`, default `true`) |
| `synology_nfs_list_shares` | List shares with their NFS export rules | — |
| `synology_nfs_set_permission` | Set NFS access for a client on a share | `share_name`, `client_ip` |

All accept `nas_name` / `base_url`.

## Shares vs. directories — the distinction

A **share** is a top-level mount point (Photos, video, homes, downloads). It's an admin-level construct: it has its own permissions, optional encryption, optional quota, and is what shows up under `/` to clients. Creating one requires admin privileges and uses `synology_create_share`.

A **directory under a share** (e.g., `/Photos/2026/april`) is just a folder. Use `create_directory` from File Station — see [files.md](files.md).

If the user says "create a share" or "add a new share", they want `synology_create_share`. If they say "create a folder", they want `create_directory`. When ambiguous ("create a Photos area"), ask.

## Creating a share

```
synology_create_share(
  share_name="projects",
  vol_path="/volume1",
  description="Engineering projects",
)
```

`share_name` and `vol_path` are both required — `vol_path` is the volume the share lands on (`/volume1`, `/volume2`, …). Optional fields: `description`, `enable_recycle_bin` (default `true`), `recycle_bin_admin_only` (default `true`). Surface DSM's response — it includes the new share's path.

## NFS workflow

Most users won't use NFS unless they're mounting the NAS from Linux, macOS, or another NAS. The flow:

1. **Check status**: `synology_nfs_status` — is the service even on?
2. **Enable if needed**: `synology_nfs_enable(enable=true)`. Idempotent — calling on an already-enabled service is harmless. Pass `nfs_v4=true` (default `false`) if the client needs NFSv4.
3. **List shares**: `synology_nfs_list_shares` — see what's currently exported and to which clients.
4. **Set permission**: `synology_nfs_set_permission` to add/modify a client rule on a specific share.

### NFS permission shape

A permission rule binds a share to a client — `client_ip`, a single host or a CIDR subnet (`'10.0.0.5'`, `'192.168.1.0/24'`) — with a privilege set:

- **`privilege`**: `readwrite` (default) or `readonly`. There is no deny value — to take away a client's NFS access you delete its rule in DSM (Control Panel → Shared Folder → Edit → NFS Permissions), not by setting a "no access" privilege here.
- **`squash`**: `root_squash` (default — root mapped to nobody), `no_root_squash` (root on the client keeps root on the NAS), `all_squash` (everyone mapped to nobody). Only override the default if the user has a specific reason (e.g., a client that must write as root).
- **`security`**: `sys` (default), `krb5`, `krb5i`, `krb5p` if Kerberos is configured. Stick with `sys` unless the user mentions Kerberos.

When in doubt, mirror what's already exported on a similar share — `synology_nfs_list_shares` shows existing rules.

## DSM 7.3.2 note

Earlier MCP versions had bugs creating shares on DSM 7.3.2 (additional-param encoding, missing `SynoToken` headers). These are fixed in current builds. If the user reports share creation failing on DSM 7.3.2, first check that they're on the latest MCP version — don't reinvent the workaround. Issue history: project commits `f62088d`, `5886007`, `9729915`.

## Gotchas

- **NFS service is off by default** on fresh DSM installs. If `synology_nfs_set_permission` fails, check `synology_nfs_status` first.
- **Firewall**: even with NFS enabled and permissions set, the DSM firewall may block port 2049. The MCP can't toggle the firewall — direct the user to DSM's Control Panel → Security → Firewall if mounts time out.
- **Permission changes are immediate** — no need to restart the service.
- **Share names are case-sensitive on the wire** but DSM presents them case-insensitively in the UI. Use the exact name returned by `synology_nfs_list_shares` to avoid mismatches.
- **Don't set `all_squash` casually** — it's useful for guest-style public shares but breaks per-user ownership. The default `root_squash` is what most users want.

## Examples

### "Mount the photos share on my Linux box at 192.168.1.50, read-only"

```
synology_nfs_status                                  # confirm enabled
synology_nfs_set_permission(
  share_name="Photos",
  client_ip="192.168.1.50",
  privilege="readonly",
  squash="root_squash",
  security="sys",
)
```

Then tell the user the mount command they'd run on the client:

```
sudo mount -t nfs <nas-ip>:/volume1/Photos /mnt/photos -o ro,nfsvers=4
```

### "Open NFS access to the whole subnet"

Use a CIDR string for `client_ip`:

```text
synology_nfs_set_permission(
  share_name="backups",
  client_ip="192.168.1.0/24",
  privilege="readwrite",
  squash="root_squash",
)
```

Confirm with the user before opening RW to a whole subnet — it's a meaningful trust decision.

### "Create a new share for project files"

```
synology_create_share(
  share_name="projects-2026",
  vol_path="/volume1",
  description="2026 project work",
)
```

If the user wants NFS on it too, follow up with `synology_nfs_set_permission`.
