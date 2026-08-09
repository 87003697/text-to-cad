from __future__ import annotations

import asyncio
import base64
import copy
import json
import mimetypes
import os
import re
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

# Warm-daemon shim: must run BEFORE the cadgen imports below (which load
# cadgen/OCP at module import time). The daemon sets CADGEN_DAEMON_CHILD so it
# never recurses; the stdlib-only client keeps the cold path overhead-free.
if os.environ.get("CADGEN_WARM") == "1" and not os.environ.get("CADGEN_DAEMON_CHILD"):
    _daemon_scripts_dir = str(Path(__file__).resolve().parents[1])
    if _daemon_scripts_dir not in sys.path:
        sys.path.insert(0, _daemon_scripts_dir)
    from cadgen_daemon.client import run_via_daemon

    _warm_exit = run_via_daemon("snapshot", sys.argv[1:], os.getcwd())
    if _warm_exit is not None:
        raise SystemExit(_warm_exit)


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
PACKAGES_DIR = SCRIPTS_DIR / "packages"
CADPY_SRC_DIR = PACKAGES_DIR / "cadgen" / "src"
for runtime_path in (SCRIPTS_DIR, PACKAGES_DIR, CADPY_SRC_DIR):
    runtime_path_text = str(runtime_path)
    if runtime_path_text not in sys.path:
        sys.path.insert(0, runtime_path_text)

import cadgen.cad_ref_syntax as cad_ref_syntax
import cadgen.lookup as lookup
from cadgen.catalog import render_package_dir
from cadgen.step_targets import ResolvedStepTarget, StepTopologyArtifact, StepTopologyArtifactError
# The format-agnostic half of this CLI now lives in cadgen so the DXF skill can use
# it too; a skill may not import another skill's code. Everything STEP-specific --
# topology, selectors, parameter sidecars, the argument parser -- stays here.
from cadgen.snapshot_core import (
    APPEARANCE_OPTION_KEYS,
    BatchSnapshotRenderer,
    COMPLEX_ASSEMBLY_LARGE_RENDER_HEIGHT,
    COMPLEX_ASSEMBLY_LARGE_RENDER_WIDTH,
    COMPLEX_ASSEMBLY_RENDER_HEIGHT,
    COMPLEX_ASSEMBLY_RENDER_WIDTH,
    CONTACT_SHEET_RENDER_HEIGHT,
    CONTACT_SHEET_RENDER_WIDTH,
    DEFAULT_RENDER_THEME_ID,
    DEFAULT_TIMEOUT_SECONDS,
    DIAGNOSTIC_RENDER_HEIGHT,
    DIAGNOSTIC_RENDER_WIDTH,
    DISPLAY_MODE_ALIASES,
    DISPLAY_OPTION_KEYS,
    MESH_INPUT_KINDS,
    MESH_SUPPORTED_RENDER_MODES,
    ORBIT_RENDER_HEIGHT,
    ORBIT_RENDER_WIDTH,
    PRESENTATION_LARGE_RENDER_HEIGHT,
    PRESENTATION_LARGE_RENDER_WIDTH,
    PRESENTATION_RENDER_HEIGHT,
    PRESENTATION_RENDER_WIDTH,
    RENDER_BROWSER_STARTUP_TIMEOUT_MS,
    RouteFileError,
    SELECTION_SHAPED_JOB_KEYS,
    SETTINGS_KEY_HOMES,
    SIMPLE_RENDER_HEIGHT,
    SIMPLE_RENDER_WIDTH,
    SIMPLE_SQUARE_RENDER_HEIGHT,
    SIMPLE_SQUARE_RENDER_WIDTH,
    SNAPSHOT_ORIGIN,
    SNAPSHOT_RENDER_URL,
    SNAPSHOT_ROUTE_GLOB,
    SUPPORTED_JOB_KEYS,
    SUPPORTED_OUTPUT_KEYS,
    SUPPORTED_RENDER_MODES,
    SnapshotError,
    TOPOLOGY_DISPLAY_MODES,
    WORKBENCH_RENDER_THEME_IDS,
    appearance_theme_id_for_job,
    asset_url_for_path,
    content_type_for_path,
    default_render_size,
    encode_path_param,
    explicit_size_profile,
    is_plain_object,
    load_appearance_option,
    load_display_option,
    load_json_text,
    max_output_size,
    normalize_common_job,
    normalize_size_profile,
    normalize_snapshot_job_packet,
    parse_camera_option,
    path_is_inside_or_equal,
    positive_integer,
    print_render_result,
    render_resolved_job_packet,
    resolve_mesh_render_job,
    has_step_parameter_render_values,
    resolve_output_size,
    selection_filter_values,
    selection_value_list,
    resolve_snapshot_route_file,
    route_file,
    snapshot_timestamp,
    step_parameter_render_values_are_animated,
    timestamp_output_path,
    validate_direct_settings_payload,
    validate_display_settings_values,
    with_snapshot_timeout,
    write_output_payload,
    write_render_outputs,
)


