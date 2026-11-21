"""Session and connection tools: status, configured NAS units, and the conditional login/logout pair.

Declarative tool catalog: description, JSON schema, handler and MCP safety
annotations live on one object, so a tool cannot ship without a classification.
"""

from tools._common import ADDITIVE, READ_ONLY, ToolSpec

TOOLS = (
    ToolSpec(
        name="synology_status",
        description="Check authentication status for Synology NAS instances",
        schema={"type": "object", "properties": {}, "required": []},
        handler="_handle_status",
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_list_nas",
        description="List all configured NAS units from secrets.json. Returns NAS names, URLs, and connection status.",
        schema={"type": "object", "properties": {}, "required": []},
        handler="_handle_list_nas",
        annotations=READ_ONLY,
    ),
)

# Registered only when auto-login is off or no stored credentials exist.
# Kept outside TOOLS so the unconditional catalog stays 107 entries.
LOGIN_TOOL = ToolSpec(
    name="synology_login",
    description="Authenticate with Synology NAS and establish session.\n\n2FA/OTP accounts: pass `otp_code` on the first login only; DSM will issue a `device_id` in the response, which you can persist into settings.json to skip OTP on future logins. If you already have a `device_id`, pass it instead of `otp_code` — DSM treats trusted devices as already authenticated.",
    schema={
        "type": "object",
        "properties": {
            "base_url": {
                "type": "string",
                "description": "Synology NAS base URL (e.g., https://192.168.1.100:5001)",
            },
            "username": {"type": "string", "description": "Username for authentication"},
            "password": {"type": "string", "description": "Password for authentication"},
            "otp_code": {
                "type": "string",
                "description": "One-time 6-digit code from the user's "
                "authenticator. Required only on the first 2FA "
                "login for a new device. Ignored when `device_id` "
                "is also given.",
            },
            "device_id": {
                "type": "string",
                "description": "Long-lived trusted-device token previously "
                "issued by DSM (returned as `did` in a successful "
                "2FA login). When supplied, DSM skips the OTP "
                "step. Preferred over `otp_code` for repeated "
                "logins.",
            },
        },
        "required": ["base_url", "username", "password"],
    },
    handler="_handle_login",
    annotations=ADDITIVE,
)

LOGOUT_TOOL = ToolSpec(
    name="synology_logout",
    description="Logout from Synology NAS session",
    schema={
        "type": "object",
        "properties": {"base_url": {"type": "string", "description": "Synology NAS base URL"}},
        "required": ["base_url"],
    },
    handler="_handle_logout",
    annotations=ADDITIVE,
)
