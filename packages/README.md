# Shared Packages

Reusable code that must be available to more than one skill lives here as a
normal Python or JavaScript package. Skills should import the package they
bundle, not sibling skill source directories.

Small shared runtime helpers such as `cadpy_metadata` should still use this
pattern: source lives once under `packages/`, then each consuming skill gets its
own generated `scripts/packages/<package>` copy.

For local development, install packages editable into the repo venv. For
production packaging, build wheels or install into a skill-local Python target
such as `vendor/python`.

`meshshot` is a mixed package: Python owns browser orchestration while its
generated browser runtime is built from the package's JavaScript source and
vendored with the consuming skill.