RUNTIME_DIR = Path(__file__).resolve().parent / "runtime"
RENDER_HTML_PATH = RUNTIME_DIR / "render.html"
# Direct mesh inputs (no STEP topology). These render through the shared mesh path but
# cannot use STEP-only modes/params: section (CAD sectioning), selector focus/hide,
# stepParameters, exploded assembly views, or CAD-edge display modes.
# Direct implicit CAD inputs (`.implicit.js` raymarch modules). These render through
# the shared runtime's implicit backend, not the mesh/STEP path: no STEP topology,
# no selectors, no stepParameters, no exploded/section, no CAD-edge display modes.
IMPLICIT_INPUT_SUFFIX = ".implicit.js"
IMPLICIT_SUPPORTED_RENDER_MODES = {"view", "orbit"}

# Closed sets of accepted job/output keys. Anything outside them is rejected
# up front: an unrecognized key was historically dropped without a word, so a
# near-miss like top-level "hide" (instead of selection.hide) or a "selection"
# nested in an output produced a full-cost render that silently ignored the
# request. Reserved keys with dedicated error messages (theme, params,
# paramsPath, workspaceRoot, rootDir) are checked separately before this set.


# Must stay in step with what cadjs `normalizeThemeSettings()` actually reads.
# `colorMode` and `projection` were missing even though the renderer consumes
# both (themeSettings.js reads `source.colorMode` / `source.projection`) and the
# CLI help states "Projection is a theme trait taken from the appearance" — so
# an authored theme carrying either was rejected as malformed.
#
# `edges` is deliberately NOT here: edge settings belong in display JSON, which
# is a real separation (edges are a per-render display choice, not part of the
# saved theme) and is covered by test_edge_settings_belong_to_display_json.

# Keys that are valid settings, just in the OTHER payload. Naming the right
# home turns "unsupported keys: edges" — which reads like the file is broken —
# into an instruction.


ensure_step_topology_artifact = None


@dataclass
class SnapshotOptions:
    job: str = ""
    input: str = ""
    output: str = ""
    mode: str = "view"
    appearance: object = DEFAULT_RENDER_THEME_ID
    appearance_specified: bool = False
    display: object = ""
    display_specified: bool = False
    camera: object = "iso"
    camera_specified: bool = False
    width: int | None = None
    height: int | None = None
    size_profile: str = ""
    params: object = None
    params_specified: bool = False
    params_path: str = ""
    params_path_specified: bool = False
    focus: list[str] | None = None
    hide: list[str] | None = None
    view_labels: bool = False
    debug: bool = False
    json: bool = False
    help: bool = False






def help_text() -> str:
    return """Usage:
  python scripts/snapshot --job render-job.json
  python scripts/snapshot --job -
  python scripts/snapshot --input models/part.step --output /tmp/part.png --appearance workbench

Shortcut flags are for common STEP/STP snapshots. --job accepts one render job, an array of render jobs, or { "jobs": [...] }. Every job input must be a relative or absolute .step/.stp path, a same-stem Python generator, a direct .glb/.stl/.3mf mesh, or a .implicit.js model; direct DXF/G-code/robot-description inputs are unsupported. Mesh inputs render shaded solid through the shared mesh path and support camera, display projection (orthographic/perspective), appearance, size-profile, orbit, and list output, but reject STEP-only options: --params/stepParameters, --focus/--hide selector refs, exploded and non-solid display modes, and section mode. Implicit (.implicit.js) inputs render through the shared runtime's raymarch backend and support camera, appearance, and orbit output; they reject the same STEP-only options plus list/section modes (no part topology). The default appearance is the workbench saved theme. --appearance accepts a saved theme name, an inline JSON appearance settings object, or a JSON appearance settings file path. --display accepts solid, rendered, transparent, hidden_edges, hidden_lines_removed, unshaded, wireframe, an inline JSON display settings object, or a JSON display settings file path. Projection is a theme trait taken from the appearance (the default workbench/light theme is orthographic; the presentation stage themes are perspective); pass {"projection":"perspective"} in display JSON only to override it for a one-off. Exploded view and edge styling belong in display JSON. The exploded view is a single slider: {"mode":"rendered","exploded":{"enabled":true,"amount":0.7},"edges":{"color":"#132232"}}. "amount" is the 0..1 spread; the layout itself is automatic (hierarchical radial explode about the assembly center) with nothing else to configure. Use --mode to pick the render mode: view (default, one still image per output), orbit (360-degree turntable GIF), section (cutaway sweep), or list (returns part occurrence refs as JSON; no output files). A .gif output is only valid in orbit mode or with animated --params values; static jobs must save stills. --camera accepts a preset, azimuth:elevation pair, or JSON object with preset, position, target, up, and zoom fields. --focus and --hide accept one or more selector refs such as #o1.2 for parts or subassemblies; pass the flag repeatedly or list refs after the flag. Option JSON is direct settings JSON, not a wrapped job fragment. Full JSON jobs use top-level appearance and display. Use --view-labels to burn the camera/view label into shortcut outputs. Use --params with STEP parameter sidecar JSON values. A generated model declares its sidecar from gen_step(); an imported .step/.stp declares nothing, so pass --params-path (job field "stepParametersPath") naming the JS sidecar beside it -- --params alone is rejected there, and --params-path is rejected on a generated model, whose sidecar belongs in its .step.py. Use --size-profile for default dimensions such as simple, diagnostic, labeled, assembly, presentation, orbit, or contact-sheet. Output file names are saved with a shared UTC seconds timestamp before the extension. Use --debug (or a job-level "debug": true field) to add a "debug" section to --json output reporting how each STEP/STP artifact was resolved: source ("generated" from a .step.py generator vs. "imported" direct STEP), whether it was an assembly, whether it was served from cache or rebuilt, whether assembly selectors were re-extracted, and how long artifact resolution took. Everyday snapshot usage does not need --debug; it exists for diagnosing slow or unexpected renders.
"""




