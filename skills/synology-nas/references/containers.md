# Container Manager

## Tools

| Tool | Purpose | Required |
|------|---------|----------|
| `synology_container_list` | List containers | — |
| `synology_container_get` | Inspect one container | `name` |
| `synology_container_start` | Start a container | `name` |
| `synology_container_stop` | Stop a container | `name` |
| `synology_container_restart` | Restart a container | `name` |
| `synology_container_delete` | Delete a container | `name` |
| `synology_container_logs` | Read container logs | `name` |
| `synology_container_resource` | Current resource usage | `name` |
| `synology_container_project_*` | List/get/create/update/start/stop/restart/build/clean/delete compose projects | varies |
| `synology_container_image_*` | List/get/delete/pull local images | varies |
| `synology_container_registry_*` | List/search/tags/download registry images | varies |
| `synology_container_network_*` | List/get/create/delete networks | varies |

All accept `nas_name` / `base_url`.

## Workflow patterns

### Inspect before mutating

For start/stop/restart/delete, first call `synology_container_get(name=...)` or `synology_container_list`. Container names are DSM Container Manager names, not image names.

### Projects are compose projects

Use project tools when the user talks about a compose app, stack, or project folder. `project_create` needs:

```
name
share_path
content  # Docker Compose YAML
```

`project_update` finds the DSM project by `name` and replaces the compose `content`. List or get the project first if the user is unsure which project they mean.

### Images vs registries

- `synology_container_image_*` works on local images already on the NAS.
- `synology_container_registry_*` searches/downloads images from the active registry.
- `synology_container_registry_download` and `synology_container_image_pull` both pull an image by `repository` and optional `tag`.

### Logs and resource usage

Use `synology_container_logs` for "why did it fail?" and `synology_container_resource` for "is it using CPU/memory?". Don't dump long logs raw; summarize errors and recent relevant lines.

## Gotchas

- **Deleting containers/projects/images/networks is destructive.** Inspect first and confirm if the target is not exact.
- **Names are not paths.** Pass container/project/network names as `name`; only project creation uses `share_path`.
- **Tags default to `latest`.** Ask or inspect if the user cares about a specific tag.
- **Network creation is advanced.** Default `driver="bridge"` is fine; only set `subnet`, `gateway`, `ip_range`, or `enable_ipv6` when the user specifies them.
- **UI-only gaps remain.** Container create/duplicate/reset/settings, interactive terminal, export, global log clear/export, registry settings, and network settings are not exposed.

## Examples

### "Restart watchtower"

```
synology_container_get(name="watchtower")
synology_container_restart(name="watchtower")
```

### "Show logs for plex"

```
synology_container_logs(name="plex")
```

Summarize recent errors; don't paste pages of logs.

### "Pull postgres 16"

```
synology_container_registry_download(repository="postgres", tag="16")
```

### "Create a compose project"

```
synology_container_project_create(
  name="media",
  share_path="/docker/media",
  content="services:\n  app:\n    image: caddy:alpine\n"
)
```
