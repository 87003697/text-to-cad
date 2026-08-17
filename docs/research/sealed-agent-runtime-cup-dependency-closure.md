# Sealed Agent Runtime: reproducible Cup dependency closure

Date: 2026-08-16
Research ticket: `SAR-002`, “Can the Cup dependency closure be rebuilt reproducibly?”
Repo baseline inspected: `develop@9c5b7ea39030a013023a2f06c83b9b869a394861`

## Answer

**Yes, the first Cup runtime can be rebuilt without copying the CVM virtualenv,
but the repository does not yet contain an admitted offline closure.** Every
external capability required by `SAR-001` has an authoritative, byte-addressable
linux/amd64 acquisition route. The difficult OCC/FreeCAD/cadpy closure is absent
from this route. The remaining native surface is CPython, NumPy, Pillow, Node,
the small project-owned VoxBlame C++ extension, Codex, and the ordinary Noble
runtime libraries those ELF objects actually declare.

This is a feasibility decision, not a claim that the final wheelhouse already
exists. Four project-owned artifacts still have to be created or admitted:

1. a CPython 3.12 linux/amd64 `meshscope` wheel whose `_native` ELF closure has
   been audited;
2. a browser-free `meshshot` client/profile wheel;
3. a canonical-build-only implicit JavaScript source bundle;
4. the already-decided Codex 0.147.0 linux/amd64 artifact after its separate
   byte-admission gate.

After those four artifacts exist, the image build can be networkless and
consume only a digest-pinned Noble base, a frozen `.deb` pool, an exact Python
wheelhouse, the signed-checksum Node archive, the admitted Codex archive, and
project-source manifests. No host `/usr`, `/sys`, `.venv`, compiler, package
manager, Playwright package, or browser asset is needed at runtime.

## Rebuild contract

“Reproducible” must mean **the same admitted bytes produce the same installed
runtime identity**, not merely that a resolver is likely to choose similar
versions:

- acquisition is a separate, networked step that records upstream identity,
  filename, size, SHA-256, and where available signature/attestation evidence;
