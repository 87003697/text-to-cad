# Scripts

Durable repository commands are grouped by purpose:

- `build.sh`: universal generated-runtime build wrapper.
- `test.sh`: universal repo validation wrapper.
- `build/`: generated skill runtime and plugin package builds/checks.
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
scripts/release/bump-version.sh patch --dry-run
scripts/release/bump-version.sh patch
scripts/release/bump-version.sh patch --amend
```

The shell wrapper writes the bump, commits the changed release metadata, and
creates a local release tag by default. Use `--no-commit` to only edit files.

Release prep with GitHub Releases:

```bash
scripts/release/create-github-release.sh patch --dry-run
scripts/release/create-github-release.sh patch
```

`create-github-release.sh` wraps the version bump, refreshes generated
skill/plugin outputs, runs generated-output checks and plugin validation,
commits the release metadata, tags it, pushes the current branch and tag, and
creates a draft GitHub Release with generated notes. Use `--publish` to publish
the release immediately, `--run-tests` to include `scripts/test.sh`, or
`--skip-release` when preparing only the commit and tag.
