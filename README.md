# kumi-tools

OpenClaw plugin package for Kumi family-operation tools.

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
