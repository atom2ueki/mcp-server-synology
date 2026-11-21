"""Container Manager: containers, Compose projects, images, registries, networks.

Declarative tool catalog: description, JSON schema, handler and MCP safety
annotations live on one object, so a tool cannot ship without a classification.
"""

from tools._common import ADDITIVE, DESTRUCTIVE, READ_ONLY, ToolSpec

TOOLS = (
    ToolSpec(
        name="synology_container_list",
        description="List Container Manager containers",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "offset": {"type": "integer", "description": "Pagination offset"},
                "limit": {"type": "integer", "description": "Maximum containers to return"},
                "container_type": {
                    "type": "string",
                    "description": "Container filter (default: all)",
                },
            },
            "required": [],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "list"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_container_health_summary",
        description="Summarize container status, health, restart counts, and images",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
            },
            "required": [],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "health_summary"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_container_disk_usage",
        description="Show Docker disk usage without changing anything",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
            },
            "required": [],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "disk_usage"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_container_get",
        description="Get detailed information about a specific container",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Container name (e.g. 'watchtower')"},
            },
            "required": ["name"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "get"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_container_start",
        description="Start a container",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Container name (e.g. 'watchtower')"},
            },
            "required": ["name"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "start"},
        annotations=ADDITIVE,
    ),
    ToolSpec(
        name="synology_container_stop",
        description="Stop a running container",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Container name (e.g. 'watchtower')"},
            },
            "required": ["name"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "stop"},
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_container_restart",
        description="Restart a container",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Container name (e.g. 'watchtower')"},
            },
            "required": ["name"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "restart"},
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_container_delete",
        description="Delete a container",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Container name (e.g. 'watchtower')"},
            },
            "required": ["name"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "delete"},
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_container_logs",
        description="Get logs from a container",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Container name (e.g. 'watchtower')"},
                "since": {
                    "type": "string",
                    "description": "Return logs since this timestamp (optional)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Pagination offset (default: 0)",
                    "minimum": 0,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max lines to return (default: 1000)",
                    "minimum": 1,
                },
            },
            "required": ["name"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "logs"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_container_resource",
        description="Get resource usage for a container",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Container name (e.g. 'watchtower')"},
            },
            "required": ["name"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "resource"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_container_project_list",
        description="List Docker Compose projects",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
            },
            "required": [],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "project_list"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_container_project_get",
        description="Get Docker Compose project details with Compose, environment, and secret fields omitted",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Project name (e.g. 'watchtower')"},
            },
            "required": ["name"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "project_get"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_container_project_create",
        description="Create and save a Docker Compose project definition; run project_build to materialize it",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Project name (e.g. 'watchtower')"},
                "content": {"type": "string", "description": "Docker Compose YAML content"},
                "share_path": {
                    "type": "string",
                    "description": "Share path where the compose file is stored",
                },
                "enable_service_portal": {
                    "type": "boolean",
                    "description": "Enable Synology service portal (default: false)",
                },
                "service_portal_name": {
                    "type": "string",
                    "description": "Optional service portal name",
                },
                "service_portal_port": {
                    "type": "integer",
                    "description": "Optional service portal port",
                },
                "service_portal_protocol": {
                    "type": "string",
                    "description": "Service portal protocol (default: http)",
                },
            },
            "required": ["name", "share_path", "content"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "project_create"},
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_container_project_update",
        description="Update a Docker Compose project",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Project name (e.g. 'watchtower')"},
                "content": {"type": "string", "description": "New Docker Compose YAML content"},
            },
            "required": ["name", "content"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "project_update"},
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_container_project_start",
        description="Start Docker Compose project services",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Project name (e.g. 'watchtower')"},
            },
            "required": ["name"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "project_start"},
        annotations=ADDITIVE,
    ),
    ToolSpec(
        name="synology_container_project_stop",
        description="Stop Docker Compose project services",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Project name (e.g. 'watchtower')"},
            },
            "required": ["name"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "project_stop"},
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_container_project_restart",
        description="Restart Docker Compose project services",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Project name (e.g. 'watchtower')"},
            },
            "required": ["name"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "project_restart"},
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_container_project_build",
        description="Materialize or rebuild a saved Docker Compose project",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Project name (e.g. 'watchtower')"},
            },
            "required": ["name"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "project_build"},
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_container_project_clean",
        description="Clean Docker Compose project resources",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Project name (e.g. 'watchtower')"},
            },
            "required": ["name"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "project_clean"},
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_container_project_delete",
        description="Delete a Docker Compose project",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Project name (e.g. 'watchtower')"},
            },
            "required": ["name"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "project_delete"},
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_container_image_list",
        description="List Docker images",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
            },
            "required": [],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "image_list"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_container_image_get",
        description="Get details of a specific Docker image",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Image repository name (e.g. 'nginx')"},
                "tag": {"type": "string", "description": "Image tag (default: latest)"},
            },
            "required": ["name"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "image_get"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_container_image_delete",
        description="Delete a Docker image",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Image repository name (e.g. 'nginx')"},
                "tag": {"type": "string", "description": "Image tag (default: latest)"},
            },
            "required": ["name"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "image_delete"},
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_container_image_prune_preview",
        description="Preview unused Docker images without deleting anything",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
            },
            "required": [],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "image_prune_preview"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_container_image_prune",
        description="Remove Docker images unused by any container; preserves containers, networks, and build cache",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
            },
            "required": [],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "image_prune"},
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_container_image_pull",
        description="Pull a Docker image from a registry",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "repository": {
                    "type": "string",
                    "description": "Image repository name (e.g. 'nginx')",
                },
                "tag": {"type": "string", "description": "Image tag (default: latest)"},
            },
            "required": ["repository"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "image_pull"},
        annotations=ADDITIVE,
    ),
    ToolSpec(
        name="synology_container_registry_list",
        description="List configured Docker registries",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
            },
            "required": [],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "registry_list"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_container_registry_search",
        description="Search for images in a Docker registry",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "query": {"type": "string", "description": "Search query for image name"},
                "offset": {"type": "integer", "description": "Pagination offset (default: 0)"},
                "limit": {"type": "integer", "description": "Max results to return (default: 50)"},
            },
            "required": ["query"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "registry_search"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_container_registry_tags",
        description="List tags for a repository in a Docker registry",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "repository": {
                    "type": "string",
                    "description": "Image repository name (e.g. 'nginx')",
                },
                "tag": {"type": "string", "description": "Image tag (default: latest)"},
            },
            "required": ["repository"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "registry_tags"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_container_registry_download",
        description="Download a Docker image from a registry",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "repository": {
                    "type": "string",
                    "description": "Image repository name (e.g. 'nginx')",
                },
                "tag": {"type": "string", "description": "Image tag (default: latest)"},
            },
            "required": ["repository"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "registry_download"},
        annotations=ADDITIVE,
    ),
    ToolSpec(
        name="synology_container_network_list",
        description="List Docker networks",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
            },
            "required": [],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "network_list"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_container_network_get",
        description="Get details of a Docker network",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Network name"},
            },
            "required": ["name"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "network_get"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_container_network_create",
        description="Create a Docker network",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Network name"},
                "driver": {"type": "string", "description": "Network driver (default: bridge)"},
                "subnet": {"type": "string", "description": "Subnet CIDR (optional)"},
                "gateway": {"type": "string", "description": "Gateway IP (optional)"},
                "ip_range": {"type": "string", "description": "IP range (optional)"},
                "enable_ipv6": {"type": "boolean", "description": "Enable IPv6 (default: false)"},
            },
            "required": ["name"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "network_create"},
        annotations=ADDITIVE,
    ),
    ToolSpec(
        name="synology_container_network_delete",
        description="Delete a Docker network",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Network name"},
            },
            "required": ["name"],
        },
        handler="_handle_container_call",
        handler_kwargs={"method_name": "network_delete"},
        annotations=DESTRUCTIVE,
    ),
)
