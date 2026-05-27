# CAD Plugin

Version: `0.1.0`

This plugin bundles the supported CAD Skills skill set for production agent
installs while keeping the root `skills/` directory as the source of truth.
Local development should link source skills directly from this checkout.

Bundled skills:

- `bambu-labs`
- `cad`
- `cad-viewer`
- `gcode`
- `sdf`
- `sendcutsend`
- `srdf`
- `step-parts`
- `urdf`

Codex consumes `.codex-plugin/plugin.json`. Claude Code consumes
`.claude-plugin/plugin.json`. Gemini CLI consumes `gemini-extension.json`.

Provider behavior:

- Codex: native plugin package through `.codex-plugin/plugin.json`.
- Claude: native plugin package through `.claude-plugin/plugin.json`.
- Gemini: native extension package through `gemini-extension.json`.