- all admitted bytes are mirrored unchanged into immutable project storage;
- the image build has no network and accepts only those mirrored inputs;
- Python installation uses exact wheel filenames and hashes with no source-build
  fallback; pip's own repeatable-install guidance recommends pinned versions,
  hashes, and a wheelhouse for this purpose ([pip repeatable installs](https://pip.pypa.io/en/stable/topics/repeatable-installs/));
- the build records the final image digest, installed-file manifest, package
  inventories and ELF dependency resolution, then runs the provider-free Cup
  capability manifest.

Project wheels do not need to be rebuilt during every Agent image build. They
should be built once in a separately digest-pinned, networkless builder from
hashed source and toolchain inputs, admitted by hash, then installed as immutable
runtime inputs. Bit-for-bit repeatability of the wheel-building process is a
desirable extra check, but is not a substitute for admitting the exact wheel
bytes that production consumes.

## External artifact closure

### Noble base, Python and shell/data packages

Use one architecture-specific Ubuntu Noble manifest digest, never the moving
`ubuntu:24.04` tag. A current official linux/amd64 Noble image demonstrates the
required mechanism and is published with an architecture-specific manifest
digest ([official Ubuntu image details](https://hub.docker.com/layers/library/ubuntu/24.04/?tab=layers)); the release implementation must record the digest it
actually admits rather than copying a digest from this research note.

All Debian packages must come from one Ubuntu Snapshot timestamp. Ubuntu's
snapshot service supports setting one timestamp across enabled repositories via
`APT::Snapshot` ([Ubuntu Snapshot Service](https://snapshot.ubuntu.com/)). The
acquisition manifest must preserve the signed `InRelease`/Release metadata and
the exact `.deb` SHA-256 values from the snapshot indices, then mirror the
resolved transitive pool. The image build installs from that pool with no apt
network access.

The runtime package roots are:

| Capability | Noble package roots | Constraint |
|---|---|---|
| Python | `python3.12`, its exact transitive runtime packages | Noble currently publishes amd64 Python `3.12.3-1ubuntu0.15`; the lock selects one snapshot/version and includes `python3.12-minimal` and both stdlib/minimal library closures ([Ubuntu package metadata](https://packages.ubuntu.com/noble/python3.12)) |
| Git workspace | `git`, `git-lfs` | Snapshot must enable `universe` as well as the relevant release, updates and security pockets because `git-lfs` is in `universe`; Noble publishes an amd64 glibc build and declares its `git`/`libc6` dependencies ([Ubuntu git-lfs metadata](https://packages.ubuntu.com/noble/git-lfs)) |
| Agent shell vocabulary | `bash`, `coreutils`, `findutils`, `sed`, `file`, `ripgrep`, `procps` | Lock the complete solver result, not just these roots; package managers and compiler packages are absent from the final runtime |
| Immutable runtime data | `ca-certificates`, `tzdata`, `locales`, plus transitive `libc-bin`/OpenSSL requirements | Noble publishes the CA bundle and its OpenSSL dependency ([Ubuntu ca-certificates metadata](https://packages.ubuntu.com/noble/ca-certificates)); the locale data is tied to Noble glibc ([Ubuntu locales metadata](https://packages.ubuntu.com/noble/locales)) |

The image creates the non-root passwd/group entry itself from fixed source text.
No font package belongs in the Agent closure: all image rendering is in the
Browser Sidecar, while Agent-side PNG handling is pixel/metadata validation with
Pillow. A later discovery of font loading would therefore be a capability drift
failure, not permission to install fonts silently.

### Python wheelhouse

The smallest demonstrated CPython 3.12 candidate set is below. The listed files
and SHA-256 values come from PyPI's authoritative release API; the final lock
still requires the admission tests below.

| Distribution | Exact candidate artifact | SHA-256 | Why it fits Noble |
|---|---|---|---|
| NumPy 2.4.6 | `numpy-2.4.6-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl` | `90f9849678c75fe7afa2d348ac842c168b0a4d3d61919687216dfc547976d853` | CPython 3.12 ABI, x86-64, glibc 2.27+; [PyPI release API](https://pypi.org/pypi/numpy/2.4.6/json) |
| trimesh 4.12.2 | `trimesh-4.12.2-py3-none-any.whl` | `b5b5afa63c5272345f2858f7676bc8c217dc8a89f4fadf6193fe10a81b5ff2aa` | Pure Python; its only unconditional dependency is `numpy>=1.20`; [PyPI release API](https://pypi.org/pypi/trimesh/4.12.2/json) |
| Pillow 12.2.0 | `pillow-12.2.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl` | `b86024e52a1b269467a802258c25521e6d742349d760728092e1bc2d135b4d76` | CPython 3.12 ABI, x86-64, glibc 2.17+; [PyPI release API](https://pypi.org/pypi/Pillow/12.2.0/json) |
| setuptools 82.0.1 | `setuptools-82.0.1-py3-none-any.whl` | `a59e362652f08dcd477c78bb6e7bd9d80a7995bc73ce773050228a348ce2e5bb` | Builder-only, pure Python; [PyPI release API](https://pypi.org/pypi/setuptools/82.0.1/json) |

The candidate is deliberately only the three runtime distributions: trimesh's
large `easy` and `recommend` extras are not requested, so SciPy, networkx,
shapely, matplotlib and other optional packages do not enter the lock. Wheel
tags express interpreter, ABI and platform compatibility, and `manylinux_x_y`
requires glibc `x.y` or newer ([PyPA compatibility-tag specification](https://packaging.python.org/en/latest/specifications/platform-compatibility-tags/#manylinux)).
Noble's glibc 2.39 satisfies the selected wheel tags.

Before these candidates become the formal lock, an admission job must download
only these exact files, verify the hashes above, inspect `WHEEL`/`METADATA`, and
run `auditwheel show` plus recursive ELF resolution inside the selected Noble
base. Wheel tags establish compatibility claims; they do not by themselves
prove the exact shared-library inventory. If the audited Pillow or NumPy wheel
contains additional bundled shared objects, those objects remain inside the
wheel manifest rather than becoming ad hoc apt dependencies.

### Node and the canonical implicit subset

Select Node.js 24.13.0 LTS for the first lock. The official archive publishes
`node-v24.13.0-linux-x64.tar.xz`, a signed `SHASUMS256` set, and SHA-256
`e798599612f4bb71333a3397ab0d095fd62214e115aea45aa858a145fc72d67e`
for that archive ([Node release directory](https://nodejs.org/download/release/v24.13.0/),
[published checksums](https://nodejs.org/download/release/v24.13.0/SHASUMS256.txt)).
Node's first-party build documentation states that official x64 binaries target
glibc 2.28+ and `GLIBCXX_3.4.25`, both within Noble's runtime generation
([Node BUILDING.md](https://github.com/nodejs/node/blob/main/BUILDING.md#platform-list)).
Node 24 is also new enough for the stable `--permission` model required by the
canonical worker ([Node permission model](https://nodejs.org/api/permissions.html)).

The canonical source graph itself has no external npm runtime import: its entry
uses Node built-ins and the worker reaches only project-owned model, animation,
SDF, mesh, exporter and common-parameter modules. The existing general package
metadata is not usable because it declares `gifenc`, Playwright and Three
([`packages/implicitjs/package.json`](../../packages/implicitjs/package.json#L41-L45)),
even though the canonical worker is already launched under filesystem grants
and an empty ambient environment
([`canonicalBuild.js`](../../packages/implicitjs/src/lib/implicitCad/canonicalBuild.js#L147-L186)).
The implementation must therefore generate a file-list-and-SHA-256 canonical
subset from the Source Snapshot; it must not run `npm install` in the Agent
image. The Node archive is the only external JavaScript runtime artifact.

## Project-owned native and Python artifacts

### `meshscope`

The source dependency declaration is currently open-ended (`trimesh`, `numpy`,
`Pillow`) and its build backend is only lower-bounded as `setuptools>=68`
([`pyproject.toml`](../../packages/meshscope/pyproject.toml#L1-L14)). Those fields
are unsuitable as the production lock and must be overridden by or changed to
the exact wheelhouse contract above.

The sole project extension is a small C++17 CPython module
([`setup.py`](../../packages/meshscope/setup.py#L10-L23)); its source uses the
CPython C API plus the C++ standard library and defines one normal CPython module
entrypoint
([`_native.cpp`](../../packages/meshscope/src/meshscope/voxblame/_native.cpp#L1-L12),
[`_native.cpp`](../../packages/meshscope/src/meshscope/voxblame/_native.cpp#L232-L247)).
This makes a CPython 3.12 linux/amd64 wheel straightforward, but it is not an
`abi3` extension and may dynamically require Noble's `libstdc++`, `libgcc_s` and
glibc. The admission gate must therefore:

1. build in a digest-pinned linux/amd64 builder with exact Python headers,
   compiler, binutils, setuptools and wheel hashes;
2. normalize source epoch/build paths or otherwise explain nondeterministic
   wheel bytes;
3. record compiler/linker commands and the source-tree digest;
4. inspect the resulting extension's interpreter, `DT_NEEDED`, symbol versions,
   RPATH/RUNPATH and resolved SONAME files;
5. run the native Cup measurement fixture in the runtime base; and
6. admit the resulting wheel by exact SHA-256.

The compiler and headers are builder-only. They are never copied into the Agent
image.

### browser-free `meshshot`

The current Python distribution unconditionally depends on both Pillow and
Playwright ([`packages/meshshot/pyproject.toml`](../../packages/meshshot/pyproject.toml#L5-L13)),
and `mesh-compare` imports its renderer at module startup
([`mesh-compare/cli.py`](../../skills/mesh-compare/scripts/mesh-compare/cli.py#L11-L27)).
Therefore no currently declared wheel satisfies the browser-free capability
contract. Implementation must split a pure-Python client/profile distribution
that contains the Broker request/response contract and PNG validation but no
Playwright import, browser runtime, bundled render JavaScript, or Playwright
dependency. Its only external Python dependency should be the already locked
Pillow wheel. Until that split is tested, the offline closure is **feasible but
not complete**.

### Codex

`SAR-004` already selected native Codex CLI 0.147.0 for
`x86_64-unknown-linux-musl` and specified an external byte-admission gate. Its
archive/executable hashes remain intentionally unresolved, so Codex is another
required admitted input rather than an online image-build dependency. The
musl-linked Codex executable is compatible with a glibc image only after the
prescribed direct-run and ELF inspection pass; the runtime must not assume that
the target triple alone proves static linkage or Node independence
([Codex artifact research](sealed-agent-runtime-codex-artifact.md)).

## Exact unresolved acquisition gaps

The dependency question is resolved “feasible” with these implementation gates;
the following values must still be filled before `Agent Runtime Verified` can be
claimed:

1. **Noble admission:** chosen linux/amd64 base manifest digest, Ubuntu snapshot
   timestamp, signed Release identity, and every resolved `.deb` filename,
   version and SHA-256. `universe` availability for `git-lfs` must be proven in
   that same snapshot.
2. **Python wheel admission:** mirrored bytes and ELF audit reports for the three
   exact third-party runtime wheels, plus the exact installer version/hash.
   Candidate hashes above prove availability but have not been downloaded or
   executed in this research task.
3. **VoxBlame wheel:** exact builder image/toolchain lock, admitted wheel hash,
   native SONAME closure and provider-free Cup measurement result.
4. **Browser-free meshshot:** package split, exact source/file manifest, wheel
   hash and a negative scan proving no Playwright/browser material.
5. **Canonical implicit subset:** generated source-file allowlist and hashes,
   Node 24.13.0 permission-model conformance, and a negative npm/browser import
   scan.
6. **Node admission:** verify the signed checksum, archive bytes, direct runtime
   version, dynamic library closure and the exact canonical Cup build/rebuild
   fixture in Noble.
7. **Codex admission:** archive and executable hashes, exact version output,
   ELF closure and Node-absent provider-free smoke required by `SAR-004`.

None of these gaps requires copying the CVM virtualenv or discovering another
large external CAD dependency. They are bounded artifact-production and
admission tasks that can run before any paid model pilot.

## Decision handed to the map

The dependency strategy should be:

> Build the first Agent image from one digest-pinned Noble amd64 base, one
> timestamped and mirrored `.deb` closure, three exact PyPI runtime wheels, one
> admitted project-native `meshscope` wheel, one browser-free project
> `meshshot` wheel, the signed-checksum Node 24.13.0 linux-x64 archive, the
> canonical-only implicit source manifest, and the separately admitted native
> Codex 0.147.0 artifact. Build and install offline; reject source fallback,
> undeclared ELF dependencies, browser material, and any dependency absent from
> the Cup capability manifest.

This is sufficient to proceed to implementation planning. Formal verification
must remain blocked until all seven admission gaps above have concrete hashes
and passing provider-free receipts.