def parse_required_value(argv: Sequence[str], index: int, flag: str) -> str:
    try:
        value = argv[index + 1]
    except IndexError as exc:
        raise SnapshotError(f"{flag} requires a value") from exc
    if not value or value.startswith("--"):
        raise SnapshotError(f"{flag} requires a value")
    return value


def parse_required_values(argv: Sequence[str], index: int, flag: str) -> tuple[list[str], int]:
    values: list[str] = []
    cursor = index + 1
    while cursor < len(argv):
        value = argv[cursor]
        if value.startswith("--"):
            break
        if value:
            values.append(value)
        cursor += 1
    if not values:
        raise SnapshotError(f"{flag} requires at least one value")
    return values, cursor - index - 1


def parse_snapshot_args(argv: Sequence[str]) -> SnapshotOptions:
    if argv and argv[0] == "daemon":
        raise SnapshotError("snapshot daemon commands have been removed; use a batch --job snapshot instead")

    options = SnapshotOptions()
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in {"--help", "-h"}:
            options.help = True
        elif arg == "--json":
            options.json = True
        elif arg == "--no-daemon":
            raise SnapshotError("--no-daemon has been removed; snapshot no longer uses a daemon")
        elif arg == "--socket" or arg.startswith("--socket="):
            raise SnapshotError("--socket has been removed; snapshot no longer uses a daemon")
        elif arg == "--view-labels":
            options.view_labels = True
        elif arg == "--debug":
            options.debug = True
        elif arg == "--job":
            options.job = parse_required_value(argv, index, arg)
            index += 1
        elif arg.startswith("--job="):
            options.job = arg[len("--job=") :]
        elif arg == "--input":
            options.input = parse_required_value(argv, index, arg)
            index += 1
        elif arg.startswith("--input="):
            options.input = arg[len("--input=") :]
        elif arg in {"--output", "-o"}:
            options.output = parse_required_value(argv, index, arg)
            index += 1
        elif arg.startswith("--output="):
            options.output = arg[len("--output=") :]
        elif arg == "--mode":
            options.mode = parse_required_value(argv, index, arg)
            index += 1
        elif arg.startswith("--mode="):
            options.mode = arg[len("--mode=") :]
        elif arg == "--appearance":
            options.appearance = parse_required_value(argv, index, arg)
            options.appearance_specified = True
            index += 1
        elif arg.startswith("--appearance="):
            options.appearance = arg[len("--appearance=") :]
            options.appearance_specified = True
        elif arg == "--display":
            options.display = parse_required_value(argv, index, arg)
            options.display_specified = True
            index += 1
        elif arg.startswith("--display="):
            options.display = arg[len("--display=") :]
            options.display_specified = True
        elif arg == "--params":
            options.params = parse_required_value(argv, index, arg)
            options.params_specified = True
            index += 1
        elif arg.startswith("--params="):
            options.params = arg[len("--params=") :]
            options.params_specified = True
        elif arg == "--params-path":
            options.params_path = parse_required_value(argv, index, arg)
            options.params_path_specified = True
            index += 1
        elif arg.startswith("--params-path="):
            options.params_path = arg[len("--params-path=") :]
            options.params_path_specified = True
        elif arg == "--focus":
            values, consumed = parse_required_values(argv, index, arg)
            options.focus = [*(options.focus or []), *values]
            index += consumed
        elif arg.startswith("--focus="):
            value = arg[len("--focus=") :]
            if not value:
                raise SnapshotError("--focus requires at least one value")
            options.focus = [*(options.focus or []), value]
        elif arg == "--hide":
            values, consumed = parse_required_values(argv, index, arg)
            options.hide = [*(options.hide or []), *values]
            index += consumed
        elif arg.startswith("--hide="):
            value = arg[len("--hide=") :]
            if not value:
                raise SnapshotError("--hide requires at least one value")
            options.hide = [*(options.hide or []), value]
        elif arg == "--size-profile":
            options.size_profile = parse_required_value(argv, index, arg)
            index += 1
        elif arg.startswith("--size-profile="):
            options.size_profile = arg[len("--size-profile=") :]
        elif arg == "--camera":
            options.camera = parse_required_value(argv, index, arg)
            options.camera_specified = True
            index += 1
        elif arg.startswith("--camera="):
            options.camera = arg[len("--camera=") :]
            options.camera_specified = True
        elif arg == "--width":
            options.width = positive_integer(parse_required_value(argv, index, arg), arg)
            index += 1
        elif arg.startswith("--width="):
            options.width = positive_integer(arg[len("--width=") :], "--width")
        elif arg == "--height":
            options.height = positive_integer(parse_required_value(argv, index, arg), arg)
            index += 1
        elif arg.startswith("--height="):
            options.height = positive_integer(arg[len("--height=") :], "--height")
        else:
            raise SnapshotError(f"Unknown argument: {arg}")
        index += 1
    if options.focus and options.hide:
        raise SnapshotError("--focus and --hide cannot be used in the same snapshot command")
    return options








