# Plugins

This directory contains plugin packages for production agent installs. Local
development should link the source skills directly, as described in
`CONTRIBUTING.md`.

## CAD

`plugins/cad` is the first plugin package. It is versioned as `0.1.0`
and bundles every supported CAD Skills skill through symlinks into the canonical
`skills/` directories.

The plugin is intentionally thin:

- `plugins/.claude-plugin/marketplace.json` describes the Claude Code
  marketplace package list.
- `plugins/cad/.codex-plugin/plugin.json` describes the Codex plugin.
- `plugins/cad/.claude-plugin/plugin.json` describes the Claude Code plugin.
- `plugins/cad/gemini-extension.json` describes the Gemini CLI extension.
- `plugins/cad/VERSION` records the package version.
- `plugins/cad/skills/` points to the live skill sources.

## Provider Support

The `cad` plugin currently has native provider manifests for:

- Codex: `plugins/cad/.codex-plugin/plugin.json`
- Claude Code: `plugins/cad/.claude-plugin/plugin.json`
- Gemini CLI: `plugins/cad/gemini-extension.json`
