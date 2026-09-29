# Changelog

## [Unreleased]

## [1.8.0] - 2026-09-29

A credential-handling release. Every entry under **Security** below is a path by which a live
secret could reach somewhere it outlives the session — a conversation transcript, a log file, a
container image, or a URL. Two of them change observable behaviour, so read **Changed** before
upgrading.

### Security
- **DSM credentials no longer travel in the query string.** `SynologyAuth.login()` and `.logout()`
  called `auth.cgi` with `requests.get(..., params=payload)`, which puts `passwd`, `otp_code` and
  `_sid` in the URL — and DSM's own nginx access log, plus every proxy in between, writes the full
  request line. They now POST the same parameters as a form body, which is what the DSM web UI
  sends. Both calls also gained the `timeout=15` every other request in the codebase already had:
  a NAS that accepted the connection and then stalled hung the startup auto-login loop, and hung
  `relogin()` while it held `_relogin_lock`, wedging session recovery for every caller behind it.
- **Redirects are refused on those same calls (CWE-200).** `requests` follows redirects by default
  and replays method and body verbatim on a 307/308, so moving to POST would have handed the
  password, OTP and device token to whatever `Location` named — and `verify_ssl` defaults to
  `False`, which puts forging one within reach of anyone on the link. `raise_for_status()` does not
  help (it raises for 4xx/5xx and lets 3xx through), so the check is explicit and a 3xx is treated
  as a failure rather than a retry — retrying would only put three more copies of the password on
  the wire. See **Changed** for the deployment consequence.
- **`synology_login` no longer echoes session credentials into the tool result.** The handler
  returned `Session ID: {sid}` followed by the raw DSM response, which carries `synotoken` and the
  long-lived trusted-device token. A tool result is written into the MCP client's conversation
  transcript, which outlives the session by a long way and travels wherever that transcript goes.
  The SID is now truncated the way `synology_logout` already truncated it, and the response is not
  echoed. Verified on DSM 7.3.2-86009 Update 4 that an ordinary login — 2FA switched off — still
  returns an 86-character device token, so this was not a 2FA-only exposure. The token is still
  printed for the one call that cannot work without it, the first-time 2FA bootstrap, which the
  tool's own description promises; it is not printed on any other login.
- **The DEBUG frame log is redacted.** At `LOG_LEVEL=DEBUG` the bridge logged raw JSON-RPC frames,
  so a `tools/call` for `synology_login`, `synology_create_user`, `ds_create_task` or
  `synology_target_create` wrote a live password to `./logs` — a mounted volume, reached through a
  documented setting. Credential-bearing keys are now masked rather than dropped, so the log keeps
  the shape that makes it worth having. The redaction is key-based and its own docstring says so:
  a `tools/call` **result** is a single free-text JSON string and is still logged verbatim.
- **The container image no longer contains `.env`.** The Dockerfile ended with `COPY .env* ./`, and
  `.dockerignore` did not exclude it, so on any machine where the README was followed and
  `SYNOLOGY_PASSWORD` put in `.env`, that password was copied into an image layer — readable with
  `docker history`, and not removed by deleting the file in a later layer. It is now supplied at
  run time instead, and `.dockerignore` excludes it so a future `COPY . .` cannot reintroduce it.
- **Session IDs are scrubbed from error messages.** Every DSM call carries `_sid=<live session id>`
  in its query string, and `requests`/`urllib3` embed the full request URL in the exception text
  they raise (`Max retries exceeded with url: …&_sid=…`). The error handlers stringified that into
  `error.message`, which the tool handlers `json.dumps` straight into a result — so an unreachable
  NAS, the most ordinary failure there is, wrote a live session credential into the transcript.
  Both the API client and `auth.synology_auth` (which builds its own messages) now scrub credential
  values on the way past, keeping the host, port and cause that make the error worth reading.
- **The iSCSI CHAP secret is no longer logged.** `chap_password` was absent from the redaction key
  list, so `synology_target_create` wrote an iSCSI CHAP secret to `./logs` in the clear at DEBUG.

