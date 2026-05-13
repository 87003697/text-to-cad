<div align="center">

<img src="assets/text-to-cad-demo.gif" alt="Demo of the CAD skill generating and previewing CAD geometry" width="100%">

<br>

# CAD Skills

A collection of agent skills for CAD, robotics and hardware design

[Docs](https://www.cadskills.xyz) | [Demo](https://demo.cadskills.xyz)

[![GitHub stars](https://img.shields.io/github/stars/earthtojake/text-to-cad?style=for-the-badge&logo=github&label=Stars)](https://github.com/earthtojake/text-to-cad/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/earthtojake/text-to-cad?style=for-the-badge&logo=github&label=Forks)](https://github.com/earthtojake/text-to-cad/network/members)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue?style=for-the-badge)](LICENSE)
[![Follow @soft_servo](https://img.shields.io/badge/Follow-%40soft__servo-000000?style=for-the-badge&logo=x)](https://x.com/soft_servo)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](skills/cad/requirements.txt)
[![build123d](https://img.shields.io/badge/build123d-CAD-00A676?style=for-the-badge)](https://github.com/gumyr/build123d)
[![OCP](https://img.shields.io/badge/OCP-OpenCascade-2F80ED?style=for-the-badge)](skills/cad/requirements.txt)
[![STEP](https://img.shields.io/badge/STEP-Export-4A5568?style=for-the-badge)](skills/cad/SKILL.md)
[![STL](https://img.shields.io/badge/STL-Export-4A5568?style=for-the-badge)](skills/cad/SKILL.md)
[![3MF](https://img.shields.io/badge/3MF-Export-4A5568?style=for-the-badge)](skills/cad/SKILL.md)
[![URDF](https://img.shields.io/badge/URDF-Robots-6B46C1?style=for-the-badge)](skills/urdf/SKILL.md)
[![SDF](https://img.shields.io/badge/SDF-Simulation-6B46C1?style=for-the-badge)](skills/sdf/SKILL.md)
[![SRDF](https://img.shields.io/badge/SRDF-MoveIt2-6B46C1?style=for-the-badge)](skills/srdf/SKILL.md)

</div>

## ✨ Features

- **Generate** - Create source-controlled CAD models with coding agents like Codex and Claude Code.
- **Export** - Produce STEP, STL, 3MF, DXF, GLB, topology data, and URDF/SDF/SRDF robot descriptions.
- **Browse** - Inspect generated geometry, flat patterns, and robot-description files in CAD Explorer.
- **Source** - Find and download off-the-shelf STEP parts from the hosted step.parts catalog.
- **Reference** - Copy stable `@cad[...]` references so agents can make precise follow-up edits.
- **Review** - Render quick review images for fast checks during an iteration loop.
- **Reproduce** - Edit source files first, then regenerate explicit targets.
- **Local** - Run harness, skills and explorer locally with no backend to host.

## 🧰 Skills

- **CAD Skill** - STEP, STL, 3MF, DXF, GLB/topology, render images, and `@cad[...]` geometry references. [Bundled skill](skills/cad/SKILL.md) · [Standalone repo](https://github.com/earthtojake/cad-skill)
- **step.parts Skill** - Find, evaluate, and download common off-the-shelf STEP models from step.parts, including screws, nuts, washers, bearings, standoffs, electronics parts, motors, and connectors. [Bundled skill](skills/step-parts/SKILL.md)
- **CAD Explorer Skill** - Start or reuse CAD Explorer and return visual review links for generated `.step`, `.stp`, `.stl`, `.3mf`, `.dxf`, `.urdf`, `.srdf`, and `.sdf` files. [Bundled skill](skills/cad-explorer/SKILL.md)
- **URDF Skill** - Generated URDF XML, robot links, joints, limits, validation, mesh references, and CAD Explorer URDF visualization. [Bundled skill](skills/urdf/SKILL.md)
- **SDF Skill** - Generated SDFormat/SDF XML, simulator model/world structure, validation, mesh URIs, plugins, and simulator-specific metadata. [Bundled skill](skills/sdf/SKILL.md)
- **SRDF Skill** - MoveIt2 SRDF semantics, direct SRDF-to-URDF Explorer links, inverse kinematics, path planning, and optional MoveIt2-server testing for existing URDFs. [Bundled skill](skills/srdf/SKILL.md)
- **SendCutSend Skill** - SendCutSend.com-specific DXF and STEP/STP upload preflight reports using its ordering guide, catalog, and specs for selected materials, SKUs, services, and secondary operations. [Bundled skill](skills/sendcutsend/SKILL.md)

## 🧩 Harness

The `harness/` directory contains optional repo-level instruction files for larger CAD projects that will be edited by coding agents. These files keep project behavior predictable: edit sources before derived artifacts, regenerate explicit targets, avoid broad repo scans, treat CAD outputs as LFS-heavy, and keep reusable workflow details in the skills themselves.

To use the harness in another CAD project, copy `harness/AGENTS.md` and `harness/CLAUDE.md` into that project's root.

## 💻 Installation

Clone once for script installs, then choose your agent:

```bash
git clone https://github.com/earthtojake/text-to-cad.git
cd text-to-cad
```

### Codex

```bash
./scripts/codex-install.sh
```

Installs to `${CODEX_HOME:-$HOME/.codex}/skills`.

### Claude Code

```bash
./scripts/claude-install.sh
```

Installs to `${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}`.

### Gemini CLI

```bash
./scripts/gemini-install.sh
```

Installs to `${GEMINI_SKILLS_DIR:-.gemini/skills}`.

### OpenClaw

```bash
./scripts/openclaw-install.sh
```

Installs to `${OPENCLAW_SKILLS_DIR:-$HOME/.openclaw/skills}`.

### Universal Installer

```bash
npx agent-skills-cli add earthtojake/text-to-cad
```

This installs the repo's skills into supported agents automatically. Learn more at [agentskills.in](https://www.agentskills.in).

For verification, troubleshooting, manual install, and uninstall steps, see [INSTALLATION.md](INSTALLATION.md). For local CAD generation, also install the CAD runtime dependencies from the setup commands below.

## 📸 Screenshots

<table>
  <tr>
    <td width="33%">
      <a href="./assets/text-to-cad-demo.gif">
        <img src="./assets/text-to-cad-demo.gif" alt="CAD skill demo showing generated geometry in CAD Explorer" width="100%">
      </a>
      <a href="./skills/cad/SKILL.md"><strong>CAD</strong></a>
    </td>
    <td width="33%">
      <a href="./assets/urdf-demo.gif">
        <img src="./assets/urdf-demo.gif" alt="URDF skill demo showing robot description output in CAD Explorer" width="100%">
      </a>
      <a href="./skills/urdf/SKILL.md"><strong>URDF</strong></a>
    </td>
    <td width="33%">
      <a href="./assets/srdf-moveit2-demo.gif">
        <img src="./assets/srdf-moveit2-demo.gif" alt="SRDF MoveIt2 skill demo showing inverse kinematics in CAD Explorer" width="100%">
      </a>
      <a href="./skills/srdf/SKILL.md"><strong>SRDF / MoveIt2</strong></a>
    </td>
  </tr>
</table>

## 🔁 Workflow

1. **Describe** - Tell your agent about the part, assembly, fixture, robot, or mechanism you want.
2. **Edit** - Let your coding agent update repo-local CAD source files.
3. **Regenerate** - Create explicit STEP, STL, 3MF, DXF, GLB, URDF, SDF, or SRDF targets.
4. **Inspect** - Open CAD Explorer to review the generated model.
5. **Reference** - Copy `@cad[...]` handles when you want geometry-aware edits.
6. **Commit** - Save the source and generated artifacts together once the model is ready.

## 🧪 Benchmarks

The repo stores heavyweight assets in `assets/**` and `benchmarks/**` through Git LFS and excludes those trees from default LFS pulls so lightweight clones do not fetch GIF assets. Benchmark markdown remains normal Git for readable diffs. To hydrate only the benchmark assets locally, run:

```bash
git lfs pull --include="benchmarks/**"
```

<table>
  <thead>
    <tr>
      <th>#</th>
      <th>Target</th>
      <th>Prompt</th>
      <th>Output</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>1</td>
      <td><a href="benchmarks/01-rectangular-calibration-block.md">Rectangular calibration block with four holes</a></td>
      <td>Create a centered 100 x 60 x 20 mm block with four 8 mm vertical through-holes. Add only a 2 mm chamfer on the top outer perimeter.</td>
      <td><img src="benchmarks/benchmark_01_rectangular_calibration_block.gif" alt="Rectangular calibration block orbit gif" width="220"></td>
    </tr>
    <tr>
      <td>2</td>
      <td><a href="benchmarks/02-circular-flange.md">Circular flange with bolt-hole pattern</a></td>
      <td>Create an 80 mm diameter, 10 mm thick circular flange with a 30 mm central through-bore. Add six 6 mm through-holes on a 60 mm bolt circle and fillet the outside circular edges.</td>
      <td><img src="benchmarks/benchmark_02_circular_flange.gif" alt="Circular flange orbit gif" width="220"></td>
    </tr>
    <tr>
      <td>3</td>
      <td><a href="benchmarks/03-l-bracket.md">L-bracket with gussets and two hole directions</a></td>
      <td>Create an L-bracket from a base plate and rear vertical plate. Add vertical base holes, horizontal back-plate holes, two triangular gussets, and a filleted base/back transition.</td>
      <td><img src="benchmarks/benchmark_03_l_bracket.gif" alt="L-bracket orbit gif" width="220"></td>
    </tr>
    <tr>
      <td>4</td>
      <td><a href="benchmarks/04-stepped-shaft-keyway.md">Stepped shaft with keyway</a></td>
      <td>Create a 120 mm shaft along X with 20/30/20 mm diameter stepped sections. Add end chamfers and a shallow rectangular keyway on top of the middle section.</td>
      <td><img src="benchmarks/benchmark_04_stepped_shaft_keyway.gif" alt="Stepped shaft orbit gif" width="220"></td>
    </tr>
    <tr>
      <td>5</td>
      <td><a href="benchmarks/05-open-top-electronics-enclosure.md">Open-top electronics enclosure with bosses</a></td>
      <td>Create a hollow open-top enclosure with 3 mm walls and floor. Add four internal standoffs with centered blind holes and 2 mm outside vertical corner fillets.</td>
      <td><img src="benchmarks/benchmark_05_open_top_electronics_enclosure.gif" alt="Open-top electronics enclosure orbit gif" width="220"></td>
    </tr>
    <tr>
      <td>6</td>
      <td><a href="benchmarks/06-clevis-bracket-lightening-cutouts.md">Aerospace-style clevis bracket with lightening cutouts</a></td>
      <td>Create a symmetric clevis bracket with a base plate, two rounded lugs, base mounting holes, and a horizontal lug bore. Add triangular lightening cutouts, reinforcing ribs, and rounded transitions.</td>
      <td><img src="benchmarks/benchmark_06_clevis_bracket_lightening_cutouts.gif" alt="Clevis bracket orbit gif" width="220"></td>
    </tr>
    <tr>
      <td>7</td>
      <td><a href="benchmarks/07-radial-engine-cylinder.md">Radial-engine-style cylinder with cooling fins</a></td>
      <td>Create a vertical engine-cylinder form with a central barrel, 12 cooling fins, a base flange, and a top cap. Add a 35 degree angled spark-plug boss with a coaxial through-hole.</td>
      <td><img src="benchmarks/benchmark_07_radial_engine_cylinder.gif" alt="Radial-engine-style cylinder orbit gif" width="220"></td>
    </tr>
    <tr>
      <td>8</td>
      <td><a href="benchmarks/08-centrifugal-impeller.md">Centrifugal impeller with backward-curved blades</a></td>
      <td>Create a centrifugal impeller with a backplate, hub, and through-bore. Add 12 fused backward-curved blades sweeping about 45 degrees from root to tip.</td>
      <td><img src="benchmarks/benchmark_08_centrifugal_impeller.gif" alt="Centrifugal impeller orbit gif" width="220"></td>
    </tr>
    <tr>
      <td>9</td>
      <td><a href="benchmarks/09-spiral-staircase.md">Spiral staircase with helical handrail</a></td>
      <td>Create a miniature spiral staircase with a central column, base disk, and 20 rising wedge treads. Add a one-revolution helical handrail and vertical balusters at the tread outer ends.</td>
      <td><img src="benchmarks/benchmark_09_spiral_staircase.gif" alt="Spiral staircase orbit gif" width="220"></td>
    </tr>
    <tr>
      <td>10</td>
      <td><a href="benchmarks/10-planetary-gear-stage.md">Simplified planetary gear stage</a></td>
      <td>Create a flat planetary gear assembly with separate sun, planet, ring, carrier, and pin bodies. Use simplified trapezoidal teeth and place three planets around the sun on a 42 mm radius circle.</td>
      <td><img src="benchmarks/benchmark_10_planetary_gear_stage.gif" alt="Planetary gear stage orbit gif" width="220"></td>
    </tr>
  </tbody>
</table>

## 🛠️ Local Development

Clone the repo:

```bash
git clone https://github.com/earthtojake/text-to-cad.git
cd text-to-cad
```

Install Python CAD dependencies:

```bash
python3.11 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/pip install -r skills/cad/requirements.txt
```

Install CAD Explorer dependencies:

```bash
npm --prefix skills/cad-explorer/scripts/explorer install
```

Start or reuse CAD Explorer for the current workspace:

```bash
npm --prefix skills/cad-explorer/scripts/explorer run dev:ensure -- --workspace-root "$PWD" --root-dir .
```

Then open the URL printed by the command.

For a specific file, pass its path explicitly:

```bash
npm --prefix skills/cad-explorer/scripts/explorer run dev:ensure -- --workspace-root "$PWD" --root-dir . --file path/to/model.step
```

CAD Explorer supports `.step`, `.stp`, `.stl`, `.3mf`, `.dxf`, `.urdf`, `.srdf`, and `.sdf` files. SRDF reviews can use optional local MoveIt2 controls when the CAD Explorer skill's MoveIt2 server is running.

For manual foreground Explorer development:

```bash
npm --prefix skills/cad-explorer/scripts/explorer run dev
```