def parse_params_option(raw_params: object) -> dict[str, object]:
    parsed = load_json_text(str(raw_params or ""), "--params")
    if not is_plain_object(parsed):
        raise SnapshotError("--params must be a STEP parameter JSON object")
    return parsed


def option_focus_hide_specified(options: SnapshotOptions) -> bool:
    return bool(options.focus or options.hide)


def merge_focus_hide_options(job: dict[str, object], options: SnapshotOptions) -> None:
    if not option_focus_hide_specified(options):
        return
    if options.focus and options.hide:
        raise SnapshotError("--focus and --hide cannot be used in the same snapshot command")
    selection = dict(job.get("selection") if is_plain_object(job.get("selection")) else {})
    if options.focus:
        selection["focus"] = list(options.focus)
    if options.hide:
        selection["hide"] = list(options.hide)
    job["selection"] = selection










def apply_option_overrides_to_job(job: object, options: SnapshotOptions, *, cwd: Path) -> object:
    if not is_plain_object(job):
        return job
    if not any(
        [
            options.view_labels,
            options.debug,
            options.size_profile,
            options.params_specified,
            options.params_path_specified,
            options.display_specified,
            options.appearance_specified,
            options.camera_specified,
            option_focus_hide_specified(options),
        ]
    ):
        return job
    next_job = copy.deepcopy(job)
    merge_focus_hide_options(next_job, options)
    if options.debug:
        next_job["debug"] = True
    if options.appearance_specified:
        next_job["appearance"] = load_appearance_option(options.appearance, cwd=cwd)
    if options.params_specified:
        next_job["stepParameters"] = parse_params_option(options.params)
    if options.params_path_specified:
        next_job["stepParametersPath"] = options.params_path
    if options.display_specified:
        next_job["display"] = load_display_option(options.display, cwd=cwd)
    if options.camera_specified:
        next_job["camera"] = parse_camera_option(options.camera)
    render = dict(next_job.get("render") if is_plain_object(next_job.get("render")) else {})
    if options.view_labels:
        render["viewLabels"] = True
    if options.size_profile:
        render["sizeProfile"] = options.size_profile
    next_job["render"] = render
    return next_job


def apply_option_overrides_to_payload(payload: object, options: SnapshotOptions, *, cwd: Path) -> object:
    if isinstance(payload, list):
        return [apply_option_overrides_to_job(job, options, cwd=cwd) for job in payload]
    if is_plain_object(payload) and isinstance(payload.get("jobs"), list):
        next_payload = copy.deepcopy(payload)
        next_payload["jobs"] = [apply_option_overrides_to_job(job, options, cwd=cwd) for job in payload["jobs"]]
        return next_payload
    return apply_option_overrides_to_job(payload, options, cwd=cwd)


def load_job_from_options(
    options: SnapshotOptions,
    *,
    stdin: Any = sys.stdin,
    cwd: Path | None = None,
) -> object:
    resolved_cwd = (cwd or Path.cwd()).resolve()
    if options.job:
        if options.job == "-":
            text = stdin.read()
            source_label = "stdin"
        else:
            job_path = (resolved_cwd / options.job).resolve()
            text = job_path.read_text(encoding="utf-8")
            source_label = str(job_path)
        return apply_option_overrides_to_payload(load_json_text(text, source_label), options, cwd=resolved_cwd)

    if not stdin.isatty() and not options.input:
        text = stdin.read()
        if text.strip():
            return apply_option_overrides_to_payload(load_json_text(text, "stdin"), options, cwd=resolved_cwd)

    if not options.input:
        raise SnapshotError("render requires --job, stdin JSON, or --input")
    if options.mode != "list" and not options.output:
        raise SnapshotError("render shortcut requires --output for non-list modes")

    output: dict[str, object] = {
        "path": options.output,
        "camera": parse_camera_option(options.camera),
    }
    if options.width:
        output["width"] = options.width
    if options.height:
        output["height"] = options.height

    job: dict[str, object] = {
        "input": options.input,
        "mode": options.mode,
        "outputs": [] if options.mode == "list" else [output],
        "appearance": load_appearance_option(options.appearance, cwd=resolved_cwd),
        "render": {"viewLabels": options.view_labels},
    }
    if options.size_profile:
        job["render"]["sizeProfile"] = options.size_profile
    if options.display_specified:
        job["display"] = load_display_option(options.display, cwd=resolved_cwd)
    if options.params_specified:
        job["stepParameters"] = parse_params_option(options.params)
    if options.params_path_specified:
        job["stepParametersPath"] = options.params_path
    if options.debug:
        job["debug"] = True
    merge_focus_hide_options(job, options)
    return job




def input_kind(file_path: Path) -> str:
    # `.implicit.js` is a compound suffix (Path.suffix sees only `.js`), so match
    # the full name before the single-suffix checks below.
    if file_path.name.lower().endswith(IMPLICIT_INPUT_SUFFIX):
        return "implicit"
    suffix = file_path.suffix.lower()
    if suffix == ".step":
        return "step"
    if suffix == ".stp":
        return "stp"
    if suffix == ".py":
        return "python"
    if suffix == ".glb":
        return "glb"
    if suffix == ".stl":
        return "stl"
    if suffix == ".3mf":
        return "3mf"
    return ""