### Fixed
- **The first disconnect permanently killed the Xiaozhi client.** The reconnect loop's cleanup read
  `websocket.closed`, an attribute of the legacy `WebSocketClientProtocol` that the asyncio
  `ClientConnection` returned by `websockets.connect` has not had since v14 — and this project
  pins `websockets>=17.0.1`. Because the read sat in a `finally`, the `AttributeError` propagated
  out of the `while` loop and out of `_xiaozhi_client` entirely, so the backoff below it was
  unreachable once a connection had been established. `close()` is idempotent, so the guard bought
  nothing and is gone.
- **A disabled DSM account was indistinguishable from a version mismatch.** `login_with_session`
  returns the DSM error body for codes that describe *this account* rather than the API version —
  but 401 was missing from that list, so a disabled account was retried across all four API
  versions, putting the password on the wire four times, and then reported as `unknown`. It now
  short-circuits like the others.
- **Actionable login failures were being discarded.** The failure branch replaced the response dump
  with a code plus DSM's table, on the stated basis that the body is "a DSM error code and nothing
  else". That stopped being true: `SynologyAuth` raises its own string codes, and
  `unexpected_redirect` carries the fix — the URL to configure instead. Such messages are now
  passed through, and the table is no longer shown for codes that are not in it.
- **`unknown` no longer sends the operator the wrong way.** It is what `SynologyAuth` returns once
  every API version has been tried without yielding a code it recognises, which covers both "nothing
  answered" and "DSM answered with a code outside the set". The message now names both rather than
  asserting silence, which pointed a NAS that had answered every request at a network fault.
- **Four eviction tests failed on `main`.** The fixture seeded a session key with a trailing slash
  that no code path can produce — `_login_nas`, `_handle_login` and `_resync_session_after_relogin`
  all store normalized keys, and `_get_base_url` normalizes the lookup. The tests were asserting
  against a server state that does not exist; the behaviour they describe works. The trailing slash
  is now exercised where it can legitimately appear, as caller input.

### Changed
- **A deployment that relied on being redirected to reach DSM now fails, by design.** If you use
  the "automatically redirect HTTP to HTTPS" DSM option, or a reverse proxy in front of it, point
  the configured URL at the final `https://` endpoint instead. Following the redirect would replay
  the request body — password, OTP, device token, or SID — to whatever host it named. The failure
  message names the cause and the fix. `auth.cgi` answers 200 on both `:5000` and `:5001` and issues
  no redirect on DSM 7.3.2-86009 Update 4, so the default setup is unaffected.
- **`ToolFailure` is renamed `ToolFailureError`** (ruff `N818`). It is internal and not re-exported,
  but `mcp_server` is a top-level module of the published distribution, so an importer using the old
  name needs to update. Nothing in the repository referenced it outside its own tests.
- **`docker-compose.yml` reads `.env` at run time.** `env_file` is `required: false`, so a
  `settings.json`-only setup — which never creates a `.env` — still starts; a bare `env_file` entry
  aborts compose when the file is absent. The entries are injected as container environment
  variables; the file itself is not mounted and is not visible inside the container.
- **Login failure output is reworded** to name the reason rather than dump the response body. The
  two duplicated "Common codes" strings, which had already drifted apart, are now one constant.
- **The bridge only redacts frames when DEBUG is enabled.** The redaction was the argument to a
  `logger.debug()` call, which Python evaluates whether or not the record is emitted, so every
  bridged frame paid a full recursive copy and re-serialisation at the default level — 3.74 ms on a
  356 KB frame, against 0.68 ms now.
- **Dependabot action bumps:** `actions/checkout` 4 → 7, `actions/setup-python` 5 → 7,
  `actions/upload-artifact` 4 → 7, `actions/download-artifact` 4 → 8.

### Added
- **CI on every pull request and push to `main`** — `ruff check src/ tests/` on a pinned ruff, and
  `pytest` across Python 3.11, 3.12 and 3.13 with `fail-fast: false`. Until this existed the only
  workflows were a PyPI publish that fires on a release tag and a label sync, so the first time
  anything was checked was when a release was built: twelve ruff findings and four failing tests had
  accumulated on `main`. Live-NAS tests are excluded twice over — no credentials reach the runner,
  so the `env_check` fixture skips them, and a `-m "not real_nas and not destructive"` filter holds
  even if secrets are wired in later. Both jobs carry `timeout-minutes`, and the workflow supports
  `workflow_dispatch`.
