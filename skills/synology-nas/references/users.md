# User & group management

## Tools

| Tool | Purpose |
|------|---------|
| `synology_list_users` | List all DSM users |
| `synology_get_user` | Get one user's details |
| `synology_create_user` | Create a new user |
| `synology_set_user` | Update an existing user |
| `synology_delete_user` | Delete a user |
| `synology_list_groups` | List all groups |
| `synology_list_group_members` | Members of a specific group |
| `synology_add_user_to_group` | Add a user to a group |
| `synology_remove_user_from_group` | Remove a user from a group |
| `synology_get_user_permissions` | Get a user's share-level permissions |
| `synology_set_user_permissions` | Set a user's share-level permissions |

All accept `nas_name` / `base_url`.

### Identifier parameter — `name` vs `username`

The tools are **inconsistent** about what the user-identifier parameter is called. Getting this wrong is a schema validation error, not a silent no-op:

| Tool | Identifier param |
|------|------------------|
| `synology_get_user` | `name` |
| `synology_create_user` | `name` |
| `synology_set_user` | `name` (plus `new_name` to rename) |
| `synology_delete_user` | `name` |
| `synology_get_user_permissions` | `name` |
| `synology_set_user_permissions` | `name` |
| `synology_add_user_to_group` | **`username`** |
| `synology_remove_user_from_group` | **`username`** |

The two group-membership tools are the exception — they take `username`, everything else takes `name`. `synology_list_group_members` takes `group` (not `group_name`).

## When to use this domain

User management is **admin-level**. The MCP must be authenticated as a user with admin rights for these calls to succeed. If the user is connecting with a non-admin account (recommended for safety), most of these tools will fail with permission errors — surface that clearly rather than retrying.

The README warns against running the MCP as a primary admin account. If user-management actions are needed, the user should temporarily authenticate with an admin account, do the work, and switch back. Don't try to elevate from inside Claude.

### When error 105 comes back

Error 105 is DSM's "the logged in session does not have permission". Report it and stop — don't retry with a different password, a different group, or a tweaked parameter name. A `synology_delete_user` against a user that doesn't exist returns 105 too, so the code says nothing about the arguments you sent; it's the session's privilege on that method. Route the user to DSM Control Panel → User & Group to do the work by hand.

## Read before write

- Before `synology_set_user`: call `synology_get_user` to see the current settings, so updates merge cleanly instead of overwriting fields unintentionally.
- Before `synology_add_user_to_group` / `synology_remove_user_from_group`: `synology_list_group_members` to confirm the current state.
- Before `synology_set_user_permissions`: `synology_get_user_permissions` to see what's already granted.

DSM's user model is additive (group membership grants permissions, plus per-user overrides). Reading first prevents accidental privilege escalation/demotion.

## Workflow patterns

### Creating a new user

`synology_create_user` requires `name` and `password`. The complete set of accepted fields:

- `name` — username (required).
- `password` — required.
- `email` — for password recovery and notifications.
- `description` — surface this when listing users.
- `cannot_chg_passwd` — boolean, default `false`.
- `passwd_never_expire` — boolean, default `true`. (Note the abbreviated `passwd`, not `password`.)
- `nas_name` / `base_url`.

**There is no `groups` parameter on `synology_create_user`.** Group membership is a separate call — create the user first, then `synology_add_user_to_group(username=..., groups=[...])`. Putting someone in `administrators` is a meaningful trust decision — confirm before doing it.

To disable an account, use `synology_set_user(name=..., expired="now")`; `"normal"` re-enables it. `expired` is an enum on `set_user`, not a date, and it does not exist on `create_user`.

### Setting share permissions

`synology_set_user_permissions(name=..., permissions=[...])` controls per-share access. Each permission entry is:

```json
{
  "name": "Photos",
  "is_writable": true,
  "is_deny": false
}
```

Only `name` (the shared folder name) is required per entry; `is_writable` and `is_deny` are booleans. There is no `share_name` key and no `"rw"`/`"ro"`/`"no_access"` string values — that grammar is:

| Intent | Entry |
|--------|-------|
| Read/write | `{"name": "Photos", "is_writable": true}` |
| Read-only | `{"name": "Photos", "is_writable": false}` |
| Deny | `{"name": "Photos", "is_deny": true}` |

Pass an array of entries; not-mentioned shares retain their existing permission. To revoke access, send an explicit `is_deny: true` entry — don't just omit the share.

### Deleting users

`synology_delete_user` is permanent. The user's home directory may or may not be removed depending on DSM's "preserve home" setting. Ask the user whether they want home-folder cleanup as a separate step (using `delete` against `/homes/<user>`).

## Gotchas

- **Built-in users**: `admin`, `guest`, and any DSM-built-in accounts behave specially. Don't try to delete `admin` (DSM may refuse). Don't change `guest`'s group memberships unless the user knows what they're doing.
- **Password requirements**: DSM enforces minimum length and complexity. If `synology_create_user` fails with a password error, surface DSM's specific requirements rather than retrying with a slightly different password.
- **Group changes don't always take effect immediately for active sessions** — a user logged in when added to a group may need to log out and back in to see new permissions on shares.
- **Deleting a user doesn't revoke active sessions automatically.** If you're removing access urgently, also tell the user to invalidate sessions in DSM Control Panel → Security → Account → Online Users.
- **Quota** is a separate feature managed via shared-folder quota or volume quota — not directly exposed in user-management tools here.

## Examples

### "Create a user 'photographer' with read-only access to Photos"

```
synology_create_user(
  name="photographer",
  password="<strong-pass>",
  description="Read-only photo access",
)
synology_add_user_to_group(              # groups are a separate call
  username="photographer",
  groups=["users"],
)
synology_set_user_permissions(
  name="photographer",
  permissions=[{"name": "Photos", "is_writable": False}]
)
```

### "Who's in the administrators group?"

```
synology_list_group_members(group="administrators")
```

### "Remove user 'temp-contractor'"

```
synology_get_user(name="temp-contractor")            # confirm it's the right one
synology_delete_user(name="temp-contractor")         # permanent
# optionally:
delete(path="/homes/temp-contractor")                # cleanup home dir
```

Confirm both steps with the user before running.
