# Login Portal reverse proxy

## Tools

| Tool | Purpose | Required |
|------|---------|----------|
| `synology_reverse_proxy_list` | All entries, with source, destination, HSTS, WebSocket, headers, profile | — |
| `synology_reverse_proxy_get` | One entry by UUID or description | `uuid_or_description` |
| `synology_access_control_profile_list` | Profiles an entry can reference | — |
| `synology_reverse_proxy_create` | Create an entry | `description`, `source_hostname`, `destination_hostname`, `destination_port` |
| `synology_reverse_proxy_update` | Change some fields, keep the rest | `uuid` |
| `synology_reverse_proxy_delete` | **DESTRUCTIVE.** Delete an entry | `uuid`, `confirm=true` |

All accept `nas_name` / `base_url`.

## Shape of an entry

- **source** — the public listener: `protocol` (`HTTP`/`HTTPS`, default `HTTPS`), `hostname` (bare host name, no scheme or path), `port` (default `443`).
- **destination** — where requests go: `protocol` (default `HTTP`), `hostname` (`localhost`, an IP, …), `port`.
- **hsts** — only valid with an HTTPS source.
- **websocket** — adds DSM's `Upgrade`/`Connection` header pair. Prefer this over putting those headers in `custom_headers`.
- **custom_headers** — `[{name, value}]`; values may use nginx variables (`$scheme`, `$remote_addr`, `$proxy_add_x_forwarded_for`).
- **access_control_profile** — a UUID from `synology_access_control_profile_list`. On update, an empty string removes it.
- **description** — DSM has no name field, so this is the entry's handle. It must be unique.

## Working with entries

1. **Look first.** `synology_reverse_proxy_list` before any write. Every entry may be serving a live site; the source host name tells you which.
2. **Create** with only what the user asked for; the defaults match the DSM UI (60 s timeouts, HTTP/1.1 to the destination).
3. **Update** names only the fields that change. Changing `destination_port` on a live entry takes the site down if nothing listens there — confirm with the user.
4. **Delete** needs `confirm=true`. Get the entry first and say which site will stop answering.

## Gotchas

- **Writes are slow.** DSM rewrites and reloads nginx on every create, update and delete. On a small NAS that takes 30–60 s and briefly affects every proxied site. Batch the user's changes into as few writes as possible.
- **A timeout is not a failure.** An error with `outcome_unknown: true` means DSM may have applied the change. List before retrying, or a retried create can leave two entries.
- **Duplicate descriptions are refused** before anything is sent. If DSM itself reports code 4154, an entry for that source already exists — list and update it instead.
- **DSM certificates are separate.** A new HTTPS host name needs a matching certificate in Control Panel → Security → Certificate; this tool does not assign one.
