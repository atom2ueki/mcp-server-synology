# src/config.py - Configuration management
# Loads all settings from XDG standard config directory (~/.config/synology-mcp/settings.json).
# Supports multiple NAS, Xiaozhi integration, and server settings.

import json
import logging
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

# Setup logger
logger = logging.getLogger("synology-mcp")

# XDG Base Directory Specification: ~/.config/synology-mcp/
XDG_CONFIG_HOME: Path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
CONFIG_DIR = XDG_CONFIG_HOME / "synology-mcp"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

# Example settings.json structure for documentation
SETTINGS_JSON_EXAMPLE = """
{
  "synology": {
    "nas1": {
      "host": "192.168.1.100",
      "port": 5000,
      "username": "admin",
      "password": "your_password",
      "note": "Primary NAS at home"
    },
    "nas2": {
      "host": "192.168.1.200",
      "port": 5001,
      "username": "admin",
      "password": "your_password",
      "note": "Backup NAS"
    }
  },
  "xiaozhi": {
    "enabled": false,
    "token": "your_xiaozhi_token",
    "endpoint": "wss://api.xiaozhi.me/mcp/"
  },
  "server": {
    "auto_login": true,
    "verify_ssl": false,
    "session_timeout": 3600,
    "debug": false,
    "log_level": "INFO"
  }
}
"""


