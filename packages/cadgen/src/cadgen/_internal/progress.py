"""Progress reporting for one model's render-artifact build.

A build has stages that cost real time, and only one of them has a denominator
known before the work starts:

* ``generate`` -- running the model's ``gen_step()`` (or parsing an imported
  STEP). Arbitrary user code; there is no unit of work to count.
* ``package`` -- walking the composed compound, serializing each leaf's BREP and
  content-hashing it. Countable only as it goes: the leaf count is not known
  until the walk ends.
* ``components`` -- meshing + selector-extracting every component GLB that is not
  already in the package's content-addressed cache. The work list is built in
  full before the first mesh runs, so ``done``/``total`` here is MEASURED, not
  estimated -- and this is where most of a slow build's time goes.
* ``finalize`` -- descriptor write + orphan prune. Fast.

So the honest report is an exact count during ``components`` and a phase label
for the opaque stages. Every event also carries ``ratioFloor``/``ratioCeiling``
and the phase's expected duration, so a reader that polls -- the CAD Viewer --
can interpolate an indeterminate stage against the wall clock without the build
having to say anything while it is busy.

**Events are emitted at work boundaries, never from a timer.** A heartbeat
thread is precisely what the generation lock had to stop relying on: OCP meshing
holds the GIL inside C for long stretches, so a heartbeat starves during exactly
the work it is meant to be reporting (see :mod:`cadgen._internal.generation_status`).
Readers interpolate instead.

Two sinks consume the events: the JSON sidecar beside the model's package (read
by the CAD Viewer's artifact-status route) and, on a CLI run, the terminal status
board.

The sidecar is advisory DECORATION only. The viewer's ready/generating/error
state machine stays driven by the generation lock, so a progress file left behind
by a killed build can never by itself make the viewer believe a build is running.
That is also why the file is not unlinked when a build ends: its terminal event
carries the run's measured ``stageMs``, which the NEXT build of this model reads
to weight its own bar. One file, no unlink race, and the phase split adapts to
the model instead of being guessed the same way for every one.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping, Sequence

PROGRESS_SUFFIX = ".generation.progress.json"
PROGRESS_SCHEMA_VERSION = 1

PHASE_GENERATE = "generate"
PHASE_PACKAGE = "package"
PHASE_COMPONENTS = "components"
PHASE_FINALIZE = "finalize"
# Terminal marker. Not a phase with a weight -- it means "this file describes a
# finished run", and a reader must never render it as an in-flight build.
PHASE_DONE = "done"

PHASE_ORDER = (PHASE_GENERATE, PHASE_PACKAGE, PHASE_COMPONENTS, PHASE_FINALIZE)

PHASE_LABELS = {
    PHASE_GENERATE: "Building geometry",
    PHASE_PACKAGE: "Collecting parts",
    PHASE_COMPONENTS: "Meshing components",
    PHASE_FINALIZE: "Writing package",
    PHASE_DONE: "Done",
}

# Fallback split, used only for a model that has never recorded a build. Rough by
# definition; the first completed build replaces it with this model's real times.
DEFAULT_PHASE_WEIGHTS = {
    PHASE_GENERATE: 0.34,
    PHASE_PACKAGE: 0.12,
    PHASE_COMPONENTS: 0.50,
    PHASE_FINALIZE: 0.04,
}
# Floor on any learned weight: a stage that was instant last time (every component
# cached, say) must still leave the bar somewhere to go if it is slow this time.
MIN_PHASE_WEIGHT = 0.02
# An indeterminate phase never fills its whole band -- reaching 100% of a stage
# whose end we cannot see would be a lie, and leaves nothing for the handoff.
INDETERMINATE_PHASE_CEILING = 0.95
# Floor between sidecar writes / status repaints for INDETERMINATE ticks, so the
# per-leaf walk of a large assembly cannot spend the build's time on reporting.
# Determinate ticks (see ProgressReporter.advance) bypass it.
MIN_EMIT_INTERVAL_MS = 100.0


def progress_path(package_dir: Path | str) -> Path:
    """The model's progress sidecar, beside its ``__cadgen__`` package directory.

    Mirrors :func:`cadgen._internal.generation_status.generation_lock_path`: for a
    package dir ``<folder>/__cadgen__/models/<name>.step`` the sidecar is the hidden
    sibling ``<folder>/__cadgen__/models/.<name>.step.generation.progress.json``. A
    fixed path per model, under gitignored ``__cadgen__``, so the viewer can read it
    during an artifact-status poll (see the viewer's server_py/artifact.py reader)."""
    package = Path(package_dir)
    return package.parent / f".{package.name}{PROGRESS_SUFFIX}"


def phase_weights_from_stage_ms(stage_ms: object) -> dict[str, float] | None:
    """Phase weights proportional to a previous build's measured stage times.

    A model whose generator dominates and a model whose meshing dominates need very
    different splits before one overall bar moves evenly, and the last build of THIS
    model is the best available predictor of the next one. Returns None when there is
    nothing usable to learn from, leaving :data:`DEFAULT_PHASE_WEIGHTS` in place."""
    if not isinstance(stage_ms, Mapping):
        return None
    values: dict[str, float] = {}
    for phase in PHASE_ORDER:
        try:
            value = float(stage_ms.get(phase))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value >= 0.0:
            values[phase] = value
    total = sum(values.values())
    if len(values) < 2 or total <= 0.0:
        return None
    floored = {phase: max(value / total, MIN_PHASE_WEIGHT) for phase, value in values.items()}
    scale = sum(floored.values())
    return {phase: weight / scale for phase, weight in floored.items()}


def render_progress_bar(ratio: float, width: int = 18) -> str:
    """A fixed-width unicode bar. Shared by the CLI sink and its tests so the two
    cannot disagree about what a given ratio looks like."""
    width = max(1, int(width))
    clamped = min(1.0, max(0.0, float(ratio)))
    filled = int(round(clamped * width))
    return f"▕{'█' * filled}{'░' * (width - filled)}▏"


@dataclass(frozen=True)
class ProgressEvent:
    """One observation of a build's position. ``total`` is None for a phase with no
    knowable denominator; ``determinate`` says whether ``done``/``total`` is a real
    count or the phase is being estimated against the clock."""

    phase: str
    label: str
    done: int
    total: int | None
    determinate: bool
    ratio: float
    ratio_floor: float
    ratio_ceiling: float
    phase_started_at_ms: float
    phase_expected_ms: float | None
    elapsed_ms: float
    detail: str = ""
    stage_ms: Mapping[str, float] | None = None

    @property
    def finished(self) -> bool:
        return self.phase == PHASE_DONE

    def payload(self) -> dict[str, object]:
        """The sidecar's JSON shape (camelCase -- it is read by the viewer)."""
        return {
            "schemaVersion": PROGRESS_SCHEMA_VERSION,
            "phase": self.phase,
            "label": self.label,
            "detail": self.detail,
            "done": self.done,
            "total": self.total,
            "determinate": self.determinate,
            "ratio": round(self.ratio, 4),
            "ratioFloor": round(self.ratio_floor, 4),
            "ratioCeiling": round(self.ratio_ceiling, 4),
            "phaseStartedAt": round(self.phase_started_at_ms),
            "phaseExpectedMs": (
                round(self.phase_expected_ms) if self.phase_expected_ms else None
            ),
            "elapsedMs": round(self.elapsed_ms),
            "updatedAt": round(time.time() * 1000.0),
            "pid": os.getpid(),
            "stageMs": ({k: round(v) for k, v in self.stage_ms.items()} if self.stage_ms else None),
        }


class ProgressReporter:
    """Tracks the current phase and pushes :class:`ProgressEvent`s to its sinks.

    ``stage_ms`` is the previous build's measured per-phase durations: it both
    weights the overall bar and supplies the expected duration an indeterminate
    phase is interpolated against. A reporter with no sinks is inert bookkeeping,
    which is what makes the no-progress call paths free of branching."""

    def __init__(
        self,
        *,
        sinks: Sequence[Callable[[ProgressEvent], None]] = (),
        stage_ms: Mapping[str, float] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sinks = [sink for sink in sinks if sink is not None]
        self._clock = clock
        self._expected = dict(stage_ms or {})
        self._weights = phase_weights_from_stage_ms(stage_ms) or dict(DEFAULT_PHASE_WEIGHTS)
        self._started = clock()
        self._phase = ""
        self._phase_started = self._started
        self._phase_started_wall_ms = time.time() * 1000.0
        self._done = 0
        self._total: int | None = None
        self._detail = ""
        self._ratio = 0.0
        self._stage_ms: dict[str, float] = {}
        self._last_emit = 0.0
        self._finished = False

    # --- driving the run ---

    def phase(self, name: str, *, total: int | None = None, detail: str = "") -> None:
        """Enter ``name``. ``total`` makes the phase determinate (a real count)."""
        if self._finished:
            return
        self._close_phase()
        self._phase = name
        self._phase_started = self._clock()
        self._phase_started_wall_ms = time.time() * 1000.0
        self._done = 0
        self._total = total if (total is None or total > 0) else 0
        self._detail = detail
        self._emit(force=True)

    def set_total(self, total: int | None) -> None:
        """Supply a denominator discovered mid-phase (a walk that has just ended)."""
        if self._finished:
            return
        self._total = total
        self._emit(force=True)

    def advance(self, count: int = 1, *, detail: str | None = None) -> None:
        """Record ``count`` more units of the current phase."""
        if self._finished:
            return
        self._done += count
        if detail is not None:
            self._detail = detail
        # Every tick of a DETERMINATE phase reports, unthrottled. Those ticks are the
        # measured ones and they are coarse -- one per component GLB, often seconds
        # apart -- so dropping one to a rate limit would pin a visible count at a stale
        # value for however long the next component takes. Only the indeterminate walk,
        # which ticks once per assembly leaf, is throttled.
        self._emit(force=self._determinate())

    def finish(self) -> None:
        """Terminal event: ratio 1.0, ``phase: done``, and the run's measured stage
        times so the next build of this model can weight its bar from them."""
        if self._finished:
            return
        self._close_phase()
        self._finished = True
        self._phase = PHASE_DONE
        self._ratio = 1.0
        self._done = 0
        self._total = None
        self._detail = ""
        self._emit(force=True)

    def stage_ms_snapshot(self) -> dict[str, float]:
        """Stage durations recorded so far, including the phase still running."""
        snapshot = dict(self._stage_ms)
        if self._phase in PHASE_ORDER:
            elapsed = (self._clock() - self._phase_started) * 1000.0
            snapshot[self._phase] = snapshot.get(self._phase, 0.0) + elapsed
        return snapshot

    # --- internals ---

    def _close_phase(self) -> None:
        if self._phase not in PHASE_ORDER:
            return
        elapsed_ms = (self._clock() - self._phase_started) * 1000.0
        self._stage_ms[self._phase] = self._stage_ms.get(self._phase, 0.0) + elapsed_ms

    def _determinate(self) -> bool:
        return self._total is not None and self._total > 0

    def _bounds(self) -> tuple[float, float]:
        """The (floor, ceiling) of the current phase's band in the overall bar."""
        if self._phase not in PHASE_ORDER:
            return (self._ratio, self._ratio)
        index = PHASE_ORDER.index(self._phase)
        floor = sum(self._weights.get(phase, 0.0) for phase in PHASE_ORDER[:index])
        span = self._weights.get(self._phase, 0.0)
        ceiling = floor + span * (1.0 if self._determinate() else INDETERMINATE_PHASE_CEILING)
        return (floor, ceiling)

    def _compute_ratio(self, floor: float, ceiling: float) -> float:
        if self._finished:
            return 1.0
        if self._determinate():
            within = min(1.0, self._done / float(self._total))  # type: ignore[arg-type]
        else:
            expected = self._expected.get(self._phase)
            if expected and expected > 0:
                elapsed_ms = (self._clock() - self._phase_started) * 1000.0
                within = min(1.0, elapsed_ms / float(expected))
            else:
                within = 0.0
        # The bar must never go backwards: a learned weight can be wrong, and a phase
        # transition that lowered the number would read as a build losing ground.
        return max(self._ratio, min(1.0, floor + (ceiling - floor) * within))

    def _emit(self, *, force: bool = False) -> None:
        if not self._sinks:
            return
        now_ms = self._clock() * 1000.0
        if not force and (now_ms - self._last_emit) < MIN_EMIT_INTERVAL_MS:
            return
        self._last_emit = now_ms
        floor, ceiling = self._bounds()
        self._ratio = self._compute_ratio(floor, ceiling)
        event = ProgressEvent(
            phase=self._phase,
            label=PHASE_LABELS.get(self._phase, self._phase or ""),
            done=self._done,
            total=self._total,
            determinate=self._determinate(),
            ratio=self._ratio,
            ratio_floor=floor,
            ratio_ceiling=ceiling,
            phase_started_at_ms=self._phase_started_wall_ms,
            phase_expected_ms=self._expected.get(self._phase),
            elapsed_ms=(self._clock() - self._started) * 1000.0,
            detail=self._detail,
            stage_ms=(self.stage_ms_snapshot() if self._finished else None),
        )
        for sink in self._sinks:
            try:
                sink(event)
            except Exception:  # noqa: BLE001 - reporting must never fail a build
                continue


class _NullProgressReporter:
    """Accepts every call and does nothing, so build code can report unconditionally."""

    def phase(self, *args: object, **kwargs: object) -> None:
        return None

    def set_total(self, *args: object, **kwargs: object) -> None:
        return None

    def advance(self, *args: object, **kwargs: object) -> None:
        return None

    def finish(self) -> None:
        return None

    def stage_ms_snapshot(self) -> dict[str, float]:
        return {}


NULL_PROGRESS = _NullProgressReporter()


def resolve(progress: object | None) -> object:
    """``progress or NULL_PROGRESS`` -- keeps ``if progress is not None`` out of the
    build path, where the reporting is incidental to what the code is saying."""
    return NULL_PROGRESS if progress is None else progress


class ProgressSidecar:
    """Writes each event to the model's sidecar, atomically.

    Temp file + ``os.replace`` so a poller never reads a half-written payload. Every
    failure is swallowed: an unwritable ``__cadgen__`` must degrade to "no progress
    reported", never to a failed build."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._disabled = False

    def __call__(self, event: ProgressEvent) -> None:
        # Phase transitions and the terminal event always land here; the reporter has
        # already throttled the ticks in between, so every event gets written.
        if self._disabled:
            return
        payload = json.dumps(event.payload(), separators=(",", ":"))
        temp_path = self._path.with_name(f"{self._path.name}.tmp{os.getpid()}")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(payload, encoding="utf-8")
            os.replace(temp_path, self._path)
        except OSError:
            with contextlib.suppress(OSError):
                temp_path.unlink(missing_ok=True)
            self._disabled = True

    def read_stage_ms(self) -> dict[str, float] | None:
        """The measured stage times left behind by this model's last finished build."""
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        stage_ms = payload.get("stageMs")
        if not isinstance(stage_ms, dict):
            return None
        recorded: dict[str, float] = {}
        for phase, value in stage_ms.items():
            try:
                recorded[str(phase)] = float(value)
            except (TypeError, ValueError):
                continue
        return recorded or None


@contextlib.contextmanager
def report_build_progress(
    package_dir: Path | str | None,
    *,
    sink: Callable[[ProgressEvent], None] | None = None,
) -> Iterator[object]:
    """Report one model's build to its sidecar (and optionally an extra sink).

    The sidecar is attached for EVERY build, CLI or viewer-triggered, which is what
    lets an open CAD Viewer show the progress of a ``cad gen`` running in another
    terminal. ``package_dir`` of None (a generator with no package) yields the null
    reporter, so callers need no special case."""
    if package_dir is None:
        yield NULL_PROGRESS
        return
    sidecar = ProgressSidecar(progress_path(package_dir))
    reporter = ProgressReporter(
        sinks=[s for s in (sidecar, sink) if s is not None],
        stage_ms=sidecar.read_stage_ms(),
    )
    try:
        yield reporter
    finally:
        reporter.finish()
