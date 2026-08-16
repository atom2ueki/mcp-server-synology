"""Synology Container Manager operations."""

import json
import os
import stat
import subprocess
import tempfile
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from utils.synology_api import SynologyAPIClient


class SynologyContainer:
    """Manage Container Manager containers on Synology DSM."""

    def __init__(
        self,
        base_url: str,
        session_id: str,
        verify_ssl: bool = False,
        syno_token: Optional[str] = None,
        ssh_username: Optional[str] = None,
        ssh_password: Optional[str] = None,
        ssh_known_hosts: Optional[str] = None,
    ):
        self._api = SynologyAPIClient(base_url, session_id, verify_ssl, syno_token=syno_token)
        self._ssh_username = ssh_username
        self._ssh_password = ssh_password
        self._ssh_known_hosts = ssh_known_hosts
        self.container_api = "SYNO.Docker.Container"
        self.container_version = 1
        self.project_api = "SYNO.Docker.Project"
        self.project_version = 1
        self.image_api = "SYNO.Docker.Image"
        self.image_version = 1
        self.registry_api = "SYNO.Docker.Registry"
        self.registry_version = 1
        # The registry `tags` method is served by API v2, unlike the v1 list/search/get.
        self.registry_tags_version = 2
        self.network_api = "SYNO.Docker.Network"
        self.network_version = 1
        self.container_log_api = "SYNO.Docker.Container.Log"
        self.container_resource_api = "SYNO.Docker.Container.Resource"

    def _make_request(
        self,
        api: str,
        version: int,
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

    def health_summary(self) -> Dict[str, Any]:
        """Return a compact health summary for every container."""
        result = self.list_containers(offset=0, limit=-1, container_type="all")
        if not result.get("success"):
            return result
        data = result.get("data", {})
        items = data.get("containers", []) if isinstance(data, dict) else []
        summary = [{"name": i.get("name"), "status": i.get("status", i.get("state")), "health": i.get("health"), "restart_count": i.get("restartCount", i.get("restart_count", 0)), "image": i.get("image")} for i in items if isinstance(i, dict)]
        return {"success": True, "data": {"containers": summary, "count": len(summary)}}

    def disk_usage(self) -> Dict[str, Any]:
        """Return Docker disk usage using the read-only SSH CLI."""
        return self._ssh_docker_command("system df", "docker_disk_usage_failed", "Docker disk usage timed out")

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

        data = projects.get("data", {})
        if isinstance(data, dict):
            candidates = list(data.values())
        elif isinstance(data, list):
            candidates = data
        else:
            candidates = []

        for project in candidates:
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
            2,
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
            params["service_portal_name"] = json.dumps(service_portal_name)
        if service_portal_port is not None:
            params["service_portal_port"] = str(service_portal_port)
        if service_portal_protocol is not None:
            params["service_portal_protocol"] = json.dumps(service_portal_protocol)

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
            name=name,
            tag=tag,
        )

    def prune_images(self) -> Dict[str, Any]:
        """Delete images that are not referenced by any container.

        Some DSM versions expose no working Docker image-prune API.  When
        explicitly configured with SSH credentials, use Docker's image-only
        prune command.  Otherwise use the DSM list/delete APIs and fail closed
        if any container image reference is unavailable.
        """
        if self._ssh_username and self._ssh_password:
            return self._ssh_docker_prune()

        return self._prune_images_from_inventory(dry_run=False)

    def preview_image_prune(self) -> Dict[str, Any]:
        """Preview removable images without invoking SSH or mutating DSM."""
        return self._prune_images_from_inventory(dry_run=True)

    def _prune_images_from_inventory(self, dry_run: bool) -> Dict[str, Any]:
        """Compute image-prune candidates from the DSM inventories."""

        containers = self.list_containers(offset=0, limit=-1, container_type="all")
        if not containers.get("success"):
            return containers
        container_data = containers.get("data")
        if not isinstance(container_data, dict) or not isinstance(container_data.get("containers"), list):
            return {"success": False, "error": {"code": "unsafe_prune", "message": "Unexpected container inventory; no images were deleted"}}
        container_items = container_data["containers"]
        references = set()
        digest_repositories = set()
        for item in container_items:
            if not isinstance(item, dict) or not item.get("image"):
                return {
                    "success": False,
                    "error": {
                        "code": "unsafe_prune",
                        "message": "Cannot determine every container image reference; no images were deleted",
                    },
                }
            image = str(item["image"])
            references.add(image)
            if "@" in image:
                digest_repositories.add(image.split("@", 1)[0])

        images_result = self.list_images(offset=0, limit=-1)
        if not images_result.get("success"):
            return images_result
        image_data = images_result.get("data", {})
        images = image_data.get("images", []) if isinstance(image_data, dict) else []
        if not isinstance(images, list):
            return {
                "success": False,
                "error": {"code": "unsafe_prune", "message": "Unexpected image inventory; no images were deleted"},
            }

        candidates = []
        skipped = []
        for image in images:
            if not isinstance(image, dict) or not image.get("repository"):
                continue
            tags = image.get("tags") or ["<none>"]
            for tag in tags:
                tag = str(tag)
                full_name = f"{image['repository']}:{tag}"
                if tag == "<none>":
                    # DSM rejects literal <none> tags.  Leave dangling layers
                    # for the Docker CLI fallback rather than reporting a
                    # misleading deletion attempt.
                    skipped.append(full_name)
                elif full_name not in references and image["repository"] not in digest_repositories:
                    candidates.append((full_name, image))

        if dry_run:
            return {
                "success": True,
                "data": {
                    "mode": "preview",
                    "candidates": [name for name, _ in candidates],
                    "skipped": skipped,
                    "errors": [],
                },
            }

        deleted = []
        errors = []
        for full_name, image in candidates:
            repository, tag = full_name.rsplit(":", 1)
            result = self._make_request(
                self.image_api,
                self.image_version,
                "delete",
                name=repository,
                tag=tag,
            )
            if result.get("success"):
                deleted.append(full_name)
            else:
                errors.append({"image": full_name, "error": result.get("error", {})})

        response = {
            "success": not errors,
            "data": {"mode": "api", "deleted": deleted, "skipped": skipped, "errors": errors},
        }
        if errors:
            response["error"] = {"code": "partial_prune", "message": "Some unused images could not be deleted"}
        return response

    def _ssh_docker_prune(self) -> Dict[str, Any]:
        """Run Docker's image-only prune through the authorized SSH account."""
        return self._ssh_docker_command("image prune --all --force", "ssh_prune_failed", "Docker prune timed out")

    def _ssh_docker_command(self, docker_command: str, error_code: str, timeout_message: str) -> Dict[str, Any]:
        """Run a fixed Docker command through the explicitly configured SSH account."""
        host = urlsplit(self._api.base_url).hostname
        if not host:
            return {"success": False, "error": {"code": "invalid_host", "message": "NAS URL has no hostname"}}
        if not self._ssh_username or not self._ssh_password:
            return {"success": False, "error": {"code": "ssh_credentials_unavailable", "message": "Explicit SSH credentials are required"}}
        if not self._ssh_known_hosts:
            return {"success": False, "error": {"code": "ssh_known_hosts_unavailable", "message": "A provisioned SSH known_hosts file is required"}}


        if docker_command not in {"image prune --all --force", "system df"}:
            return {"success": False, "error": {"code": "unsupported_ssh_command", "message": "Unsupported Docker command"}}

        askpass_fd, askpass = tempfile.mkstemp(prefix="synology-mcp-askpass-")
        os.close(askpass_fd)
        try:
            os.chmod(askpass, stat.S_IRWXU)
            with open(askpass, "w", encoding="utf-8") as handle:
                handle.write("#!/bin/sh\nprintf '%s\\n' \"$SYNOLOGY_SSH_PASSWORD\"\n")
            env = os.environ.copy()
            env.update({
                "SYNOLOGY_SSH_PASSWORD": self._ssh_password,
                "SSH_ASKPASS": askpass,
                "SSH_ASKPASS_REQUIRE": "force",
                "DISPLAY": "synology-mcp",
            })
            command = "sudo -n /var/packages/ContainerManager/target/usr/bin/docker " + docker_command
            result = subprocess.run(
                [
                    "/usr/bin/setsid", "/usr/bin/ssh", "-o", "BatchMode=no", "-o", "ConnectTimeout=20",
                    "-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no",
                    "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={self._ssh_known_hosts}", f"{self._ssh_username}@{host}", command,
                ],
                capture_output=True, text=True, timeout=300, env=env, check=False,
            )
        except FileNotFoundError as exc:
            return {"success": False, "error": {"code": "ssh_unavailable", "message": str(exc)}}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": {"code": "ssh_timeout", "message": timeout_message}}
        finally:
            try:
                os.unlink(askpass)
            except OSError:
                pass

        output = (result.stdout or "").strip()
        if result.returncode:
            return {"success": False, "error": {"code": error_code, "message": (result.stderr or output).strip()[-2000:]}}
        data = {"mode": "ssh", "output": output}
        if docker_command == "system df":
            data["command"] = docker_command
        return {"success": True, "data": data}

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
            self.registry_tags_version,
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
        offset: int = 0,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        """Get Container Manager logs for a container."""
        params = {
            "name": json.dumps(name),
            "from": json.dumps(since or ""),
            "to": json.dumps(""),
            "level": json.dumps(""),
            "keyword": json.dumps(""),
            "sort_dir": json.dumps("DESC"),
            "offset": str(offset),
            "limit": str(limit),
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
