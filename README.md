# kumi-tools

Stable Kumi family-operation tools. The active Phase 1 implementation is the
Hermes-native plugin under `hermes/kumi`; the root JavaScript package remains
the historical OpenClaw prototype and is not deployed to the current VPS.

## Hermes tools

- `list_family_members`
- `create_family_task`
- `list_family_tasks`
- `assign_family_task`
- `claim_family_task`
- `complete_family_task`
- `read_family_sheet`
- `list_family_calendar_events`
- `create_family_calendar_event`

The model can provide only domain inputs. The plugin obtains LINE actor,
conversation, and message identity from Hermes session context, computes a
stable operation key and payload hash, authenticates as a dedicated Supabase
user, and calls named `kumi_core` RPCs. It never accepts arbitrary URLs,
headers, household IDs, actor IDs, SQL, or service-role credentials.

Google Workspace tools follow the same boundary. The operator fixes one
spreadsheet ID, one calendar ID, and an exact worksheet allowlist in the VPS
environment. The model cannot provide file IDs, calendar IDs, URLs, OAuth
scopes, or credentials. Calendar writes are disabled independently by default
and require the user to confirm the exact event details.

## Local verification

```bash
python3 -m venv .venv
.venv/bin/pip install -r hermes/requirements-dev.txt
.venv/bin/python -m unittest discover -s hermes/tests -v
```

## VPS deployment

1. Copy `hermes/kumi` to `/home/che/.hermes/plugins/kumi`.
2. Enable with `hermes plugins enable kumi --no-allow-tool-override`.
3. Copy a populated `deploy/kumi-hermes.env.example` to
   `/home/che/.config/kumi/hermes.env`, set mode `0600`, and install the
   versioned systemd drop-ins.
4. Keep `KUMI_MUTATIONS_ENABLED=false` for the read-only spike.
5. Restart `hermes-gateway.service`, verify member/task reads in private and
   approved group contexts, then set mutations to `true`.

## Google Workspace credentials

Use a dedicated Google Cloud service account and share only the selected
spreadsheet and calendar with its `client_email`. Do not enable domain-wide
delegation. This keeps access narrower than an OAuth token for the operator's
whole Google account and avoids refresh-token expiry.

Required Google Cloud setup:

1. Create or select a Kumi-only Google Cloud project.
2. Enable **Google Sheets API** and **Google Calendar API**.
3. Create one service account without project IAM roles.
4. Create one JSON key and place it only at
   `/home/che/.config/kumi/google-service-account.json` with mode `0600`.
5. Share the `家族事務` spreadsheet with the service-account email as Viewer
   for the read-only rollout. Do not share the containing Drive folder.
6. Share the operator-approved target calendar with the service-account email.
   The current production target is Louis's existing primary calendar because
   acceptance requires querying existing events. It is shared as event writer.
   The independent Hermes mutation flag is enabled only after separate approval
   for that exact primary calendar.
7. Set `KUMI_GOOGLE_SPREADSHEET_ID`, `KUMI_GOOGLE_CALENDAR_ID`, and the exact
   `KUMI_GOOGLE_ALLOWED_SHEETS` list in `hermes.env`.

Recommended initial allowlist:

```text
注意事項,療程,行政,長照｜樂活
```

Keep `支出`, `保險`, and `銀行帳戶` excluded until Kumi has a field-level
authorization policy for financial data.

Restrict the LINE platform to the Kumi domain tools and explicitly selected
safe supporting toolsets. In `~/.hermes/config.yaml`:

```yaml
platform_toolsets:
  line:
    - kumi
    - memory
    - web
```

Do not expose the `terminal` toolset to LINE. Otherwise the model can route a
Calendar request to Hermes's generic OAuth-based Google CLI instead of the
service-account-backed Kumi tool.

After copying the updated plugin and installing `hermes/requirements-dev.txt`,
run the read-only smoke test. It prints only row and event counts, never cell or
event contents:

```bash
python ~/.config/kumi/smoke_google_workspace.py
```

Set `KUMI_GOOGLE_MUTATIONS_ENABLED=true` only after the configured calendar has
passed this read-only probe and the operator explicitly accepts that the LINE
agent may modify that exact calendar. For a primary calendar, treat this as a
separate higher-risk approval from read access.

After approval and enabling the flag, run the two-actor production write probe.
It creates one temporary event per allowed LINE identity, verifies readback,
removes both events, and prints only boolean/count metadata:

```bash
python ~/.config/kumi/verify_google_workspace_write.py
```

The versioned `patches/hermes-0.19.1-line-group-room.patch` captures the local
LINE group/room wake-word patch currently present on the pinned VPS runtime.
`patches/hermes-0.19.1-line-message-context.patch` fixes LINE's inbound
`message_id` propagation into the trusted `SessionSource`; without it, Kumi
write/read tools fail closed because they cannot build an operation identity.

## LINE Rich Menu

Phase 1.2 uses one default 3x2 Rich Menu made entirely from LINE message
actions. Message actions enter the existing Hermes text flow, so the menu does
not add a parallel command handler or require custom postback support.
Each action includes the configured `@kumi` group wake word; the LINE adapter
strips it in group/room chats before routing the request to Hermes.

- Menu object: `line/rich-menu/phase1.json`
- Editable visual source: `line/rich-menu/phase1.svg`
- LINE upload asset: `line/rich-menu/phase1.png` (`2500x843`, under 1 MB)
- Operator tool: `hermes/scripts/manage_rich_menu.py`

To change the visual style, edit the SVG and render a fresh PNG. A Rich Menu
image cannot be replaced in-place, so `deploy` creates a new menu, switches the
default, verifies it, and removes the superseded Kumi-owned menu:

```bash
sips -s format png line/rich-menu/phase1.svg \
  --out line/rich-menu/phase1.png
```

Keep labels large because LINE scales the full 2500px canvas to the phone
viewport. The current layout deliberately uses icon + 82px primary label and
no secondary copy.

The operator tool reads `LINE_CHANNEL_ACCESS_TOKEN` from the existing VPS
`~/.hermes/.env`; it never prints or copies the token. It supports local checks,
remote status, idempotent deployment, byte-for-byte verification, and guarded
rollback:

```bash
python hermes/scripts/manage_rich_menu.py check \
  --menu line/rich-menu/phase1.json \
  --image line/rich-menu/phase1.png

python manage_rich_menu.py deploy --menu phase1.json --image phase1.png
python manage_rich_menu.py verify --menu phase1.json --image phase1.png
python manage_rich_menu.py rollback
```

Deployment state contains only non-secret menu IDs and hashes and is stored at
`~/.config/kumi/rich-menu-state.json` with mode `0600`.

## Credentials

The plugin requires a Supabase project URL, publishable key, and the email and
password of one dedicated low-privilege Auth user. Store them only in the
operator-readable VPS environment file. Do not use a service-role key.

## Historical OpenClaw prototype

## Current tools

### `list_family_members`

Lists family member summaries through the stable Supabase RPC contract:

```text
kumi_core.list_family_members()
```

## Config

Configure the plugin under:

```json
{
  "plugins": {
    "entries": {
      "kumi-tools": {
        "enabled": true,
        "config": {
          "supabaseUrl": "https://uahzamhpbbzintsalqgg.supabase.co",
          "supabaseAnonKey": "<anon key>"
        }
      }
    }
  }
}
```

The agent-facing tool name should remain stable even if the implementation later moves behind a Spring Boot `kumi-api`.
