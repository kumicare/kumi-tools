import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { Type } from "typebox";

function readConfig(config) {
  const supabaseUrl =
    typeof config.supabaseUrl === "string" ? config.supabaseUrl.trim().replace(/\/+$/, "") : "";
  const supabaseAnonKey =
    typeof config.supabaseAnonKey === "string" ? config.supabaseAnonKey.trim() : "";

  if (!supabaseUrl) {
    throw new Error("kumi-tools config missing supabaseUrl");
  }
  if (!supabaseAnonKey) {
    throw new Error("kumi-tools config missing supabaseAnonKey");
  }

  return { supabaseUrl, supabaseAnonKey };
}

async function listFamilyMembers(config) {
  const response = await fetch(`${config.supabaseUrl}/rest/v1/rpc/list_family_members`, {
    method: "POST",
    headers: {
      apikey: config.supabaseAnonKey,
      Authorization: `Bearer ${config.supabaseAnonKey}`,
      "Content-Profile": "kumi_core",
    },
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Supabase list_family_members failed (${response.status}): ${body}`);
  }

  return await response.json();
}

export default definePluginEntry({
  id: "kumi-tools",
  name: "Kumi Tools",
  description: "Agent tools for Kumi family operations.",
  register(api) {
    api.registerTool({
      name: "list_family_members",
      description: "List family member summaries known to Kumi Brain.",
      parameters: Type.Object({}),
      async execute() {
        const config = readConfig(api.pluginConfig);
        const members = await listFamilyMembers(config);
        return {
          content: [{ type: "text", text: JSON.stringify(members, null, 2) }],
          details: { members },
        };
      },
    });
  },
});
