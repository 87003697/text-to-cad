# Scripts

Durable repository commands are grouped by purpose:

- `build.sh`: universal generated-runtime build wrapper.
- `test.sh`: universal repo validation wrapper.
- `build/`: generated skill runtime builds and checks.
- `catalog/`: model catalog upload and download helpers.
- `check/`: repo validation suites.
- `dev/`: local skill install/uninstall wiring.
- `release/`: release metadata helpers.
- `viewer/`: CAD Viewer repository sync helpers.
- `git-hooks/`: hook entrypoints used by local Git configuration.

Most shell commands live in their grouped directories. Keep only broad
compatibility wrappers at the root of `scripts/`.

Release version bumps:

```bash
scripts/release/bump-version.py patch --dry-run
scripts/release/bump-version.py patch
```