- **`synology_login` surfaces DSM's trusted-device token under both field names.** DSM names it
  `device_id` at `SYNO.API.Auth` v7 and `did` at v6 and v3; the bootstrap prefers v6 but falls
  through to v7, so both are read. Worth flagging separately: `login_with_session`, `_login_nas`
  and `config.save_device_id` still read only `did`, so on a v7-served login the token is neither
  cached nor persisted. That looks like the root cause of the v6-preference workaround, and is not
  addressed here.
- `tests/test_bridge_log_redaction.py` re-checks the redaction key list against the live tool
  schemas, so a new credential field fails the suite rather than logging silently — for names the
  guard's pattern recognises as credential-shaped; one it does not anticipate still needs the set
  updated by hand, as the comment now says.

## [1.7.1] - 2026-09-22

### Fixed
- **The Windows ACL audit rejected the very lockdown the README documents.** Trustee SIDs were stringified with `str()`, but pywin32's `PySID.__str__` returns a `PySID:`-prefixed repr (e.g. `PySID:S-1-5-32-544`), so the well-known allowlist literals for `NT AUTHORITY\SYSTEM` and `BUILTIN\Administrators` never matched — only the current user's own ACE passed, and a DACL created with the README's own `icacls` script failed the fail-closed check at startup, steering users toward `SYNOLOGY_MCP_ALLOW_UNVERIFIED_WINDOWS_ACL=true`. Both sides of the comparison now go through `win32security.ConvertSidToStringSid`, which yields the canonical locale-independent `S-1-…` form (#97). The test fakes now model `PySID`'s prefixed `str()` and value equality, so this class of bug fails the suite instead of shipping.

## [1.7.0] - 2026-09-22

