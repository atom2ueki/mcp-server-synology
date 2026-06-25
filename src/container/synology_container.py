"""Synology Container Manager operations."""

import json
from typing import Any, Dict, Optional

from utils.synology_api import SynologyAPIClient


class SynologyContainer:
    """Manage Container Manager containers on Synology DSM."""

    def __init__(
        self,
        base_url: str,
        session_id: str,
        verify_ssl: bool = False,
        syno_token: Optional[str] = None,
    ):
        self._api = SynologyAPIClient(base_url, session_id, verify_ssl, syno_token=syno_token)
        self.container_api = "SYNO.Docker.Container"
        self.container_version = "1"
        self.project_api = "SYNO.Docker.Project"
        self.project_version = "1"
        self.image_api = "SYNO.Docker.Image"
        self.image_version = "1"
        self.registry_api = "SYNO.Docker.Registry"
        self.registry_version = "1"
        self.network_api = "SYNO.Docker.Network"
        self.network_version = "1"
        self.container_log_api = "SYNO.Docker.Container.Log"
        self.container_resource_api = "SYNO.Docker.Container.Resource"

    def _make_request(
        self,
        api: str,
        version: str,
        method: str,
        **params: Any,
    ) -> Dict[str, Any]:
        """Make a request to a Container Manager API."""
        return self._api.post(api, method, version, params or None)

    def _container_name_request(self, method: str, name: str) -> Dict[str, Any]:
        """Call a name-based container method."""
        # DSM expects container names as JSON strings on SYNO.Docker.Container.
        return self._make_request(
            self.container_api,
            self.container_version,
            method,
            name=json.dumps(name),
        )

    def list_containers(
        self, offset: int = 0, limit: int = -1, container_type: str = "all"
    ) -> Dict[str, Any]:
        """List Container Manager containers."""
        return self._make_request(
            self.container_api,
            self.container_version,
            "list",
            offset=str(offset),
            limit=str(limit),
            type=container_type,
        )

    def get_container(self, name: str) -> Dict[str, Any]:
        """Get one Container Manager container by name."""
        return self._container_name_request("get", name)

    def start_container(self, name: str) -> Dict[str, Any]:
        """Start a Container Manager container by name."""
        return self._container_name_request("start", name)

    def stop_container(self, name: str) -> Dict[str, Any]:
        """Stop a Container Manager container by name."""
        return self._container_name_request("stop", name)

    def restart_container(self, name: str) -> Dict[str, Any]:
        """Restart a Container Manager container by name."""
        return self._container_name_request("restart", name)

    def delete_container(
        self,
        name: str,
        force: bool = False,
        preserve_profile: bool = True,
    ) -> Dict[str, Any]:
        """Delete a Container Manager container by name."""
        return self._make_request(
            self.container_api,
            self.container_version,
            "delete",
            name=json.dumps(name),
            force="true" if force else "false",
            preserve_profile="true" if preserve_profile else "false",
        )

    def list_projects(self) -> Dict[str, Any]:
        """List Container Manager projects."""
        return self._make_request(self.project_api, self.project_version, "list")

    def _project_id(self, name: str) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Find DSM's internal project ID for a project name."""
        projects = self.list_projects()
        if not projects.get("success"):
            return None, projects

        for project in projects.get("data", {}).values():
            if isinstance(project, dict) and project.get("name") == name:
                return project["id"], None

        return None, {
            "success": False,
            "error": {
                "code": "not_found",
                "message": f"Container Manager project '{name}' not found",
            },
        }

    def _project_id_request(self, method: str, name: str) -> Dict[str, Any]:
        """Call an ID-based project method by project name."""
        project_id, error = self._project_id(name)
        if error:
            return error
        return self._make_request(self.project_api, self.project_version, method, id=project_id)

    def _ensure_project_folder(self, share_path: str) -> Dict[str, Any]:
        """Create the project folder DSM requires before project creation."""
        folder_path, _, name = share_path.rstrip("/").rpartition("/")
        if not folder_path or not name:
            return {
                "success": False,
                "error": {
                    "code": "invalid_path",
                    "message": "Project share_path must include a parent folder and project name",
                },
            }

        return self._make_request(
            "SYNO.FileStation.CreateFolder",
            "2",
            "create",
            folder_path=folder_path,
            name=name,
            force_parent="true",
        )

    def get_project(self, name: str) -> Dict[str, Any]:
        """Get a Container Manager project by name."""
        return self._project_id_request("get", name)

    def start_project(self, name: str) -> Dict[str, Any]:
        """Start a Container Manager project by name."""
        return self._project_id_request("start", name)

    def stop_project(self, name: str) -> Dict[str, Any]:
        """Stop a Container Manager project by name."""
        return self._project_id_request("stop", name)

    def restart_project(self, name: str) -> Dict[str, Any]:
        """Restart a Container Manager project by name."""
        return self._project_id_request("restart", name)

    def build_project(self, name: str) -> Dict[str, Any]:
        """Build a Container Manager project by name."""
        return self._project_id_request("build", name)

    def clean_project(self, name: str) -> Dict[str, Any]:
        """Clean a Container Manager project by name."""
        return self._project_id_request("clean", name)

    def delete_project(self, name: str) -> Dict[str, Any]:
        """Delete a Container Manager project by name."""
        return self._project_id_request("delete", name)

    def create_project(
        self,
        name: str,
        share_path: str,
        content: str,
        enable_service_portal: bool = False,
        service_portal_name: Optional[str] = None,
        service_portal_port: Optional[int] = None,
        service_portal_protocol: str = "http",
    ) -> Dict[str, Any]:
        """Create a Container Manager project from compose content."""
        folder = self._ensure_project_folder(share_path)
        if not folder.get("success"):
            return folder

        params = {
            "name": json.dumps(name),
            "share_path": json.dumps(share_path),
            "content": json.dumps(""),
            "enable_service_portal": "true" if enable_service_portal else "false",
            "service_portal_name": json.dumps(service_portal_name or ""),
            "service_portal_port": str(service_portal_port or 0),
            "service_portal_protocol": json.dumps(
                service_portal_protocol if enable_service_portal else ""
            ),
        }

        result = self._make_request(self.project_api, self.project_version, "create", **params)
        if not result.get("success") or not content:
            return result

        return self.update_project(name, content)

    def update_project(
        self,
        name: str,
        content: str,
        enable_service_portal: Optional[bool] = None,
        service_portal_name: Optional[str] = None,
        service_portal_port: Optional[int] = None,
        service_portal_protocol: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update a Container Manager project's compose content."""
        project_id, error = self._project_id(name)
        if error:
            return error

        params = {"id": project_id, "content": content}
        if enable_service_portal is not None:
            params["enable_service_portal"] = json.dumps(enable_service_portal)
        if service_portal_name is not None:
            params["service_portal_name"] = service_portal_name
        if service_portal_port is not None:
            params["service_portal_port"] = str(service_portal_port)
        if service_portal_protocol is not None:
            params["service_portal_protocol"] = service_portal_protocol

        return self._make_request(self.project_api, self.project_version, "update", **params)

    def list_images(
        self, offset: int = 0, limit: int = -1, show_dsm: bool = False
    ) -> Dict[str, Any]:
        """List Container Manager images."""
        return self._make_request(
            self.image_api,
            self.image_version,
            "list",
            offset=str(offset),
            limit=str(limit),
            show_dsm=json.dumps(show_dsm),
        )

    def get_image(self, name: str, tag: str = "latest") -> Dict[str, Any]:
        """Get one Container Manager image."""
        return self._make_request(self.image_api, self.image_version, "get", image=f"{name}:{tag}")

    def _image_by_name_tag(
        self, name: str, tag: str
    ) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Find a local image object for APIs that require full image payloads."""
        images = self.list_images(limit=-1)
        if not images.get("success"):
            return None, images

        for image in images.get("data", {}).get("images", []):
            if not isinstance(image, dict):
                continue
            if image.get("repository") == name and tag in image.get("tags", []):
                return image, None

        return None, {
            "success": False,
            "error": {
                "code": "not_found",
                "message": f"Container Manager image '{name}:{tag}' not found",
            },
        }

    def delete_image(self, name: str, tag: str = "latest") -> Dict[str, Any]:
        """Delete one Container Manager image."""
        image, error = self._image_by_name_tag(name, tag)
        if error:
            return error

        return self._make_request(
            self.image_api,
            self.image_version,
            "delete",
            images=json.dumps([image]),
        )

    def pull_image(self, repository: str, tag: str = "latest") -> Dict[str, Any]:
        """Pull a Container Manager image from a registry."""
        return self._make_request(
            self.image_api,
            self.image_version,
            "pull_start",
            repository=repository,
            tag=tag,
        )

    def list_registries(self) -> Dict[str, Any]:
        """List Container Manager registries."""
        return self._make_request(self.registry_api, self.registry_version, "get")

    def search_registry(self, query: str, offset: int = 0, limit: int = 50) -> Dict[str, Any]:
        """Search images in Container Manager registries."""
        return self._make_request(
            self.registry_api,
            self.registry_version,
            "search",
            q=query,
            offset=str(offset),
            limit=str(limit),
            page_size=str(limit),
        )

    def list_registry_tags(
        self, repository: str, offset: int = 0, limit: int = 50
    ) -> Dict[str, Any]:
        """List tags for a registry image."""
        return self._make_request(
            self.registry_api,
            "2",
            "tags",
            repository=repository,
            offset=str(offset),
            limit=str(limit),
        )

    def list_networks(self) -> Dict[str, Any]:
        """List Container Manager networks."""
        return self._make_request(self.network_api, self.network_version, "list")

    def get_network(self, name: str) -> Dict[str, Any]:
        """Get one Container Manager network by name."""
        networks = self.list_networks()
        if not networks.get("success"):
            return networks

        for network in networks.get("data", {}).get("network", []):
            if isinstance(network, dict) and network.get("name") == name:
                return {"success": True, "data": {"network": network}}

        return {
            "success": False,
            "error": {
                "code": "not_found",
                "message": f"Container Manager network '{name}' not found",
            },
        }

    def create_network(
        self,
        name: str,
        driver: str = "bridge",
        subnet: Optional[str] = None,
        gateway: Optional[str] = None,
        ip_range: Optional[str] = None,
        enable_ipv6: bool = False,
    ) -> Dict[str, Any]:
        """Create a Container Manager network."""
        params = {
            "name": name,
            "driver": driver,
            "enable_ipv6": json.dumps(enable_ipv6),
        }
        if subnet is not None:
            params["subnet"] = subnet
        if gateway is not None:
            params["gateway"] = gateway
        if ip_range is not None:
            params["iprange"] = ip_range

        return self._make_request(self.network_api, self.network_version, "create", **params)

    def delete_network(self, name: str) -> Dict[str, Any]:
        """Delete a Container Manager network by name."""
        network = self.get_network(name)
        if not network.get("success"):
            return network

        return self._make_request(
            self.network_api,
            self.network_version,
            "remove",
            networks=json.dumps([network["data"]["network"]]),
        )

    def get_container_logs(
        self,
        name: str,
        since: Optional[str] = None,
        timestamps: bool = False,
    ) -> Dict[str, Any]:
        """Get Container Manager logs for a container."""
        params = {
            "name": json.dumps(name),
            "from": json.dumps(since or ""),
            "to": json.dumps(""),
            "level": json.dumps(""),
            "keyword": json.dumps(""),
            "sort_dir": json.dumps("DESC"),
            "offset": "0",
            "limit": "1000",
        }
        return self._make_request(self.container_log_api, self.container_version, "get", **params)

    def get_container_resource(self, name: str) -> Dict[str, Any]:
        """Get real-time resource usage for a container."""
        result = self._make_request(
            self.container_resource_api,
            self.container_version,
            "get",
            name=json.dumps(name),
        )
        if not result.get("success"):
            return result

        data = result.get("data", {})
        resources = data.get("resources")
        if not isinstance(resources, list):
            return result

        matches = [resource for resource in resources if resource.get("name") == name]
        if not matches:
            return {
                "success": False,
                "error": {
                    "code": "not_found",
                    "message": f"Container Manager resource '{name}' not found",
                },
            }

        return {"success": True, "data": {**data, "resources": matches}}
