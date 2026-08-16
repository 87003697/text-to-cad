# Formal Cup Agent runtime capability surface

Research ticket: `SAR-001`
Source baseline: `develop@9c5b7ea39030a013023a2f06c83b9b869a394861` plus the reviewed Browser Sidecar candidate `563ba9f3e823235d3065237b87c1be9a206d17bd`
Date: 2026-08-16

## Answer

The first sealed Agent runtime can be materially smaller than the current CVM
host surface. For the exact `cup_cup_033` fixture, the routing contract selects
`implicit-cad`, not `cad`: the current numeric inspector reports 3,764 faces,
non-watertight geometry, and Euler characteristic 144, and the first matching
routing rule sends any Euler characteristic other than 2 to `implicit-cad`.[1]
Consequently the Cup contract needs the browser-free implicit canonical builder,
VoxBlame, the Workspace authority helper, Codex, and local Git/LFS. It does **not**
need build123d, cadquery-ocp/OCP, cadpy, FreeCAD, ROS, or a host CAD virtualenv.

The capability surface should be frozen as the following allowlist. Exact package
versions, ELF SONAMEs and file hashes belong to the dependency-closure decision
(`SAR-002`); this finding fixes which capabilities that closure must satisfy.

## Normative capability allowlist

### Executables

| Executable | Required use | Contract boundary |
|---|---|---|
| fixed `codex` artifact | Run one model-driven Agent Execution | Exact version and artifact hash are selected separately; no runtime install or update |
| `python3` 3.12.x | Numeric mesh inspection, all six `mesh-compare` operations, and `mesh-to-cad-workspace` | Must load the locked Python environment and the native VoxBlame extension |
| fixed Node runtime | Run `implicit-cad/scripts/canonical-build.mjs` for build and rebuild | Must support the Node permission model and `--experimental-vm-modules`; the canonical worker receives an empty ambient environment and bounded filesystem grants.[2] |
| `git` and `git-lfs` | Workspace initialization, scoped staging, commits, attributes and evidence validation | LFS is mandatory: the helper runs `git lfs version/install`, checks the filter, and commits protocol paths.[3] |
| `bash` | Codex shell execution and reviewed gateway/entrypoint wrappers | No package manager or bootstrap shell is promised |
| `rg`, `find`, `sed`, `cat`, `ls`, `stat`, `file`, `mkdir`, `cp`, `mv`, `rm`, `chmod`, `sha256sum`, `env`, `ps` | The bounded inspection and artifact-handling vocabulary used by the Agent | These are Agent affordances, not permission to inspect outside the Source Snapshot, job directories, `/proc` self/job view, or declared runtime files |

`curl`, `wget`, `apt`, `dnf`, `pip`, `uv`, `npm`, compilers, Docker/Podman,
`rsync`, `ssh` and cloud CLIs are not runtime capabilities. Wheels, Node files,
Codex and native objects are installed at image-build time. The Agent may author
files only inside its job workspace; it does not build or mutate the image.

### Formal workflow entrypoints

The manifest must exercise exactly these semantic operations:

1. Numeric route evidence: `python skills/mesh-inspect/scripts/mesh-inspect
   <cup.ply>`.
2. Canonical Reference: `mesh-compare voxblame-prepare-reference`.
3. Workspace lifecycle: `init`, `begin-attempt`, bounded `run`,
   `publish-step-zero`, zero to five `publish-cycle`/`record-attempt`
   transactions, `finalize`, `recover` when applicable, and `validate`.[4]
4. Implicit build/rebuild: `node skills/implicit-cad/scripts/canonical-build.mjs`
   with either `--source` or `--recipe`. The output is a self-contained implicit
   source, canonical GLB, profile, build record and rebuild recipe; no STEP is
   fabricated for this route.[2]
5. Geometry evidence: all six public `mesh-compare` operations:
   `voxblame-prepare-reference`, `voxblame-measure`, `voxblame-targets`,
   `voxblame-diff`, `voxblame-preview`, and `voxblame-verify`.[5]

The formal path must not call `mesh-preview`, `$cad snapshot`, the implicit
snapshot command, Playwright, Chromium, or a Viewer server inside the Agent.

### Python surface

The promised import surface is:

- standard-library modules used by Codex support, Workspace and evidence code;
- `numpy`, `trimesh`, and `PIL`;
- the vendored `meshscope` package;
- `meshscope.voxblame._native`, compiled for the image's CPython 3.12 ABI and
  `linux/amd64` glibc environment;
- a browser-free `meshshot` **client/profile** package implementing the
  registered residual request and PNG validation, but containing neither a
  Playwright dependency nor local-browser code.

