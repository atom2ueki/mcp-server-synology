# src/health/synology_health.py - Synology NAS health monitoring
# Supports both DSM 6 and DSM 7 APIs with automatic fallback.

from typing import Any, Dict, Optional

from utils.synology_api import SynologyAPIClient


class SynologyHealth:
    """Queries Synology DSM APIs for system health, storage, network, and UPS status."""

    def __init__(
        self,
        base_url: str,
        session_id: str,
        verify_ssl: bool = False,
        syno_token: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id
        self.verify_ssl = verify_ssl
        self.syno_token = syno_token
        self._api = SynologyAPIClient(base_url, session_id, verify_ssl, syno_token=syno_token)

    def _api_call(
        self, api: str, method: str, version: int = 1, extra_params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make an authenticated call to /webapi/entry.cgi."""
        return self._api.get(api, method, version, extra_params)

    def _api_call_with_fallback(
        self,
        primary_api: str,
        primary_method: str,
        fallback_api: str,
        fallback_method: str,
        version: int = 1,
        extra_params: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Try the primary API, fall back to an alternative if it fails."""
        result = self._api_call(primary_api, primary_method, version, extra_params)
        if result.get("success"):
            return result
        return self._api_call(fallback_api, fallback_method, version, extra_params)

    # ------------------------------------------------------------------
    # System
    # ------------------------------------------------------------------

    def system_info(self) -> Dict[str, Any]:
        """Get system model, serial, DSM version, uptime, temperature."""
        result = self._api_call("SYNO.Core.System", "info")
        if result.get("success"):
            return result
        # SYNO.DSM.Info requires version 2 on DSM 7.x (minVersion=2, maxVersion=2)
        return self._api_call("SYNO.DSM.Info", "getinfo", 2)

    def utilization(self) -> Dict[str, Any]:
        """Get real-time CPU, memory, swap, and disk I/O utilization."""
        return self._api_call("SYNO.Core.System.Utilization", "get")

    # ------------------------------------------------------------------
    # Storage — uses SYNO.Storage.CGI.Storage on DSM 6 as fallback
    # ------------------------------------------------------------------

    def _storage_load_info(self) -> Dict[str, Any]:
        """DSM 6 fallback: loads all storage info in one call."""
        return self._api_call("SYNO.Storage.CGI.Storage", "load_info")

    def disk_list(self) -> Dict[str, Any]:
        """List all physical disks with SMART status, model, temp, size."""
        result = self._api_call("SYNO.Core.Storage.Disk", "list")
        if result.get("success"):
            return result
        # DSM 6 fallback
        storage = self._storage_load_info()
        if storage.get("success"):
            return {"success": True, "data": {"disks": storage["data"].get("disks", [])}}
        return storage

    def disk_smart_info(self, disk_id: str) -> Dict[str, Any]:
        """Get detailed SMART attributes for a specific disk."""
        result = self._api_call(
            "SYNO.Core.Storage.Disk", "get_smart_info", extra_params={"disk": disk_id}
        )
        if result.get("success"):
            return result
        # DSM 6 fallback
        return self._api_call("SYNO.Storage.CGI.Smart", "get")

    def volume_list(self) -> Dict[str, Any]:
        """List all volumes with status, size, usage, filesystem type."""
        result = self._api_call("SYNO.Core.Storage.Volume", "list")
        if result.get("success"):
            return result
        # DSM 6 fallback
        storage = self._storage_load_info()
        if storage.get("success"):
            return {"success": True, "data": {"volumes": storage["data"].get("volumes", [])}}
        return storage

    def storage_pool_list(self) -> Dict[str, Any]:
        """List RAID/storage pools with level, status, member disks."""
        result = self._api_call("SYNO.Core.Storage.Pool", "list")
        if result.get("success"):
            return result
        # DSM 6 fallback
        storage = self._storage_load_info()
        if storage.get("success"):
            return {"success": True, "data": {"pools": storage["data"].get("storagePools", [])}}
        return storage

    # ------------------------------------------------------------------
    # Network
    # ------------------------------------------------------------------

    def network_info(self) -> Dict[str, Any]:
        """Get network interface status and transfer rates."""
        return self._api_call("SYNO.Core.Network", "get")

    # ------------------------------------------------------------------
    # UPS
    # ------------------------------------------------------------------

    def ups_info(self) -> Dict[str, Any]:
        """Get UPS status, battery level, power readings."""
        return self._api_call("SYNO.Core.ExternalDevice.UPS", "get")

    # ------------------------------------------------------------------
    # Services / Packages
    # ------------------------------------------------------------------

    def package_list(self) -> Dict[str, Any]:
        """List installed packages and their running status."""
        return self._api_call("SYNO.Core.Package", "list")

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------

    def system_log(self, offset: int = 0, limit: int = 50) -> Dict[str, Any]:
        """Get recent system log entries."""
        return self._api_call(
            "SYNO.Core.SyslogClient.Log",
            "list",
            extra_params={"offset": str(offset), "limit": str(limit)},
        )

    # ------------------------------------------------------------------
    # Combined summary
    # ------------------------------------------------------------------

    def health_summary(self) -> Dict[str, Any]:
        """Aggregate system info, utilization, disk health, and volume status."""
        summary = {}

        sys_info = self.system_info()
        if sys_info.get("success"):
            summary["system"] = sys_info.get("data", {})

        util = self.utilization()
        if util.get("success"):
            summary["utilization"] = util.get("data", {})

        disks = self.disk_list()
        if disks.get("success"):
            summary["disks"] = disks.get("data", {})

        volumes = self.volume_list()
        if volumes.get("success"):
            summary["volumes"] = volumes.get("data", {})

        pools = self.storage_pool_list()
        if pools.get("success"):
            summary["storage_pools"] = pools.get("data", {})

        net = self.network_info()
        if net.get("success"):
            summary["network"] = net.get("data", {})

        ups = self.ups_info()
        if ups.get("success"):
            summary["ups"] = ups.get("data", {})

        return {"success": True, "data": summary}