def logical_step_path_for_python_source(source_path: Path) -> Path:
    # `<name>.step.py` -> `<name>.step` (strip only `.py`; the stem already ends in `.step`).
    name = source_path.name
    if name.endswith(".step.py"):
        return source_path.with_name(name[: -len(".py")])
    return source_path.with_suffix(".step")


def same_stem_python_generator_path(step_path: Path) -> Path | None:
    # The generator for `<name>.step` is `<name>.step.py` (append `.py` to the full step filename).
    candidate = step_path.with_name(step_path.name + ".py")
    try:
        return candidate if re.search(r"\bgen_step\s*\(", candidate.read_text(encoding="utf-8")) else None
    except OSError:
        return None


def resolve_input_path(raw_input: object, *, cwd: Path) -> Path:
    input_text = str(raw_input or "").strip()
    if not input_text:
        raise SnapshotError("render job is missing input")
    raw_path = Path(input_text)
    selected = raw_path.resolve() if raw_path.is_absolute() else (cwd / raw_path).resolve()
    if not selected.exists():
        if selected.suffix.lower() in {".step", ".stp"} and same_stem_python_generator_path(selected):
            return selected
        raise SnapshotError(f"Render input does not exist: {input_text}")
    return selected






def resolve_explicit_step_parameter_path(
    raw_value: object,
    *,
    root_path: Path,
    resolved_cwd: Path,
) -> Path:
    """The sidecar named by --params-path / a job's ``stepParametersPath``.

    Only imported STEP/STP inputs may name one: a generated model declares its
    sidecar from ``gen_step()``, and letting the command line point somewhere
    else would render parameters the model itself does not claim."""
    text = str(raw_value or "").strip()
    if not text:
        raise SnapshotError("stepParametersPath must name a JS parameter sidecar")
    candidate = Path(text)
    resolved = (candidate if candidate.is_absolute() else resolved_cwd / candidate).resolve()
    if resolved.suffix.lower() not in (".js", ".mjs"):
        raise SnapshotError(f"stepParametersPath must be a .js or .mjs parameter sidecar: {text}")
    if not resolved.is_file():
        raise SnapshotError(f"stepParametersPath does not exist: {text}")
    # The renderer fetches assets relative to the STEP's own folder, so a sidecar
    # outside it has no URL to serve -- reject it here rather than fail mid-render.
    if not path_is_inside_or_equal(resolved, root_path):
        raise SnapshotError(
            f"stepParametersPath must sit inside the STEP model folder ({root_path}): {text}"
        )
    return resolved