`meshscope` declares `trimesh`, `numpy`, and Pillow, and production measurement
defaults to the C++ `_native` backend and fails closed if it cannot be imported.[6]
The precise transitive wheels and native shared-library closure must be derived
from locked Linux wheels and the compiled extension, not copied from the current
Mac or CVM `.venv`.

The first Cup runtime explicitly excludes `build123d`, `cadquery-ocp`, `cadpy`,
`playwright`, browser executables, browser caches, Matplotlib and SciPy unless
`SAR-002` demonstrates that a required locked dependency imports one. They are
not part of the semantic Cup contract.

### Node/implicit surface

Only the browser-free canonical-build module graph is required: the wrapper,
`canonicalBuild.js`, `canonicalBuildWorker.mjs`, and their internal schema,
model, animation, SDF evaluator, meshing, export-model and GLB exporter modules.
The canonical builder already rejects network APIs and runs authored source in a
restricted child process.[2]

The general `implicitjs` package currently declares Playwright and carries
snapshot/browser code. Those files are not part of the sealed Agent surface.
The implementation must publish/vendor a canonical-build-only runtime subset
instead of installing the package's present general dependency set.

### Fixture and data surface

The conformance fixture set must contain:

- hydrated `models/toys4k/cup_cup_033.ply`, exactly 190,047 bytes with SHA-256
  `3d4c7ca9118ef8a6d4ae3e7af3117250ca824ad5b8de36dcfa2c66cece47ae67`;
- a checked-in, self-contained canonical Cup `.implicit.js` source satisfying
  `units: "unitless"`, exact `[-0.5, 0.5]^3` bounds and the
  `implicit_voxblame_depth8/1` profile;
- trusted route-adapter and geometry-entrypoint registry records;
- expected build/rebuild, native measurement, preview-profile, Observable
  Geometry, Workspace validation and cleanup identities.

Only the raw PLY exists as a durable repo fixture today. The canonical Cup
source and golden identity bundle must be promoted to `models/` during
implementation; an ignored historical pilot output is evidence, not a sealed
runtime fixture.

The runtime also carries immutable non-secret data required by its own binaries:
the Python standard library, UTF-8 locale data, CA roots, a minimal passwd/group
identity for the non-root Agent, UTC timezone data, Git's executable templates,
and the exact skill/package files named above. Project source and the Cup fixture
remain in the independently identified read-only Source Snapshot rather than in
the Agent image.

### Environment and writable state

The runtime environment is an explicit allowlist:

- `HOME=<job-private-home>` and `CODEX_HOME=<job-private-home>/.codex`;
- `PATH` containing only sealed runtime binary directories;
- `LANG=C.UTF-8`, `LC_ALL=C.UTF-8`, `TZ=UTC`;
- `GIT_TERMINAL_PROMPT=0`, `PYTHONDONTWRITEBYTECODE=1`;
- `TMPDIR=/tmp`, `XDG_CACHE_HOME=<job-private-cache>`;
- one outer-generated Codex config binding the fixed model to the job's Retry
  Proxy endpoint and a random job-local bearer token;
- the fixed Browser authority and job-private Broker socket paths declared by
  the cross-artifact browser contract.

The real Venus credential is absent. So are inherited proxy variables, host Git
configuration, SSH agent state, cloud credentials, Docker variables, Playwright
variables, and host language/runtime search paths. Git author identity, when
needed for Workspace commits, is job-scoped configuration supplied by the outer
authority.

Writable state is limited to the experiment workspace, job-private Codex home,
job-private cache/tmp, and runner-owned output/receipt locations. The image root,
Source Snapshot, skill runtime and input mesh are read-only.

### Network and Browser Broker capabilities

The Agent receives exactly two communications capabilities:

1. a job-scoped connection to the Venus Retry Proxy using only the random local
   token; the proxy, outside the Agent image, holds the real Venus credential;
2. the job-private Unix socket for the Browser Broker plus its immutable
   authority file.

There is no general Internet, DNS, package registry, arbitrary URL, Sidecar
endpoint, Docker socket, or direct browser connection. Canonical input
preparation forbids remote geometry dependencies, and canonical implicit source
forbids network APIs.[2]

The only Agent-callable Browser-Backed Preview program required by Cup is:

- `residual`: `cadena_residual_eight_view/1`, supporting `step` and `final`,
  eight fixed views, fixed camera/postprocess policy, and the current candidate
  program digest
  `d2138ad7f3b74094862cfa8bd4d3ee0fb59ba8bde89a82962afae9ae02b0180b`.[7]

The candidate also registers a `viewer` program, but its present request is a
fixed `inspection-step` conformance fixture. It is an outer startup/conformance
check, not a promised Cup modeling capability. The first runtime therefore does
not require a general Viewer program, `$cad-viewer`, or direct `$cad snapshot`.

