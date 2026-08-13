# PROTOTYPE — prelaunched Chromium over CDP

This throwaway prototype answers one question: can the unchanged public
`render_residual_preview(...)` call use the exact Playwright 1.60
`chrome-headless-shell` through a Python-owned loopback CDP prelaunch, without
changing meshshot's Three.js runtime, camera profile, route/fulfill payload
path, console stages, page evaluation, or eight-view PNG semantics?

It compares two adapters:

- A: Playwright `chromium.launch(executable_path=...)`.
- B: Python prelaunch of the same executable and Playwright
  `connect_over_cdp()`.

The launch profile is derived from Playwright v1.60.0's
`packages/playwright-core/src/server/chromium/chromiumSwitches.ts` and
`Chromium.defaultArgs`. Adapter B replaces Playwright's pipe-owned startup with
an isolated user-data directory and a loopback-only ephemeral CDP port. Raw
argv, endpoint, process IDs, and temporary paths never enter the result.

Run from the repository root:

```bash
/Users/zhiyuanma/Desktop/codes/text-to-cad/.venv/bin/python \
  packages/meshshot/prototypes/prelaunched_cdp/run.py
```

The command prints and rewrites `recorded-result.json`. It is intentionally a
single-purpose probe, not a production implementation or supported API.