def step_parameter_path_for_step_source(package_dir: Path, input_path: Path) -> Path | None:
    """The hand-authored JS sidecar a model declares via its package descriptor's
    ``paramsPath`` (model-folder-relative, set from gen_step()'s ``params``). Returns None
    when the model declares no sidecar — there is no filename convention."""
    try:
        descriptor = json.loads((package_dir / "assembly.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    params_path = str(descriptor.get("paramsPath") or "").strip()
    if not params_path:
        return None
    return (input_path.parent / params_path).resolve()






















def reference_root_for_input(input_path: Path, cwd: Path) -> Path:
    return cwd if path_is_inside_or_equal(input_path, cwd) else input_path.parent


def cad_ref_for_step_path(repo_root: Path, step_path: Path) -> str:
    try:
        relative = step_path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        relative = step_path.resolve().as_posix()
    suffix = step_path.suffix
    return relative[: -len(suffix)] if suffix else relative


def load_ensure_step_topology_artifact():
    global ensure_step_topology_artifact
    if ensure_step_topology_artifact is None:
        from cadgen.step_artifacts import ensure_step_topology_artifact as imported_ensure

        ensure_step_topology_artifact = imported_ensure
    return ensure_step_topology_artifact






def selector_value_requires_topology(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    parsed = cad_ref_syntax.parse_selector(text)
    return parsed is not None and parsed.selector_type != "opaque"


def selection_requires_selector_topology(job: Mapping[str, object]) -> bool:
    return any(selector_value_requires_topology(value) for value in selection_filter_values(job))


def ensure_render_job_step_artifact(
    job: Mapping[str, object],
    *,
    reference_root: Path,
    input_path: Path,
    step_path: Path,
    require_selector: bool = False,
    debug_info: dict[str, object] | None = None,
) -> StepTopologyArtifact:
    target = ResolvedStepTarget(
        cad_path=cad_ref_for_step_path(reference_root, step_path),
        kind="part",
        source_path=input_path,
        step_path=step_path,
        # A job input naming the .py generator must keep using the generator
        # entry even when a same-stem exported .step exists beside it.
        explicit_python=input_path.suffix.lower() == ".py",
    )
    try:
        ensure_artifact = load_ensure_step_topology_artifact()
        return ensure_artifact(
            target,
            require_selector=require_selector,
            debug=debug_info,
        )
    except StepTopologyArtifactError as exc:
        raise SnapshotError(str(exc)) from exc


def artifact_selector_index(artifact: StepTopologyArtifact | None) -> lookup.SelectorIndex | None:
    selector_bundle = artifact.selector_bundle if artifact is not None else None
    if selector_bundle is None:
        return None
    manifest = selector_bundle.manifest if isinstance(selector_bundle.manifest, dict) else None
    if manifest is None:
        return None
    buffers = selector_bundle.buffers if isinstance(selector_bundle.buffers, Mapping) else None
    return lookup.build_selector_index(manifest, buffers=buffers)


def validate_occurrence_selector(selector: str, *, selector_index: lookup.SelectorIndex | None, source_label: str) -> None:
    if selector_index is None:
        return
    if selector not in selector_index.occurrence_by_id:
        raise SnapshotError(f"{source_label} references unknown part/subassembly occurrence selector: {selector}")


def normalize_selection_selector(
    raw_value: str,
    *,
    selector_index: lookup.SelectorIndex | None,
    source_label: str,
) -> list[str]:
    text = str(raw_value or "").strip()
    if not text:
        return []
    parsed = cad_ref_syntax.parse_selector(text)
    if parsed is None:
        return []
    if parsed.selector_type == "opaque":
        return [parsed.canonical]
    if parsed.selector_type != "occurrence":
        raise SnapshotError(
            f"{source_label} supports only part/subassembly occurrence refs; "
            f"got {parsed.selector_type} selector {text!r}"
        )
    validate_occurrence_selector(parsed.canonical, selector_index=selector_index, source_label=source_label)
    return [parsed.canonical]


def normalize_selection_filter_values(
    value: object,
    *,
    expected_cad_path: str,
    selector_index: lookup.SelectorIndex | None,
    source_label: str,
) -> list[str]:
    _ = expected_cad_path
    selectors: list[str] = []
    for raw_value in selection_value_list(value):
        selectors.extend(
            normalize_selection_selector(raw_value, selector_index=selector_index, source_label=source_label)
        )
    return selectors


def normalize_render_job_selection(
    job: Mapping[str, object],
    *,
    expected_cad_path: str,
    selector_index: lookup.SelectorIndex | None,
) -> dict[str, object] | None:
    selection = job.get("selection") if is_plain_object(job.get("selection")) else None
    if selection is None:
        return None
    if any(selection_value_list(selection.get(key)) for key in ("focus", "refs")) and selection_value_list(
        selection.get("hide")
    ):
        raise SnapshotError("selection.focus/refs and selection.hide cannot be used in the same snapshot job")
    normalized = dict(selection)
    for key in ("focus", "refs", "hide"):
        if key not in selection:
            continue
        normalized[key] = normalize_selection_filter_values(
            selection.get(key),
            expected_cad_path=expected_cad_path,
            selector_index=selector_index,
            source_label=f"selection.{key}",
        )
    return normalized






def resolve_implicit_render_job(
    job: dict[str, object],
    *,
    kind: str,
    input_path: Path,
    root_path: Path,
    resolved_cwd: Path,
    timestamp: str | None,
    **_kind_context: object,
) -> dict[str, object]:
    """Resolve a direct implicit CAD input (`.implicit.js`).

    Implicit models render through the shared runtime's raymarch backend, so this
    skips the STEP artifact/package pipeline and hands the renderer the module URL.
    STEP-only options are rejected up front with clear errors."""
    # Selector focus/hide/refs need the selector index built from STEP topology.
    if selection_filter_values(job):
        raise SnapshotError(
            "selection focus/hide/refs require STEP topology; implicit models have no "
            "part/subassembly selectors"
        )
    # stepParameters drive a STEP parameter sidecar; implicit models carry their own descriptors.
    if has_step_parameter_render_values(job.get("stepParameters")):
        raise SnapshotError(
            "stepParameters require a STEP model; implicit models are parameterized by their "
            "own descriptor, not STEP sidecars"
        )
    if job.get("stepParametersPath") is not None:
        raise SnapshotError(
            "stepParametersPath requires a STEP model; implicit models are parameterized by their "
            "own descriptor, not STEP sidecars"
        )

    mode = str(job.get("mode") or "view").strip().lower()
    if mode not in SUPPORTED_RENDER_MODES:
        raise SnapshotError(f"Unsupported render mode: {mode or '(missing)'}")
    if mode not in IMPLICIT_SUPPORTED_RENDER_MODES:
        supported = ", ".join(sorted(IMPLICIT_SUPPORTED_RENDER_MODES))
        raise SnapshotError(
            f"{mode} mode is not supported for implicit inputs; implicit models support: {supported}"
        )

    # Implicit rendering is raymarched shading — there is no CAD topology for
    # wireframe/edges/exploded, so reject anything but the default solid shading.
    display = job.get("display") if is_plain_object(job.get("display")) else {}
    raw_display_mode = re.sub(r"[\s-]+", "_", str(display.get("mode") or "").strip().lower())
    canonical_display_mode = DISPLAY_MODE_ALIASES.get(raw_display_mode, raw_display_mode)
    if canonical_display_mode and canonical_display_mode != "solid":
        raise SnapshotError(
            f"{canonical_display_mode} display mode is not supported for implicit inputs; "
            "implicit models render raymarched shading"
        )
    exploded = display.get("exploded") if is_plain_object(display.get("exploded")) else None
    if exploded is not None and exploded.get("enabled"):
        raise SnapshotError(
            "exploded view requires STEP assembly occurrence structure; implicit models "
            "cannot be exploded"
        )

    asset_url = asset_url_for_path(input_path, root_path)
    resolved: dict[str, object] = {
        "rootPath": str(root_path),
        "inputPath": str(input_path),
        "inputUrl": asset_url,
        "kind": "implicit",
        "url": asset_url,
    }
    if bool(job.get("debug")):
        resolved["debug"] = {"implicitSource": {"kind": "implicit"}}

    normalized = normalize_common_job(job, mode=mode, resolved_cwd=resolved_cwd, timestamp=timestamp)
    normalized["resolved"] = resolved
    return normalized


def resolve_render_job(
    raw_job: object,
    *,
    cwd: Path | None = None,
    timestamp: str | None = None,
) -> dict[str, object]:
    if not is_plain_object(raw_job):
        raise SnapshotError("render job must be an object")
    job = copy.deepcopy(raw_job)
    if "theme" in job:
        raise SnapshotError("render jobs use appearance; theme is reserved for saved appearance settings")
    if "params" in job:
        raise SnapshotError("render jobs use stepParameters; params is reserved for shortcut --params parsing")
    if "paramsPath" in job:
        raise SnapshotError(
            "render jobs use stepParametersPath; paramsPath is the descriptor field a generator writes"
        )
    forbidden_root_fields = [field for field in ("workspaceRoot", "rootDir") if field in job]
    if forbidden_root_fields:
        raise SnapshotError(
            "snapshot jobs no longer accept workspaceRoot or rootDir; pass a relative or absolute input path instead"
        )

    # Every other key must come from the closed job schema. Selection-shaped
    # near-misses get the exact fix spelled out; anything else is named with
    # the supported set so a typo fails here instead of rendering as if the
    # key were absent.
    unknown_keys = sorted(set(job) - SUPPORTED_JOB_KEYS)
    if unknown_keys:
        selection_shaped = [key for key in SELECTION_SHAPED_JOB_KEYS if key in unknown_keys]
        if selection_shaped:
            fields = ", ".join(f'"{key}": [...]' for key in selection_shaped)
            raise SnapshotError(
                f"render jobs take part selectors inside the selection object, not at top level; "
                f"move {fields} into \"selection\": {{...}}"
            )
        raise SnapshotError(
            f"unknown render job key(s): {', '.join(unknown_keys)}; "
            f"supported keys: {', '.join(sorted(SUPPORTED_JOB_KEYS))}"
        )

    resolved_cwd = (cwd or Path.cwd()).resolve()
    raw_input = str(job.get("input") or "").strip()
    if not raw_input:
        raise SnapshotError("render job is missing input")

    # A job's own `display` string gets the same treatment as the --display
    # flag: a mode name, an inline JSON object, or a path to a display JSON.
    # Without this it fell through to normalize_common_job, which accepts only
    # a plain object and silently substituted {"mode": "solid"} -- so
    # "wireframe", a file path, and an outright typo all rendered the default.
    raw_display = job.get("display")
    if isinstance(raw_display, str) and raw_display.strip():
        job["display"] = load_display_option(raw_display, cwd=resolved_cwd)

    # Closed-set display values are validated for the --display flag path in
    # load_display_option; a display object embedded in a full JSON job must get
    # the same guard, or a typo'd projection/mode silently renders the default.
    if is_plain_object(job.get("display")):
        validate_display_settings_values(job["display"], source_label="job display")

    input_path = resolve_input_path(raw_input, cwd=resolved_cwd)
    root_path = input_path.parent.resolve()
    reference_root = reference_root_for_input(input_path, resolved_cwd)
    kind = input_kind(input_path)
    source_path = input_path
    if kind == "python":
        input_path = logical_step_path_for_python_source(input_path)
        root_path = input_path.parent.resolve()
        kind = "step"
    resolver = KIND_RESOLVERS.get(kind)
    if resolver is None:
        raise SnapshotError(
            "Snapshot supports STEP/STP inputs, same-stem Python generators, "
            "direct GLB/STL/3MF meshes, or .implicit.js models"
        )
    return resolver(
        job,
        kind=kind,
        input_path=input_path,
        root_path=root_path,
        source_path=source_path,
        reference_root=reference_root,
        resolved_cwd=resolved_cwd,
        timestamp=timestamp,
    )


def resolve_step_render_job(
    job: dict[str, object],
    *,
    kind: str,
    input_path: Path,
    root_path: Path,
    source_path: Path,
    reference_root: Path,
    resolved_cwd: Path,
    timestamp: str | None,
    **_kind_context: object,
) -> dict[str, object]:
    has_param_render = has_step_parameter_render_values(job.get("stepParameters"))
    animated_params = step_parameter_render_values_are_animated(job.get("stepParameters"))
    # `source_path` is the catalog entry: a `.step.py` generator, or the STEP itself
    # when the model was imported. Which one it is decides where a sidecar may come
    # from. Settled before any artifact work so a misuse fails immediately rather
    # than after a build.
    entry_is_generated = source_path.suffix.lower() == ".py"
    explicit_parameter_path: Path | None = None
    if job.get("stepParametersPath") is not None:
        if entry_is_generated:
            raise SnapshotError(
                "stepParametersPath is for imported STEP/STP files; a generated model declares "
                f"its sidecar from gen_step() (params: ...) in {source_path.name}"
            )
        if not has_param_render:
            raise SnapshotError("stepParametersPath needs stepParameters values (--params-path needs --params)")
        explicit_parameter_path = resolve_explicit_step_parameter_path(
            job["stepParametersPath"],
            root_path=root_path,
            resolved_cwd=resolved_cwd,
        )

    debug_enabled = bool(job.get("debug"))
    step_artifact_debug: dict[str, object] | None = {} if debug_enabled else None
    artifact = ensure_render_job_step_artifact(
        job,
        reference_root=reference_root,
        input_path=source_path,
        step_path=input_path,
        require_selector=selection_requires_selector_topology(job),
        debug_info=step_artifact_debug,
    )
    expected_cad_path = cad_ref_for_step_path(reference_root, input_path)
    normalized_selection = normalize_render_job_selection(
        job,
        expected_cad_path=expected_cad_path,
        selector_index=artifact_selector_index(artifact),
    )

    # The render cache is keyed by the ENTRY filename (`source_path`: the `.step.py` generator for
    # a generated model, or the `.step`/`.stp` itself), not the logical step path.
    package_dir = render_package_dir(source_path)
    if not package_dir.is_dir():
        raise SnapshotError(f"STEP/STP render input is missing its render package: {package_dir}")

    step_parameter_path = explicit_parameter_path or step_parameter_path_for_step_source(
        package_dir, input_path
    )

    mode = str(job.get("mode") or "view").strip().lower()
    if mode not in SUPPORTED_RENDER_MODES:
        raise SnapshotError(f"Unsupported render mode: {mode or '(missing)'}")
    if has_param_render and mode != "view":
        raise SnapshotError("stepParameters support only view mode; set display.mode for display-style changes")
    if has_param_render and (step_parameter_path is None or not step_parameter_path.exists()):
        if entry_is_generated:
            raise SnapshotError(
                "STEP/STP render stepParameters require a parameter sidecar the model declares "
                f"(gen_step params / descriptor paramsPath): {input_path}"
            )
        raise SnapshotError(
            "imported STEP/STP render stepParameters require --params-path naming the JS parameter "
            f"sidecar; an imported file declares none: {input_path}"
        )

    outputs = job.get("outputs") if isinstance(job.get("outputs"), list) else []
    if animated_params and len(outputs) != 1:
        raise SnapshotError("animated stepParameters require exactly one output")

    resolved: dict[str, object] = {
        "rootPath": str(root_path),
        "inputPath": str(input_path),
        "inputUrl": asset_url_for_path(input_path, root_path),
        "kind": kind,
        "packagePath": str(package_dir),
    }
    # Component-GLB package (the canonical render artifact for every STEP model): inline
    # the descriptor and pre-resolve one asset URL per unique component GLB so the renderer
    # fetches and composes them in world space.
    descriptor = json.loads((package_dir / "assembly.json").read_text())
    component_urls = {
        cid: asset_url_for_path(package_dir / str(entry.get("glb", "")), root_path)
        for cid, entry in (descriptor.get("components") or {}).items()
    }
    resolved["package"] = {"descriptor": descriptor, "componentUrls": component_urls}
    if step_parameter_path is not None and step_parameter_path.exists():
        resolved["stepParameterPath"] = str(step_parameter_path)
        resolved["stepParameterUrl"] = asset_url_for_path(step_parameter_path, root_path)
    if debug_enabled:
        resolved["debug"] = {"stepArtifact": step_artifact_debug}

    if normalized_selection is not None:
        job["selection"] = normalized_selection

    normalized = normalize_common_job(job, mode=mode, resolved_cwd=resolved_cwd, timestamp=timestamp)
    normalized["resolved"] = resolved
    return normalized


# Kind dispatch for render-job resolution. Every resolver takes the same
# signature (job plus the resolved input-kind context) and returns the common
# normalized job shape with a kind-specific ``resolved`` payload — adding a new
# input kind is one table entry, not another if-chain arm plus a copied tail.
KIND_RESOLVERS: dict[str, Callable[..., dict[str, object]]] = {
    "step": resolve_step_render_job,
    "stp": resolve_step_render_job,
    "glb": resolve_mesh_render_job,
    "stl": resolve_mesh_render_job,
    "3mf": resolve_mesh_render_job,
    "implicit": resolve_implicit_render_job,
}


def resolve_render_job_packet(raw_payload: object, *, cwd: Path | None = None) -> dict[str, object]:
    single, jobs = normalize_snapshot_job_packet(raw_payload)
    timestamp = snapshot_timestamp()
    return {
        "single": single,
        "jobs": [resolve_render_job(job, cwd=cwd, timestamp=timestamp) for job in jobs],
    }






















async def run_render_cli_async(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    stdout: Any = sys.stdout,
    stdin: Any = sys.stdin,
) -> int:
    options = parse_snapshot_args(argv)
    if options.help:
        stdout.write(help_text())
        return 0
    raw_payload = load_job_from_options(options, stdin=stdin, cwd=cwd)
    packet = resolve_render_job_packet(raw_payload, cwd=cwd)
    result = await render_resolved_job_packet(packet, runtime_dir=RUNTIME_DIR)
    write_render_outputs(result)
    print_render_result(result, json_output=options.json, stdout=stdout)
    return 0


def run_render_cli(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    stdout: Any = sys.stdout,
    stderr: Any = sys.stderr,
    stdin: Any = sys.stdin,
) -> int:
    try:
        return asyncio.run(run_render_cli_async(argv, cwd=cwd, stdout=stdout, stdin=stdin))
    except SnapshotError as exc:
        stderr.write(f"{exc}\n")
        return 1
    except Exception as exc:
        stderr.write(f"{exc}\n")
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    return run_render_cli(list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
