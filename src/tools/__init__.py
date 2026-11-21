"""Tool catalog: one declaration per tool, aggregated in registration order.

A tool's description, JSON schema, handler and MCP safety annotations live in
the same `ToolSpec`, so there is no second table to keep in sync. Adding a tool
is adding one spec; forgetting its classification is impossible.
"""

from tools._common import ADDITIVE, DESTRUCTIVE, READ_ONLY, ToolSpec
from tools.container import TOOLS as _CONTAINER
from tools.download_station import TOOLS as _DOWNLOAD_STATION
from tools.file_station import TOOLS as _FILE_STATION
from tools.health import TOOLS as _HEALTH
from tools.iscsi import TOOLS as _ISCSI
from tools.nfs import TOOLS as _NFS
from tools.reverse_proxy import TOOLS as _REVERSE_PROXY
from tools.session import LOGIN_TOOL, LOGOUT_TOOL
from tools.session import TOOLS as _SESSION
from tools.shares import TOOLS as _SHARES
from tools.update_security import TOOLS as _UPDATE_SECURITY
from tools.users import TOOLS as _USERS

_ALL = (
    *_SESSION,
    *_FILE_STATION,
    *_DOWNLOAD_STATION,
    *_HEALTH,
    *_UPDATE_SECURITY,
    *_ISCSI,
    *_CONTAINER,
    *_NFS,
    *_SHARES,
    *_REVERSE_PROXY,
    *_USERS,
)

_NAMES = [spec.name for spec in _ALL]
_DUPLICATES = {name for name in _NAMES if _NAMES.count(name) > 1}
if _DUPLICATES:
    raise ValueError(f"duplicate tool specs: {', '.join(sorted(_DUPLICATES))}")
if len(_ALL) != 107:
    raise ValueError(f"expected 107 unconditionally registered tools, got {len(_ALL)}")

# Catalog order is module grouping order; tools/list has no ordering contract.
ALL_TOOLS = tuple(_ALL)

__all__ = [
    "ADDITIVE",
    "ALL_TOOLS",
    "DESTRUCTIVE",
    "LOGIN_TOOL",
    "LOGOUT_TOOL",
    "READ_ONLY",
    "ToolSpec",
]