### Added
- **Published to PyPI as [`mcp-server-synology`](https://pypi.org/project/mcp-server-synology/).** The
  distribution was renamed from `synology-mcp` (already taken on PyPI by an unrelated project);
  the `synology-mcp` console script keeps its name, and `mcp-server-synology` is installed as an
  equivalent alias so `uvx mcp-server-synology` works in MCP client configs with no local clone.
  Pushing a semver tag (`1.7.0` or `v1.7.0`) now builds and publishes the package via GitHub
  Actions, using PyPI trusted publishing (no upload token in repo secrets).
- `copy_file` performs a server-side File Station copy of one regular file and reports success only after verifying the target path and byte count. It avoids routing binary data through the model, but is deliberately documented as unsuitable for transactionally consistent backups of live SQLite databases.
- `get_file_content` supports lossless structured base64 reads with raw size, MIME type and SHA-256, while text mode now decodes UTF-8 strictly. `create_file` accepts strictly validated base64 uploads. Both directions enforce explicit size limits.
- **iSCSI provisioning (SAN Manager).** The iSCSI surface was read-only -
  `synology_lun_list` and `synology_lun_get` and nothing else - so a LUN could
  not be created through this server at all. Adds `synology_lun_create`,
  `synology_lun_delete`, `synology_target_list`, `synology_target_get`,
  `synology_target_create`, `synology_target_delete`, `synology_target_map_lun`
  and `synology_target_unmap_lun` over `SYNO.Core.ISCSI.LUN` and
  `SYNO.Core.ISCSI.Target`, in a new `src/iscsi/` module.
  Deleting a LUN or a target, and unmapping a LUN, each require `confirm: true`.
- **DSM error codes are described, and the failing call is named.** DSM answers a
  failure with a bare number, so 105 (no permission), 106 (session expired) and
  119 (dead SID) are indistinguishable and none of them says which API produced
  it. Every failed response now carries `api`, `method` and `version`, plus a
  `message` from DSM's published common-error table where the code is known. An
  unmapped code gets the call recorded and no invented description.

### Fixed
- File downloads no longer pass through `requests.Response.text`, which silently replaced or dropped bytes in databases, images and archives.
- File Station create-folder and copy/move calls now send DSM's required JSON-array fields. Copy/move completion is verified from File Station rather than trusting DSM's unreliable `found_file_num` counter.
- Synchronous File Station I/O runs in worker threads so long searches, transfers and task polling do not block the MCP event loop.
- **Session recovery covered only one of DSM's three session-expiry codes.**
  `SynologyAPIClient` re-authenticated and retried on 119 only, so **106**
  ("session timeout") and **107** ("session interrupted by duplicated login")
  were returned to the caller raw, with no recovery attempted. 107 is not
  exotic for a server that holds one long-lived `session=webui` session: any
  other login as the same account displaces it.
- **Session recovery could name the wrong SID under concurrency.** The stale SID
  was read off the client *after* the failed request, so with two calls in
  flight one could report the other's freshly-refreshed SID as the dead one -
  defeating the auth layer's de-duplication and opening a second session. The
  SID a request actually used is now captured before it is sent.
- **A slow LUN write timed out client-side while succeeding on the NAS.** The
  15-second timeout was applied to every call. A `LUN/create` that exceeded it
  returned a network error to the caller and created the LUN anyway, leaving a
  LUN whose uuid nobody held. LUN create and delete now use a longer timeout,
  and the per-call timeout is configurable.

## [1.6.0] - 2026-08-20

### Added
- **DSM 2FA/OTP support with trusted-device reuse.** `SYNO.API.Auth` login accepts an `otp_code`, so accounts with 2FA enabled can be used without the prior "create a non-2FA account" security downgrade. First-time login returns a device token (`did`); later logins pass `device_id` and skip OTP entirely, and the token is persisted so it survives a restart. The DSM error-119 recovery path reuses the cached `did`, keeping silent session recovery working on 2FA accounts. First-time bootstrap prefers API v6. (#56, #75, #77, closes #54)
- **Native Streamable HTTP transport.** Setting `MCP_HTTP=true` serves MCP 2.0 Streamable HTTP from in-process uvicorn — configurable via `MCP_HTTP_HOST`/`MCP_HTTP_PORT`/`MCP_HTTP_PATH` (default `/mcp`) or a `server.http` block in `settings.json` — replacing the `mcp-proxy` sidecar. DNS-rebinding protection is on by default, with `MCP_HTTP_ALLOWED_HOSTS`/`MCP_HTTP_ALLOWED_ORIGINS` for deployments behind a reverse proxy. (#81)
- **iSCSI LUN tools** — `synology_lun_list` (name, UUID, size, used space, status, mapped targets, backing volume) and `synology_lun_get`, which resolves a single LUN client-side from the list response rather than making a second round trip. Both follow the existing read-only `synology_volume_status`/`synology_storage_pool` pattern. (#84, closes #64)
- **Container health and disk-usage summaries**, plus `synology_container_image_prune` and a read-only `synology_container_image_prune_preview`. Pruning goes through Container Manager APIs only and stays image-only — containers, networks and build cache are preserved — fails closed on incomplete or malformed inventories, and reports dangling images DSM cannot safely delete rather than pretending they were removed. (#87)
- Per-NAS `url` key in `settings.json`: when present it is used verbatim (trailing slash stripped) and wins over `host`/`port`, so a NAS published through a reverse proxy on the default 443 — whose URL carries no port — can finally be addressed. Non-string values are rejected with a warning and skip only that entry. (#76)
- Per-NAS `verify_ssl`, applied to every client rather than only some. (#78)
- On-demand login for `nas_name`. `nas_name_map` was populated only by startup auto-login, so with `AUTO_LOGIN=false` every `nas_name` call failed and the only way in was `synology_login` — which takes the password as a tool argument, writing it into the MCP client's transcript. Share listings also return timestamps. (#67)
- Windows ACL enforcement on `settings.json` before load: a full audit via pywin32 checking that the owner SID matches the current user and that the DACL carries no wider grant. The Windows branch now dispatches on `os.name == "nt"` before the POSIX `hasattr(os, "getuid")` guard that had made it dead code. **This is fail-closed** — if pywin32 is not installed the server refuses to load the credentials rather than loading them unverified; set `SYNOLOGY_MCP_ALLOW_UNVERIFIED_WINDOWS_ACL=true` to accept the risk and load anyway. Runtime errors from `win32security` also fail closed. (#83)

### Fixed
- `list_directory` and `get_file_info` reported **every file as 0 bytes**. DSM returns the byte count inside the entry's `additional` block (which is where we ask for the `size` field), but both handlers only read a top-level `size` key that DSM never populates. Sizes are now read from `additional.size`, falling back to the top-level key for API versions that inline it. (#59)
- `search_files` always failed with `Synology API error: 103` ("no such method"). It polled `SYNO.FileStation.Search`'s `status` method, which does not exist on DSM 7.3.2 — the `list` method reports completion via its own `finished` flag. Polling now goes through `list`, and cleanup calls `clean` as well as `stop` to release the task slot. (#59)
- `search_files` now retries when DSM discards a freshly started search task. Measured live on DSM 7.3.2, roughly 60% of tasks vanish immediately after `start` returns their id, answering `{"finished": true}` with no `total`/`files` — indistinguishable from an unknown taskid. The retry loop makes results deterministic; without it, a single search silently returned 0 matches most of the time. (#59)
- `search_files` pages through results instead of returning only the first page, and its matches now carry real byte sizes. (#59)
- `get_file_info` reported a **nonexistent path as an existing file**. DSM doesn't fail the request for a missing path — it answers `success: true` with a per-entry error (`{"code": 408, "path": ...}` and no `name`), and the handler passed that straight through as `{"name": null, "size": 0}`. Every existence check built on this tool therefore answered "yes". It now raises for that response. (#61)
- `move_file` could not do what it documented. It hands the destination to `SYNO.FileStation.CopyMove` as `dest_folder_path`, which only accepts an existing folder, so the documented "full path with new name" form failed with `1002`/`408`. It now renames around the move to support that form — and picks the safe order, renaming first unless the new name is already taken in the source directory, in which case it moves first and renames after. (#61)
- `synology_disk_smart` never returned SMART attributes. `disk_smart_info()` queried `SYNO.Core.Storage.Disk/get_smart_info`, which does not exist on DSM 6 or 7, and its DSM 6 fallback called `SYNO.Storage.CGI.Smart/get` **without forwarding `extra_params`** — asking DSM for SMART data without naming a disk. On DSM 7.3.2 the tool returned error 103; on DSM 7.1.1 it returned `success: true` with an empty `hddinfo`, so a monitoring caller saw "no problems" from a query that never looked at a drive. The attribute table now comes from `SYNO.Storage.CGI.Smart/get_smart_info` keyed by the disk's **device path** (`device=/dev/sata1`); a bare name is rejected by DSM with error 117, so `disk_id` accepts either the id (`sata1`, `sda`, `nvme0n1`) or a full `/dev` path and is normalized, falling back to a `disk_list()` lookup when the two diverge. Verified against DSM 7.1.1-42962 (DS214play) and DSM 7.3.2-86009 (RS822RP+), returning 19–20 attributes per drive. (#57)
- DSM error-119 auto-recovery now extends to FileStation. #27 added the single-retry re-auth but scoped it to `SynologyAPIClient`; `SynologyFileStation` keeps its own `_sid` and issues requests through its own `_make_request()`, so it never went through the patched client and a 119 surfaced straight to the caller. (#63)
- Group-membership writes are verified rather than assumed, and the member-listing limits are documented. (#90, #88)

### Changed
- Migrated to the **mcp 2.0** constructor-callback API (`on_list_tools`/`on_call_tool`); the decorator-based API this server used (`@server.list_tools()`, `@server.call_tool()`) was removed upstream in mcp 2.0.0. The legacy v1 bridge surface (`handle_call_tool()`, `call_tool_direct()`, `get_tools_list()`), a dead `_websocket_handler()`, and the hardcoded bridge server name/version went with it. (#68, #69)
- Tool dispatch now runs off a registry. A 50-branch `if/elif` chain in `_dispatch_tool` and a parallel 300-line `_get_tool_definitions()` were replaced by a single registry of ~85 tools (83 plus `synology_login`/`synology_logout`, which are registered only when auto-login is unavailable), so a tool's schema and its handler are declared together in one place instead of in two lists kept in sync by hand. (#82)
- `search_files` documents DSM's actual matching rule: `pattern` is a case-insensitive **substring** of the entry name, and wildcards are not special (`*.dcm`, `dcm` and `*dcm*` all return the same entries). The tool description previously advertised glob-style wildcards.
- The README's remote-deployment section documents the transport the server actually runs. It still described an `mcp-proxy` sidecar wrapping `main.py` over stdio and a `/sse` connector URL, but the server has served native Streamable HTTP from uvicorn at `MCP_HTTP_PATH` (default `/mcp`) since `docker-compose.http.yml` moved off the proxy. The security note now says plainly that there is no application-level authentication, and that the `MCP_HTTP_ALLOWED_HOSTS`/`MCP_HTTP_ALLOWED_ORIGINS` DNS-rebinding allowlist is not a substitute for it. (#93)
- Removed `requirements.txt` and `requirements-http.txt`; `pyproject.toml` is now the single source of truth for dependencies. Test dependencies moved to the `test` extra (`pip install ".[test]"`), and the Docker image installs the project with `pip install .`. The dead `INSTALL_HTTP` build arg (a no-op since `mcp>=2.0.0` bundled uvicorn) is gone. `pyproject.toml` also gained full project metadata and a synced CLI entry point. (#92, #74)
- The bundled `synology-nas` Agent Skill documents the correct user and NFS tool parameters. (#89)
- The test suite runs on Windows. (#66)
- Dependency bumps: `mcp` `>=2.0.0`, `websockets` `>=17.0.1`, `setuptools` `>=84.0.0`. (#53, #55, #58, #65, #85)

## [1.5.0] - 2026-06-27

### Added
- **Container Manager support** — ~30 new MCP tools for Synology DSM Container Manager (Docker), spanning containers (list/get/start/stop/restart/delete/logs/resource), compose projects (list/get/create/update/start/stop/restart/build/clean/delete), images (list/get/delete/pull), registries (list/search/tags/download), and networks (list/get/create/delete). They reuse the existing per-NAS session caching and multi-NAS targeting, and destructive operations require explicit names. The `synology-nas` Agent Skill gains a Container Manager domain (`references/containers.md`, GHCR + runtime-DNS gotchas, and an eval). Thanks @denisdasilvarocha. (#44)
- `synology_container_logs` exposes `offset`/`limit` pagination (defaults `0`/`1000`, bounded `offset >= 0` / `limit >= 1`) instead of a hardcoded 1000-line query. (#46, #49)

### Fixed
- `synology_logout` now evicts **all** per-domain service-instance caches (health, container, NFS, user management), not just FileStation/DownloadStation, so no stale instance lingers on a dead session — and the same applies to the graceful expired-session path. That branch now coerces the DSM error code with `str()` before matching, so DSM's **numeric** `105`/`106` (returned via JSON) hit the cleanup path instead of falling through to the failure branch. (#48, closes #47)
- `update_project` JSON-encodes the service-portal name/protocol consistently with `create_project`, so portal-config updates reach DSM correctly; `_project_id` tolerates non-dict project payloads instead of raising on lookup. (#44)

### Changed
- Container Manager API versions are typed as `int` to match `SynologyAPIClient.post()`, and `list_registry_tags` routes its v2 call through a named `registry_tags_version` field instead of a bare literal. (#45, #50, #49, #51)
- Extracted a single `_service_instance_dicts()` helper so session login, relogin, logout, and cleanup all evict the same canonical cache set. (#48)
- Dependency bumps: `mcp` `>=1.28.0`, `mcp-proxy` `>=0.12.0`, `pytest` `>=9.1.1`, and `actions/checkout` to v7. (#39–#43)

## [1.4.2] - 2026-06-12

### Added
- Optional HTTP/SSE transport for remote deployments via `docker-compose.http.yml` (mcp-proxy). The extra dependency is isolated in `requirements-http.txt` and only installed when the image is built with `INSTALL_HTTP=1`/`true`; the default stdio/Xiaozhi image is unchanged. (#25, #36)

### Fixed
- Transparent recovery from DSM error 119 ("SID not found"). When a server-side session expires — typically after ~1h of inactivity on `SYNO.Core.*` APIs — `SynologyAPIClient` now re-authenticates with the cached credentials and retries the call once instead of failing until the process restarts. The relogin is concurrency-safe (serialized per NAS, so simultaneous 119s collapse into a single new session rather than leaking orphaned SIDs) and resyncs `mcp_server`'s cached SID/token and lazily-built service instances, so a later logout targets the live session. A failed auth-module import on the recovery path is now logged instead of silently swallowed. (#27, #37)

### Changed
- Hardened the HTTP/SSE Docker build and isolated the mcp-proxy dependency from the core image. (#36)
- Bumped `mcp` to `>=1.27.2` and `pytest-asyncio` to `>=1.4.0`. (#26, #28)
- CI: gate `@claude` and PR-review workflows to trusted users, support fork PRs via `pull_request_target`, and skip Dependabot/fork runs where appropriate. (#29–#33)

## [1.4.1] - 2026-05-05

### Fixed
- `system_info`: use `SYNO.DSM.Info` version 2 as fallback on DSM 7.x — version 1 is below `minVersion` and returns error 104; `SYNO.DSM.Info/getinfo/v2` returns model, serial, DSM version string, RAM, temperature, and uptime successfully. Thanks @leto1210. (#17)

### Changed
- Hardened Claude Code workflows: skip runs on bot-triggered events, add `id-token: write` for claude-code-action OIDC, refresh Dependabot config with PR limits and labels, and add label-sync + issue-triage workflows. (#18, #19)

## [1.4.0] - 2026-05-01

### Added
- `synology-nas` Anthropic Agent Skill at `skills/synology-nas/` — teaches Claude how to use the MCP tools effectively (multi-NAS targeting, aggregate health checks, path conventions, per-domain workflows for files/downloads/health/NFS/users). Works in Claude Code, Claude Desktop, and claude.ai. (#14, closes #5)
- Claude Code `@claude`-mention reviewer workflow on PRs. (#15)

### Fixed
- CI now checks out the PR head SHA on `issue_comment` triggers so commit-aware reviews work. (#16)

## [1.3.0] - 2026-04-28

### Added
- DSM 7.3.2+ CSRF support: capture `SynoToken` at login (`enable_syno_token=yes`), thread `X-SYNO-TOKEN` through every service module, default session type changed to `webui`. Older DSM (6.x, 7.0–7.2) ignore the flag and continue to work header-less.

### Fixed
- `synology_create_share` on DSM 7.3.2 — `SYNO.Core.Share.create` now sends a JSON-encoded `shareinfo` envelope plus a top-level JSON-encoded `name`. Verified against DSM 7.3.2-86009 Update 3. (#8)
- Silent loss of `additional` field data on DSM 7.3.2 — now sent as JSON arrays in `FileStation.list_directory`, `FileStation.get_file_info`, and `DownloadStation.list_tasks`. Thanks @CynicalTyr. (#7)
- `FileStation.create_file` upload now threads `X-SYNO-TOKEN` on the direct `requests.Session().post(...)` path.

### Tests
- New regression test pinning the `create_share` wire format.
- Repaired 11 stale `test_config` tests broken by an earlier `SECRETS_FILE` → `SETTINGS_FILE` rename.

## [1.2.0] - 2026-02-27

### Added
- Unified `settings.json` configuration replacing `secrets.json` — single file for NAS credentials, Xiaozhi, and server settings. Uses XDG path `~/.config/synology-mcp/settings.json`. Supports multiple NAS devices.
- Centralized logging via Python's `logging` module with configurable levels (DEBUG/INFO/WARNING/ERROR), set in `settings.json`.
- Lint configuration in `pyproject.toml` (Ruff, Black, mypy). Codebase reformatted with Black.

### Security
- File permission enforcement: refuses to load settings with insecure permissions (e.g. 0644).
- README guidance on using dedicated accounts without 2FA.

## [1.1.0] - 2025-06-07

# 🚀 Synology MCP Server v1.1.0 - Xiaozhi WebSocket & Enhanced Docker Support

**Release Date:** June 7, 2025

🌟 **Major feature update bringing WebSocket support and enhanced multi-client capabilities!**

## 🚀 What's New

### 🤖 **Xiaozhi WebSocket Integration**
- **WebSocket-based MCP support** for [Xiaozhi ESP32](https://github.com/78/xiaozhi-esp32)
- **Dual client support** - Run both stdio (Claude/Cursor) and WebSocket (Xiaozhi) simultaneously
- **Environment-based configuration** with `ENABLE_XIAOZHI` toggle
- **Secure token authentication** for Xiaozhi connections
- **Auto-reconnection** and error recovery for WebSocket connections

### 🐳 **Enhanced Docker Support**
- **Multi-protocol Docker containers** supporting both stdio and WebSocket connections
- **Flexible deployment options** - Choose stdio-only or full WebSocket bridge mode
- **Improved environment variable handling** in containerized deployments
- **Better logging and debugging** for Docker-based setups

### 🔧 **Infrastructure Improvements**
- **Multiclient bridge architecture** for handling multiple connection types
- **Requirements validation** with helpful error messages
- **Enhanced startup diagnostics** and configuration display
- **Improved error handling** and graceful shutdown

## 📋 Configuration

### Environment Variables
- `ENABLE_XIAOZHI`: Enable Xiaozhi WebSocket bridge (true/false, default: false)
- `XIAOZHI_TOKEN`: Your Xiaozhi authentication token (required if ENABLE_XIAOZHI=true)
- `XIAOZHI_MCP_ENDPOINT`: Xiaozhi MCP endpoint (optional, defaults to wss://api.xiaozhi.me/mcp/)

### Usage Modes
- **Claude/Cursor Only**: `ENABLE_XIAOZHI=false` (default)
- **Dual Support**: `ENABLE_XIAOZHI=true` (supports both Xiaozhi WebSocket and Claude/Cursor stdio)

---

## [1.0.0] - 2025-05-31

# 🎉 Synology MCP Server v1.0.0 - Initial Release

**Release Date:** May 31, 2025

🚀 **The first stable release of Synology MCP Server is here!**

## 🌟 What's New

This initial release brings full Model Context Protocol (MCP) integration for Synology NAS devices, enabling AI assistants to seamlessly manage your NAS through natural language commands.

## ✨ Key Features

### 🔐 **Secure Authentication & Session Management**
- **Persistent session management** across multiple NAS devices
- **Auto-login functionality** with environment configuration
- **Session cleanup** on server shutdown

### 📁 **Complete File System Operations**
- **📋 List & Browse**: List shares, directories with detailed metadata
- **🔍 Search**: Find files with pattern matching (wildcards supported)
- **📝 Create**: Create files with custom content and directories
- **🗑️ Delete**: Unified delete function (auto-detects files vs directories)
- **✏️ Rename**: Rename files and directories
- **📦 Move**: Move files/directories to new locations
- **ℹ️ Info**: Get detailed file/directory information with timestamps, permissions, ownership

### 📥 **Download Station Integration**
- **📊 Monitor**: View download tasks, statistics, and system info
- **➕ Create**: Add download tasks from URLs and magnet links
- **⏸️ Control**: Pause, resume, and delete download tasks
- **📈 Statistics**: Real-time download/upload statistics

### 🤖 **Multi-Client AI Support**
- **🤖 Claude Desktop** - Full integration with Anthropic's Claude
- **↗️ Cursor** - Seamless coding assistant integration
- **🔄 Continue** - VS Code extension support
- **💻 Codeium** - AI coding assistant compatibility

### 🐳 **Easy Deployment**
- **Docker Compose** setup with one command
- **Environment-based configuration** for security
- **Auto-SSL verification** options
- **Debug logging** for troubleshooting