## Required contract corrections before implementation can pass

1. **Split numeric routing from browserful mesh inspection.** The current
   `$mesh-inspect` skill makes `mesh-preview` mandatory and stops when it fails,
   although the Mesh-to-CAD routing rubric consumes only numeric fields.[8] The
   formal Cup workflow must explicitly call the numeric inspector as its routing
   contract, or add a new registered input-preview program. The smaller and
   already sufficient choice is the numeric-only formal route check.
2. **Make formal meshshot browser-free by construction.** The reviewed candidate
   renderer already chooses the Broker when the fixed formal authority exists
   and imports Playwright lazily only for the legacy local path, but its package
   metadata still unconditionally declares `playwright`.[7] Publish a formal
   client/profile distribution (or an equivalent dependency extra) with no
   Playwright package and no local-browser runtime.
3. **Vendor the canonical implicit subset.** Do not copy the current general
   implicit runtime with its Playwright/browser dependencies into the Agent.
4. **Create the durable provider-free Cup fixture.** Conformance must run build,
   rebuild, native measurement, registered residual preview, finalize/validate,
   receipt publication and cleanup without a model call.

Until those four items exist, a container that merely starts Codex is not an
`Agent Runtime Verified` artifact.

## Observed host facilities that are not contract capabilities

The legacy runner's read-only host surface (`/usr`, `/sys`, broad `/etc` trees,
the host `.venv`, installed Codex skills, gateway file and Playwright cache) is
an implementation workaround, not a requirement of Cup. In particular, the
sealed image must not depend on:

- the CVM's `/usr`, `/sys`, `/etc/pki`, `/etc/alternatives` or dynamic
  `/etc/ld.so.*` trees;
- `/root`, `~/.secrets/text-to-cad.env`, the host Codex home or host Git config;
- the host `.venv` or its duplicate/editable package state;
- a host/global Node installation or `node_modules` tree;
- Chromium, Chrome, Playwright packages/caches, CDP, browser environment
  variables or the Browser Sidecar filesystem;
- Docker socket/CLI, bwrap, systemd, cloud metadata, S3 tooling, SSH or CVM
  provisioning tools;
- `claude-tap` as an Agent dependency. Trace/retry supervision remains an
  outer-runtime concern; the Agent sees only the bounded model-proxy capability.

These facilities may still exist on the CVM host or in the Broker/Sidecar
artifacts. They are not mounted into, copied into, or promised by the Agent
Runtime Artifact.

## Implication for the remaining map

`SAR-002` can now derive one reproducible closure against this allowlist rather
than attempting to seal the host. The largest uncertainty has also fallen away:
the first Cup artifact is an implicit-SDF runtime and does not need the large OCC
stack. The principal engineering seams are the browser-free meshshot client,
canonical-only implicit bundle, deterministic fixture, and container/receipt
integration.

## Sources

1. [`models/toys4k/cup_cup_033.ply`](../../models/toys4k/cup_cup_033.ply),
   locally hashed and inspected on 2026-08-16; routing rule in
   [`routing-rubric.md`](../../skills/mesh-to-cad/references/routing-rubric.md#L6-L15).
2. [`implicit-cad/SKILL.md`](../../skills/implicit-cad/SKILL.md#L176-L215) and
   [`canonicalBuild.js`](../../skills/implicit-cad/scripts/packages/implicitjs/src/lib/implicitCad/canonicalBuild.js#L110-L186).
3. [`workspace_core.py`](../../skills/mesh-to-cad/scripts/mesh-to-cad-workspace/workspace_core.py#L2892-L2984).
4. [`mesh-to-cad/SKILL.md`](../../skills/mesh-to-cad/SKILL.md#L41-L149).
5. [`mesh-compare/SKILL.md`](../../skills/mesh-compare/SKILL.md#L19-L109).
6. [`meshscope/pyproject.toml`](../../packages/meshscope/pyproject.toml#L5-L18),
   [`setup.py`](../../packages/meshscope/setup.py#L15-L23), and
   [`voxelize.py`](../../packages/meshscope/src/meshscope/voxblame/voxelize.py#L43-L68).
7. Reviewed Browser Sidecar candidate at
   `563ba9f3e823235d3065237b87c1be9a206d17bd`:
   `packages/meshshot/src/meshshot/browser_contract.json`,
   `packages/meshshot/src/meshshot/renderer.py`,
   `scripts/pilot/browser_sidecar_gate.py`, and
   `scripts/pilot/browser_sidecar.py`.
8. [`mesh-inspect/SKILL.md`](../../skills/mesh-inspect/SKILL.md#L20-L80) and the
   numeric-only fields consumed by
   [`routing-rubric.md`](../../skills/mesh-to-cad/references/routing-rubric.md#L36-L46).
