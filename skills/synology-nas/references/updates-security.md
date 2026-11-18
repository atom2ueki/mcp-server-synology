# DSM updates & security posture

Read the NAS's update and security state, and change the handful of hardening
settings DSM exposes. Every write requires `confirm=true`; the read-only pair
does not.

## Tools

| Tool | What it does |
|------|--------------|
| `synology_update_status` | Installed DSM version, the available update (version, security release, whether it reboots), its download/install state, packages with pending updates (Package Center's own `upgrade`/`repair`/`forcereplaced` test), and the package auto-update policy |
| `synology_security_posture` | QuickConnect, auto-block and its allow list, Telnet/SSH, HTTPS redirect/HSTS/ports, firewall, 2FA enforcement, admin/guest disabled, administrators (DSM user accounts only — non-user members such as service accounts are omitted), Security Advisor result/findings/schedule |
| `synology_dsm_update_download` | Start downloading the available DSM update. Installs nothing |
| `synology_dsm_update_install` | Without `confirm`: DSM's pre-install checks only. With `confirm`: install. The NAS may reboot |
| `synology_terminal_set` | Telnet/SSH on or off; the SSH port is kept |
| `synology_autoblock_set` | Auto-block enable/attempts/window/expiry. Only an actual enable transition is refused while the allow list is empty |
| `synology_autoblock_allow_add` / `_remove` | Manage the never-block allow list. Entries are named exactly as `synology_security_posture` lists them |
| `synology_https_redirect_set` | DSM's HTTP→HTTPS redirect; DSM restarts its web server |

## Ordering

1. Read before writing: `synology_security_posture` (or `synology_update_status`)
   gives the current state, and the writes return before/after around exactly
   what you asked to change.
2. For a DSM update: `synology_dsm_update_install` **without** `confirm` first —
   it runs DSM's own pre-install checks and returns blockers, warnings and any
   EULA. Only then re-issue with `confirm=true` plus the `accept_warnings=true`
   / `accept_eula=true` flags the note names. Installing reboots the NAS when
   DSM says so; the session ends and reconnects.

## Gotchas

- **Never-block before enabling auto-block**: enabling auto-block is refused
  while the allow list is empty, so this server's own address (and the LAN)
  cannot be locked out. Add allow-list entries first.
- **Lockout math**: removing this server's own IP from the allow list while
  auto-block is on is how you brick your own agent. Don't.
- **Verbatim entries**: allow-list removals must be named exactly as the
  posture lists them (a `10.0.0.1` stored as-is is not `10.0.0.1/32`); an
  unknown name is refused rather than silently no-op'd.
- **Honest failures**: these tools report `outcome: "unknown"` when DSM gives
  no answer (never blindly retry — the write may have applied), `verified:
  false` when a confirmed write's read-back failed, and `read_back_mismatch`
  when the read-back contradicts the request. Trust those fields over your
  expectation.
- **Major upgrades**: a promotion (major-version) target is refused; use DSM's
  UI for those.