class SynologyConfig:
    """Configuration manager for Synology MCP Server."""

    def __init__(self, env_file: Optional[str] = None):
        """Initialize configuration from settings.json."""
        # Legacy .env support (deprecated - use settings.json instead)
        if env_file:
            load_dotenv(env_file)
        elif os.path.exists(".env"):
            load_dotenv(".env")

        self._load_env_settings()
        self._load_settings()

    def _load_env_settings(self):
        """Load non-sensitive settings from environment / .env."""
        self.server_name = os.getenv("MCP_SERVER_NAME", "synology-mcp-server")
        self.server_version = os.getenv("MCP_SERVER_VERSION", "1.0.0")
        self.default_session_timeout = int(os.getenv("SESSION_TIMEOUT", "3600"))
        self.auto_login = os.getenv("AUTO_LOGIN", "true").lower() == "true"
        self.verify_ssl = os.getenv("VERIFY_SSL", "false").lower() == "true"
        self.debug = os.getenv("DEBUG", "false").lower() == "true"
        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()

        # Streamable HTTP transport settings
        self.http_enabled = os.getenv("MCP_HTTP", "false").lower() == "true"
        self.http_host = os.getenv("MCP_HTTP_HOST", "127.0.0.1")
        self.http_port = int(os.getenv("MCP_HTTP_PORT", "8765"))
        self.http_path = os.getenv("MCP_HTTP_PATH", "/mcp")
        # Comma-separated allowed Host/Origin values for DNS rebinding
        # protection when binding to a non-loopback address. Defaults to the
        # loopback host:port. Operators behind a reverse proxy should add their
        # public domain here.
        self.http_allowed_hosts = [
            h.strip()
            for h in os.getenv("MCP_HTTP_ALLOWED_HOSTS", "").split(",")
            if h.strip()
        ]
        self.http_allowed_origins = [
            o.strip()
            for o in os.getenv("MCP_HTTP_ALLOWED_ORIGINS", "").split(",")
            if o.strip()
        ]

        # Legacy single-NAS env vars (still supported as fallback)
        self.synology_url = os.getenv("SYNOLOGY_URL")
        self.synology_username = os.getenv("SYNOLOGY_USERNAME")
        self.synology_password = os.getenv("SYNOLOGY_PASSWORD")
        # One-shot 2FA code for legacy .env single-NAS users on first login.
        # Settings.json users store `device_id` per-NAS for ongoing reuse and
        # don't need this. Read-only here; auto-login consumes it but does
        # not clear it (auto-login only runs once per process start today).
        self.synology_otp_code = os.getenv("SYNOLOGY_OTP_CODE")

    def _check_file_permissions(self, path: Path) -> bool:
        """Check if secrets file has safe permissions (0600 or stricter).

        Returns True if permissions are safe, False otherwise.
        Prints warning if permissions are too open.

        POSIX: checks file ownership and group/other permission bits.
        Windows: full ACL audit via pywin32 (owner SID match + DACL allowlist).
        Fails closed if pywin32 is missing; opt back in with
        ``SYNOLOGY_MCP_ALLOW_UNVERIFIED_WINDOWS_ACL=true``.
        Other platforms without os.getuid(): skipped with a warning, returns
        True (unverified).
        """
        if os.name == "nt":
            return self._check_windows_file_permissions(path)

        if not hasattr(os, "getuid"):
            logger.warning(
                f"Permission check for {path} SKIPPED: this platform has no POSIX "
                "file ownership. Treated as acceptable so the file still loads, but its "
                "permissions were NOT verified. Confirm the ACLs restrict it to your user."
            )
            return True

        # POSIX fallback (original logic)
        try:
            file_stat = path.stat()
            mode = file_stat.st_mode

            # Check if file is owned by current user
            if os.getuid() != file_stat.st_uid:
                logger.warning(f"{path} is owned by a different user (uid={file_stat.st_uid})")
                return False

            # Check for group/other read/write permissions
            if mode & (stat.S_IRWXG | stat.S_IRWXO):
                logger.warning(f"{path} has overly permissive permissions (mode={oct(mode)})")
                logger.warning(f"Recommended: chmod 600 {path}")
                return False

            return True
        except OSError as e:
            logger.warning(f"Could not check permissions for {path}: {e}")
            return False

    def _check_windows_file_permissions(self, path: Path) -> bool:
        """Check Windows ACL for settings.json.

        Enforces the policy from issue #72: the file must grant access only to
        the current user (plus a small system allowlist). Concretely:

        1. The owner SID must match the current user.
        2. The DACL must be present (a NULL DACL means "everyone full access"
           and is rejected).
        3. No allow-ACE may grant access to a principal outside the allowlist:
           the current user, ``NT AUTHORITY\\SYSTEM``, and
           ``BUILTIN\\Administrators``. Inherited ``Everyone`` /
           ``BUILTIN\\Users`` / ``Authenticated Users`` grants fail the check.

        pywin32 is an **optional** dependency. If it is not installed the check
        **fails closed** (returns ``False``) so credentials are never loaded
        unverified. Operators who accept the risk can opt back in to the old
        unverified-load behaviour by setting
        ``SYNOLOGY_MCP_ALLOW_UNVERIFIED_WINDOWS_ACL=true``.
        """
        try:
            import ntsecuritycon
            import win32api
            import win32security
        except ImportError:
            return self._windows_acl_unavailable(path)

        try:
            # OWNER + DACL: we need both to enforce the policy. OWNER alone is
            # not enough — a file owned by the current user can still inherit
            # an Everyone/BUILTIN\Users allow-ACE from its parent folder.
            info = (
                win32security.OWNER_SECURITY_INFORMATION
                | win32security.DACL_SECURITY_INFORMATION
            )
            sd = win32security.GetFileSecurity(str(path), info)
            owner_sid = sd.GetSecurityDescriptorOwner()

            # Current user SID via the process token.
            # GetTokenInformation(TokenUser) returns a (sid, attributes) tuple.
            token = win32security.OpenProcessToken(
                win32api.GetCurrentProcess(),
                win32security.TOKEN_QUERY,
            )
            token_user = win32security.GetTokenInformation(token, win32security.TokenUser)
            current_sid = token_user[0]

            if current_sid != owner_sid:
                logger.warning(
                    f"{path} owner SID does not match current user. "
                    "Expected ACL to restrict access to the current user only."
                )
                return False

            # Build the allowlist of trusted trustee SIDs. We compare SIDs as
            # strings (sid.__str__) for stability against pywin32's PySID
            # object identity quirks.
            #
            # Use well-known SID *literals* rather than LookupAccountName:
            # account names are localized on non-English Windows (e.g. German
            # "Administratoren"), so LookupAccountName("Administrators") can
            # fail and drop S-1-5-32-544 from the allowlist — rejecting a
            # normal secured DACL. The SID strings are locale-independent.
            allowed_sid_strs = {
                str(current_sid),
                "S-1-5-18",       # NT AUTHORITY\SYSTEM
                "S-1-5-32-544",   # BUILTIN\Administrators
            }

            dacl = sd.GetSecurityDescriptorDacl()
            if dacl is None:
                # NULL DACL: no access control — anyone can read/write. Reject.
                logger.warning(
                    f"{path} has a NULL DACL (no access control). "
                    "Refusing to load. Restrict the file to your user; see README."
                )
                return False

            # PyACL: enumerate via GetAceCount()/GetAce(i) — it is NOT directly
            # iterable on real Windows (pywin32 returns a PyACL object).
            #
            # ACE type semantics (Win32):
            #   0 ACCESS_ALLOWED_ACE_TYPE              — widens (grant)
            #   1 ACCESS_DENIED_ACE_TYPE               — narrows (deny)
            #   6 ACCESS_ALLOWED_OBJECT_ACE_TYPE       — widens (object grant)
            #   7 ACCESS_DENIED_OBJECT_ACE_TYPE        — narrows (object deny)
            #   9 ACCESS_ALLOWED_CALLBACK_ACE_TYPE     — widens (conditional)
            #  11 ACCESS_ALLOWED_CALLBACK_OBJECT_ACE_TYPE — widens (conditional obj)
            #   2/3 SYSTEM_AUDIT / SYSTEM_ALARM         — audit, not access
            #
            # Strategy: explicitly enumerate the DENY and audit types we know
            # are safe to skip (they don't widen access). For the known ALLOW
            # types, audit the trustee. For ANYTHING ELSE (callback, dynamic,
            # or future types), fail closed — we can't prove they don't widen.
            allow_type = ntsecuritycon.ACCESS_ALLOWED_ACE_TYPE
            # Fallback values per WinNT.h / MS-DTYP §2.4.4.1:
            #   ACCESS_ALLOWED_OBJECT_ACE_TYPE = 5, ACCESS_DENIED_OBJECT_ACE_TYPE = 6,
            #   ACCESS_DENIED_ACE_TYPE = 1, SYSTEM_AUDIT_ACE_TYPE = 2,
            #   SYSTEM_ALARM_ACE_TYPE = 3.
            # getattr() reads the real ntsecuritycon first; the literal is only
            # a defensive default if an attribute is missing.
            allow_object_type = getattr(
                ntsecuritycon, "ACCESS_ALLOWED_OBJECT_ACE_TYPE", 5
            )
            deny_type = getattr(ntsecuritycon, "ACCESS_DENIED_ACE_TYPE", 1)
            deny_object_type = getattr(
                ntsecuritycon, "ACCESS_DENIED_OBJECT_ACE_TYPE", 6
            )
            # Types that provably don't widen access: deny types (narrow) and
            # SACL audit types (no access effect). Everything else must be
            # audited or rejected.
            safe_to_skip = {
                deny_type,
                deny_object_type,
                getattr(ntsecuritycon, "SYSTEM_AUDIT_ACE_TYPE", 2),
                getattr(ntsecuritycon, "SYSTEM_ALARM_ACE_TYPE", 3),
            }
            # ACE tuple shapes returned by pywin32's PyACL.GetAce(i):
            #   conventional: ((ace_type, ace_flags), mask, sid)
            #   object:       ((ace_type, ace_flags), mask, object_type,
            #                  inherited_object_type, sid)
            # Conventional trustee SID is ace[2]; object ACE trustee is ace[-1].
            ace_count = dacl.GetAceCount()
            for i in range(ace_count):
                ace = dacl.GetAce(i)
                # ace_type is ace[0][0] — ace[0][1] is the *flags* (e.g.
                # INHERITED_ACE), which would misclassify an inherited
                # allow-ACE as a deny and let it through.
                try:
                    ace_type = ace[0][0]
                except Exception:
                    ace_type = None

                # Known-safe non-widening types (deny / audit) narrow or have
                # no access effect — skip them.
                if ace_type in safe_to_skip:
                    continue

                # Known allow types — audit the trustee against the allowlist.
                if ace_type == allow_type:
                    trustee_index = 2
                elif ace_type == allow_object_type:
                    trustee_index = -1
                else:
                    # Unrecognised type (callback, conditional, dynamic, or a
                    # future type). We can't prove it doesn't widen access, so
                    # fail closed rather than risk a silent bypass.
                    logger.warning(
                        f"{path} DACL contains an ACE of unrecognised type "
                        f"({ace_type}); failing closed. "
                        "Restrict the file to your user; see README."
                    )
                    return False

                try:
                    trustee_sid = ace[trustee_index]
                    trustee_sid_str = str(trustee_sid)
                except Exception as e:
                    logger.warning(
                        f"{path} has an ACE whose trustee could not be read ({e}); "
                        "failing closed."
                    )
                    return False

                if trustee_sid_str not in allowed_sid_strs:
                    logger.warning(
                        f"{path} DACL grants access to a principal outside the "
                        f"allowlist (SID={trustee_sid_str}, ace_type={ace_type}). "
                        "Restrict the file to your user; see README."
                    )
                    return False

            logger.info(
                f"Windows ACL check passed for {path} "
                "(owner verified, no foreign allow-ACE in DACL)"
            )
            return True

        except Exception as e:
            logger.warning(f"Windows ACL check failed for {path}: {e}")
            return False

    @staticmethod
    def _windows_acl_unavailable(path: Path) -> bool:
        """Handle pywin32 being unavailable on Windows.

        Default is fail-closed: refuse to load credentials we can't verify.
        Operators who accept the risk of an unverified settings.json can set
        ``SYNOLOGY_MCP_ALLOW_UNVERIFIED_WINDOWS_ACL=true`` to restore the old
        load-anyway behaviour.
        """
        allow_unverified = os.getenv(
            "SYNOLOGY_MCP_ALLOW_UNVERIFIED_WINDOWS_ACL", "false"
        ).lower() == "true"
        if allow_unverified:
            logger.warning(
                f"pywin32 not installed. Windows ACL check for {path} SKIPPED "
                "(SYNOLOGY_MCP_ALLOW_UNVERIFIED_WINDOWS_ACL=true). "
                "File permissions were NOT verified — install pywin32 to enable enforcement."
            )
            return True
        logger.error(
            f"pywin32 not installed; cannot verify Windows ACL for {path}. "
            "Refusing to load credentials. Either install pywin32 or set "
            "SYNOLOGY_MCP_ALLOW_UNVERIFIED_WINDOWS_ACL=true to accept the risk."
        )
        return False

    def _load_settings(self):
        """Load all settings from XDG config directory (~/.config/synology-mcp/settings.json)."""
        self.nas_configs: Dict[str, Dict[str, Any]] = {}

        # Default values for xiaozhi and server settings.
        # http_* values are already set from env vars in _load_env_settings;
        # keep them and only override when server.http explicitly supplies a value.
        self.xiaozhi_enabled = False
        self.xiaozhi_token = ""
        self.xiaozhi_endpoint = "wss://api.xiaozhi.me/mcp/"

        if SETTINGS_FILE.exists():
            # Check file permissions - refuse to load if insecure
            if not self._check_file_permissions(SETTINGS_FILE):
                logger.error("Refusing to load settings with insecure permissions")
                return

            try:
                data = json.loads(SETTINGS_FILE.read_text())

                # Load server settings FIRST (they override env vars).
                # The NAS loop below falls back to `self.verify_ssl` for any
                # entry that does not set its own, so `server.verify_ssl` has
                # to be applied before that loop runs. Parsed afterwards, the
                # fallback silently used the environment value instead and a
                # `server.verify_ssl` in settings.json never reached a NAS.
                server_section = data.get("server", {})
                if server_section:
                    if "auto_login" in server_section:
                        self.auto_login = server_section["auto_login"]
                    if "verify_ssl" in server_section:
                        self.verify_ssl = server_section["verify_ssl"]
                    if "session_timeout" in server_section:
                        self.default_session_timeout = server_section["session_timeout"]
                    if "debug" in server_section:
                        self.debug = server_section["debug"]
                    if "log_level" in server_section:
                        self.log_level = server_section["log_level"].upper()
                    # HTTP transport settings
                    http_section = server_section.get("http", {})
                    if "enabled" in http_section:
                        self.http_enabled = http_section["enabled"]
                    if "host" in http_section:
                        self.http_host = http_section["host"]
                    if "port" in http_section:
                        self.http_port = http_section["port"]
                    if "path" in http_section:
                        self.http_path = http_section["path"]

                # Load Synology NAS credentials
                synology_section = data.get("synology", {})

                if not synology_section:
                    logger.warning(f"No 'synology' section found in {SETTINGS_FILE}")

                for nas_name, nas_info in synology_section.items():
                    if not isinstance(nas_info, dict):
                        logger.warning(
                            f"Invalid entry for NAS '{nas_name}' - expected object, got {type(nas_info)}"
                        )
                        continue

                    # `url` wins over host/port when present. The host/port form
                    # below can only ever build https://host:5001 or http://host:<port>,
                    # so it cannot address a NAS sitting behind a reverse proxy on
                    # the default 443 (no port in the URL at all). Reverse-proxied
                    # setups set `url` directly.
                    raw_url = nas_info.get("url")
                    if raw_url is not None and not isinstance(raw_url, str):
                        logger.warning(
                            f"Invalid 'url' for NAS '{nas_name}' - expected string, "
                            f"got {type(raw_url)}"
                        )
                        continue
                    explicit_url = (raw_url or "").rstrip("/")
                    host = nas_info.get("host", "")
                    port = nas_info.get("port", 5000)
                    username = nas_info.get("username", "")
                    password = nas_info.get("password", "")
                    # Optional 2FA/OTP support (DSM Login Web API Guide):
                    #   - `otp_code`: one-shot code from authenticator. Needed
                    #     only on the FIRST login after enabling 2FA on the
                    #     DSM account. After that first login, DSM issues a
                    #     device token (`did`); paste it as `device_id` and
                    #     remove `otp_code`.
                    #   - `device_id`: long-lived trusted-device token. When
                    #     present, DSM skips OTP for this login and for the
                    #     silent re-auth path (DSM error 119 recovery).
                    otp_code = nas_info.get("otp_code") or None
                    device_id = nas_info.get("device_id") or None

                    if not host and not explicit_url:
                        logger.warning(
                            f"Missing 'host' (or 'url') for NAS '{nas_name}' in {SETTINGS_FILE}"
                        )
                        continue
                    if not username:
                        logger.warning(
                            f"Missing 'username' for NAS '{nas_name}' in {SETTINGS_FILE}"
                        )
                        continue
                    if not password:
                        logger.warning(
                            f"Missing 'password' for NAS '{nas_name}' in {SETTINGS_FILE}"
                        )
                        continue

                    if explicit_url:
                        base_url = explicit_url
                    else:
                        scheme = "https" if port == 5001 else "http"
                        base_url = f"{scheme}://{host}:{port}"

                    # Per-NAS override, falling back to the global server
                    # setting applied above. A single global flag cannot serve
                    # both a NAS with a real certificate (verification on) and
                    # one addressed by IP with DSM's self-signed certificate
                    # (verification off) once more than one NAS is configured.
                    nas_verify_ssl = nas_info.get("verify_ssl", self.verify_ssl)
                    if not isinstance(nas_verify_ssl, bool):
                        logger.warning(
                            f"Invalid 'verify_ssl' for NAS '{nas_name}' - expected "
                            f"boolean, got {type(nas_verify_ssl)}; "
                            f"falling back to {self.verify_ssl}"
                        )
                        nas_verify_ssl = self.verify_ssl

                    self.nas_configs[nas_name] = {
                        "base_url": base_url,
                        "username": username,
                        "password": password,
                        "verify_ssl": nas_verify_ssl,
                        "note": nas_info.get("note", ""),
                        "otp_code": otp_code,
                        "device_id": device_id,
                    }

                # Load Xiaozhi settings
                xiaozhi_section = data.get("xiaozhi", {})
                if xiaozhi_section:
                    self.xiaozhi_enabled = xiaozhi_section.get("enabled", False)
                    self.xiaozhi_token = xiaozhi_section.get("token", "")
                    self.xiaozhi_endpoint = xiaozhi_section.get(
                        "endpoint", "wss://api.xiaozhi.me/mcp/"
                    )

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse {SETTINGS_FILE}: {e}")
            except OSError as e:
                logger.error(f"Failed to read {SETTINGS_FILE}: {e}")
        else:
            # No settings file
            logger.info(f"No {SETTINGS_FILE} found")

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def verify_ssl_for(self, base_url: str) -> bool:
        """Return the SSL verification policy for a NAS, by base URL.

        Clients are created per base_url, which is the only NAS identity
        available at those call sites. Falls back to the global setting for a
        base_url that matches no configured NAS (legacy .env single-NAS mode,
        or a session created by an explicit login tool call).

        Both sides go through `_normalize_base_url` before matching: an
        explicit login to `https://NAS.example` must still resolve the policy
        of a NAS configured as `https://nas.example` — otherwise the global
        fallback could silently *downgrade* verification for a NAS that asked
        for it.
        """
        target = self._normalize_base_url(base_url)
        for cfg in self.nas_configs.values():
            configured = cfg.get("base_url")
            if configured and self._normalize_base_url(configured) == target:
                return cfg.get("verify_ssl", self.verify_ssl)
        return self.verify_ssl

    @staticmethod
    def _normalize_base_url(url: str) -> str:
        """Canonicalize a base URL for identity comparison.

        URL identity is not string identity: scheme and hostname are
        case-insensitive, an explicit default port (443/https, 80/http) is
        equivalent to its omitted form, and a trailing slash on the path
        carries no meaning here. The path itself is case-sensitive, so it is
        preserved verbatim. Input without a parseable hostname is returned
        as-is (minus trailing slash) and simply never matches a configured
        URL, preserving the previous raw-equality behavior for it.
        """
        stripped = url.strip().rstrip("/")
        parts = urlsplit(stripped)
        if not parts.hostname:
            return stripped
        scheme = parts.scheme.lower()
        try:
            port = parts.port
        except ValueError:
            port = None
        host = parts.hostname.lower()
        if port is None or (scheme, port) in (("https", 443), ("http", 80)):
            netloc = host
        else:
            netloc = f"{host}:{port}"
        return urlunsplit((scheme, netloc, parts.path, "", ""))

    @staticmethod
    def get_config_dir() -> Path:
        """Return the XDG config directory path."""
        return CONFIG_DIR

    @staticmethod
    def get_settings_file() -> Path:
        """Return the settings file path."""
        return SETTINGS_FILE

    def get_nas_names(self) -> List[str]:
        """Return the list of configured NAS names."""
        return list(self.nas_configs.keys())

    def has_synology_credentials(self) -> bool:
        """Check if at least one NAS has credentials."""
        return bool(self.nas_configs) or bool(
            self.synology_url and self.synology_username and self.synology_password
        )

    def get_synology_config(self, nas_name: Optional[str] = None) -> Dict[str, Any]:
        """Get connection config for a specific NAS (or the first/legacy one).

        Args:
            nas_name: Key from secrets.json (e.g. 'nas1'). If None, returns
                      the first configured NAS or falls back to .env values.
        """
        if nas_name and nas_name in self.nas_configs:
            return self.nas_configs[nas_name]

        # Return first available from secrets.json
        if self.nas_configs:
            first = next(iter(self.nas_configs.values()))
            return first

        # Legacy .env fallback
        return {
            "base_url": self.synology_url,
            "username": self.synology_username,
            "password": self.synology_password,
            "verify_ssl": self.verify_ssl,
            "otp_code": self.synology_otp_code,
            "device_id": None,
        }

    def save_device_id(self, nas_name: str, device_id: str) -> bool:
        """Persist a DSM trusted-device token back to settings.json.

        DSM can hand back a fresh `did` on a login that already presented one.
        The old token stops working at that point, so a token kept only in
        memory survives exactly one process. MCP servers are restarted every
        session, which turns that into "2FA works once, then 403 forever".

        Rewrites only this NAS's `device_id`, preserving everything else in the
        file, and clears any spent `otp_code` alongside it. Returns True on a
        successful write.
        """
        if not isinstance(device_id, str) or not device_id.strip():
            return False
        if not SETTINGS_FILE.exists():
            return False

        try:
            data = json.loads(SETTINGS_FILE.read_text())
            synology = data.get("synology") if isinstance(data, dict) else None
            entry = synology.get(nas_name) if isinstance(synology, dict) else None
            if not isinstance(entry, dict):
                logger.warning(f"Cannot persist device_id: NAS '{nas_name}' not in settings")
                return False
            token_changed = entry.get("device_id") != device_id
            otp_present = "otp_code" in entry
            if not token_changed and not otp_present:
                return False  # unchanged, nothing to write

            entry["device_id"] = device_id
            # A one-shot OTP is spent once DSM has issued a device token.
            # Leaving it behind makes the next start look like a first-time
            # 2FA login and fail on a stale code.
            entry.pop("otp_code", None)

            # Write via a temp file in the same directory created with 0600
            # from the start (mkstemp), then rename, so the secrets never
            # briefly exist world-readable and a crash mid-write cannot
            # truncate the real file.
            fd, tmp_name = tempfile.mkstemp(
                dir=SETTINGS_FILE.parent,
                prefix=f".{SETTINGS_FILE.name}.",
                suffix=".tmp",
            )
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
                json.dump(data, tmp_file, indent=2)
                tmp_file.write("\n")
            os.replace(tmp_name, SETTINGS_FILE)

            # Keep the in-memory copy in step with disk.
            if nas_name in self.nas_configs:
                self.nas_configs[nas_name]["device_id"] = device_id
                self.nas_configs[nas_name]["otp_code"] = None

            logger.info(f"Persisted refreshed device_id for '{nas_name}'")
            return True
        except (OSError, json.JSONDecodeError) as e:
            logger.error(f"Failed to persist device_id for '{nas_name}': {e}")
            return False

    def resolve_base_url(self, nas_name: str) -> Optional[str]:
        """Get the base_url for a NAS name, or None if not found."""
        cfg = self.nas_configs.get(nas_name)
        return cfg["base_url"] if cfg else None

    def validate_config(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []
        if not self.has_synology_credentials():
            errors.append("No Synology credentials found in secrets.json or .env")
        if self.default_session_timeout < 60:
            errors.append("SESSION_TIMEOUT must be at least 60 seconds")
        return errors

    def __str__(self) -> str:
        nas_names = ", ".join(self.nas_configs.keys()) if self.nas_configs else "none"
        return f"SynologyConfig(nas=[{nas_names}], auto_login={self.auto_login})"


# Global config instance
config = SynologyConfig()
