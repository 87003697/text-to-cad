# Installation

This guide covers installing the bundled agent skills. For CAD runtime setup,
CAD Explorer dependencies, and sample-model development, see the Local
Development section in [README.md](README.md).

The bundled skills are `cad`, `step-parts`, `cad-explorer`, `urdf`, `sdf`,
`srdf`, and `sendcutsend`.

## Quick Start

Clone the repo once:

```bash
git clone https://github.com/earthtojake/text-to-cad.git
cd text-to-cad
```

Then choose a provider:

```bash
./scripts/codex-install.sh
./scripts/claude-install.sh
./scripts/gemini-install.sh
./scripts/openclaw-install.sh
```

Or use the external universal installer:

```bash
npx agent-skills-cli add earthtojake/text-to-cad
```

Restart your agent if newly installed skills do not appear.

## Installer Script

Use the shared installer when you want a specific target, one skill, a dry run,
or symlinked project-local installs:

```bash
./scripts/install.sh --agent codex
./scripts/install.sh --agent claude --skill cad
./scripts/install.sh --agent project --link
./scripts/install.sh --agent all --dry-run
./scripts/install.sh --list
```

Supported agents:

| Agent | Default destination |
| --- | --- |
| `codex` | `${CODEX_HOME:-$HOME/.codex}/skills` |
| `claude` | `${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}` |
| `gemini` | `${GEMINI_SKILLS_DIR:-.gemini/skills}` |
| `openclaw` | `${OPENCLAW_SKILLS_DIR:-$HOME/.openclaw/skills}` |
| `cursor` | `${CURSOR_SKILLS_DIR:-.cursor/skills}` |
| `vscode` | `${VSCODE_SKILLS_DIR:-.github/skills}` |
| `goose` | `${GOOSE_SKILLS_DIR:-$HOME/.config/goose/skills}` |
| `project` | `${PROJECT_SKILLS_DIR:-.skills}` |

Options:

| Option | Behavior |
| --- | --- |
| `--skill <name>` | Installs one skill. Repeat to install multiple skills. |
| `--dry-run` | Prints intended actions without writing files. |
| `--force` | Replaces existing installed skill folders. |
| `--link` | Creates symlinks instead of copying folders. |
| `--list` | Lists discovered skills and exits. |

By default, the installer copies skills and skips destinations that already
exist.

## Provider Notes

### Codex

```bash
./scripts/codex-install.sh
```

This installs user-level skills into `${CODEX_HOME:-$HOME/.codex}/skills`.

For a project-local setup in agents that read `.agents/skills`, use a symlink:

```bash
mkdir -p .agents
ln -sfn ../skills .agents/skills
```

### Claude Code

```bash
./scripts/claude-install.sh
```

This installs user-level skills into `${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}`.

For a project-local setup, install or link into `.claude/skills`:

```bash
CLAUDE_SKILLS_DIR=.claude/skills ./scripts/claude-install.sh --link
```

### Gemini CLI

```bash
./scripts/gemini-install.sh
```

The default destination is `.gemini/skills`. In Gemini-compatible setups, skills
can be activated with the provider's skill activation mechanism, such as
`activate_skill(name="cad")`.

### OpenClaw

```bash
./scripts/openclaw-install.sh
```

The default destination is `$HOME/.openclaw/skills`. OpenClaw-style agents load
skills from their YAML frontmatter triggers.

### Other Agents

Use the shared installer for supported local skill directories:

```bash
./scripts/install.sh --agent cursor
./scripts/install.sh --agent vscode
./scripts/install.sh --agent goose
./scripts/install.sh --agent project
```

Use `--dry-run` first if you want to confirm where files will be installed.

## Manual Installation

Manual installation is just copying or linking skill folders into the skill root
your agent reads.

Copy all bundled skills into Codex:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R skills/* "${CODEX_HOME:-$HOME/.codex}/skills/"
```

Copy one skill:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R skills/cad "${CODEX_HOME:-$HOME/.codex}/skills/"
```

Symlink all skills for a project-local Claude Code setup:

```bash
mkdir -p .claude
ln -sfn ../skills .claude/skills
```

## Verification

List the skills discovered by the installer:

```bash
./scripts/install.sh --list
```

Preview an install:

```bash
./scripts/codex-install.sh --dry-run
```

Check that an installed skill has its entrypoint:

```bash
test -f "${CODEX_HOME:-$HOME/.codex}/skills/cad/SKILL.md"
```

Restart the agent after installation, then ask it to use one of the installed
skills by name.

## Troubleshooting

- **Skill does not appear**: restart the agent and confirm the destination path
  matches the provider you are using.
- **Existing skill was not updated**: rerun the installer with `--force`.
- **Wrong project received `.gemini/skills`, `.cursor/skills`, or `.skills`**:
  rerun from the intended project root or set the matching destination
  environment variable.
- **Shell says permission denied**: run `chmod +x scripts/*.sh scripts/install-skills.py`.
- **`python3` is missing**: install Python 3 or run with `PYTHON_BIN=/path/to/python3`.
- **CAD generation fails after skill install**: install the CAD runtime
  dependencies from the Local Development section in [README.md](README.md).

## Uninstallation

Remove installed skill folders from the provider destination. For example, to
remove all bundled skills from Codex:

```bash
rm -rf \
  "${CODEX_HOME:-$HOME/.codex}/skills/cad" \
  "${CODEX_HOME:-$HOME/.codex}/skills/step-parts" \
  "${CODEX_HOME:-$HOME/.codex}/skills/cad-explorer" \
  "${CODEX_HOME:-$HOME/.codex}/skills/urdf" \
  "${CODEX_HOME:-$HOME/.codex}/skills/sdf" \
  "${CODEX_HOME:-$HOME/.codex}/skills/srdf" \
  "${CODEX_HOME:-$HOME/.codex}/skills/sendcutsend"
```

For project-local symlink installs, remove the symlink:

```bash
rm -f .agents/skills
rm -f .claude/skills
```
