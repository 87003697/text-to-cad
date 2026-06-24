import { useEffect, useRef, useState } from "react";
import { Check, ChevronLeft, ChevronRight, Copy, SquareMousePointer } from "lucide-react";
import { cn } from "@/ui/utils";
import { copyTextToClipboard } from "@/ui/clipboard";
import { FILE_SHEET_SECTION_IDS } from "@/workbench/fileSheetSections";
import { Button } from "../ui/button";

// A reference is a selected topology entity (face / edge / solid) or assembly
// occurrence. All the data below comes straight off the resolved reference
// object (reference + reference.pickData) — no extra geometry work — mirroring
// the per-selection readouts in Onshape / Fusion / SolidWorks: lead with the
// type + identity, then the signature measurement, then size and coordinates.

const SELECTOR_TYPE_LABELS = Object.freeze({
  face: "Face",
  edge: "Edge",
  shape: "Solid",
  occurrence: "Component"
});

const SURFACE_LABELS = Object.freeze({
  plane: "Planar",
  cylinder: "Cylindrical",
  cone: "Conical",
  sphere: "Spherical",
  torus: "Toroidal",
  spline: "Freeform",
  bspline: "Freeform",
  nurbs: "Freeform"
});

const CURVE_LABELS = Object.freeze({
  line: "Line",
  circle: "Circle",
  arc: "Arc",
  ellipse: "Ellipse",
  spline: "Spline",
  bspline: "Spline"
});

// Onshape-style axis colour coding for coordinate triples.
const AXES = Object.freeze([
  { key: "X", className: "text-rose-500 dark:text-rose-400" },
  { key: "Y", className: "text-emerald-500 dark:text-emerald-400" },
  { key: "Z", className: "text-sky-500 dark:text-sky-400" }
]);

const SUMMARY_PATTERN = /^(.*?)\s+(area|length|volume)\s*=\s*([-\d.eE+]+)\s*$/;

function titleCase(value) {
  const text = String(value || "").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
}

function formatNumber(value, digits = 2) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "—";
  }
  return numeric.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function parseSummary(summary) {
  const text = String(summary || "").trim();
  const match = text.match(SUMMARY_PATTERN);
  if (match) {
    return { subtype: match[1].trim(), measureKey: match[2], measureValue: Number(match[3]) };
  }
  return { subtype: text, measureKey: null, measureValue: null };
}

function bboxDimensions(bbox) {
  const min = Array.isArray(bbox?.min) ? bbox.min : null;
  const max = Array.isArray(bbox?.max) ? bbox.max : null;
  if (!min || !max) {
    return null;
  }
  const dims = [0, 1, 2].map((axis) => Math.abs((Number(max[axis]) || 0) - (Number(min[axis]) || 0)));
  return dims.some((value) => value > 1e-9) ? dims : null;
}

function radiusFromParams(params) {
  if (!params || typeof params !== "object") {
    return null;
  }
  const candidate = params.radius ?? params.radius1 ?? params.majorRadius ?? params.minorRadius;
  const numeric = Number(candidate);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : null;
}

function describeReference(reference) {
  const pick = reference?.pickData || {};
  const type = reference?.selectorType;
  const parsed = parseSummary(reference?.summary);
  let subtype = "";
  if (type === "face") {
    subtype = SURFACE_LABELS[pick.surfaceType] || titleCase(pick.surfaceType || parsed.subtype);
  } else if (type === "edge") {
    subtype = CURVE_LABELS[parsed.subtype] || titleCase(parsed.subtype);
  } else {
    subtype = titleCase(pick.kind || parsed.subtype);
  }
  return {
    typeLabel: SELECTOR_TYPE_LABELS[type] || "Reference",
    subtype,
    parsed,
    pick,
    selector: String(reference?.displaySelector || reference?.normalizedSelector || "").trim(),
    component: String(pick.sourceName || pick.name || reference?.occurrenceId || "").trim()
  };
}

function MeasureRow({ label, mono = true, children }) {
  return (
    <div className="flex min-h-6 items-baseline justify-between gap-3 px-2 py-1">
      <span className="shrink-0 text-[11px] text-muted-foreground">{label}</span>
      <span
        className={cn(
          "min-w-0 truncate text-right text-[11px] text-sidebar-foreground",
          mono && "font-mono tabular-nums"
        )}
      >
        {children}
      </span>
    </div>
  );
}

function CoordRow({ label, vector, digits = 2 }) {
  const values = Array.isArray(vector) ? vector : [];
  return (
    <div className="px-2 py-1">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="mt-0.5 flex items-baseline gap-3 font-mono text-[11px] tabular-nums">
        {AXES.map((axis, index) => (
          <span key={axis.key} className="inline-flex items-baseline gap-1">
            <span className={cn("text-[9px] font-semibold", axis.className)}>{axis.key}</span>
            <span className="text-sidebar-foreground">{formatNumber(values[index], digits)}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function ReferenceCopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef(null);
  useEffect(() => () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
    }
  }, []);
  if (!text) {
    return null;
  }
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-xs"
      className="size-5 text-muted-foreground hover:text-foreground"
      aria-label="Copy reference"
      title="Copy reference"
      onClick={() => {
        copyTextToClipboard(text);
        setCopied(true);
        if (timerRef.current) {
          clearTimeout(timerRef.current);
        }
        timerRef.current = setTimeout(() => setCopied(false), 1200);
      }}
    >
      {copied ? (
        <Check className="size-3 text-emerald-500" strokeWidth={2.5} aria-hidden="true" />
      ) : (
        <Copy className="size-3" strokeWidth={2} aria-hidden="true" />
      )}
    </Button>
  );
}

function ReferenceDetail({ reference }) {
  const { typeLabel, subtype, parsed, pick, selector, component } = describeReference(reference);
  const dims = bboxDimensions(pick.bbox);
  const radius = radiusFromParams(pick.params);

  const measureLabel = parsed.measureKey === "area"
    ? "Area"
    : parsed.measureKey === "length"
      ? "Length"
      : parsed.measureKey === "volume"
        ? "Volume"
        : null;
  const measureUnit = parsed.measureKey === "area"
    ? "mm²"
    : parsed.measureKey === "volume"
      ? "mm³"
      : "mm";
  const hasMeasure = measureLabel && Number.isFinite(parsed.measureValue);

  return (
    <div className="flex min-w-0 flex-col">
      <div className="flex items-center gap-2 px-2 pb-1.5 pt-1">
        <span className="inline-flex shrink-0 items-center rounded-sm bg-accent px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-accent-foreground">
          {typeLabel}
        </span>
        {subtype ? <span className="truncate text-[11px] text-muted-foreground">{subtype}</span> : null}
        <span className="ml-auto inline-flex shrink-0 items-center gap-1">
          {selector ? (
            <code className="rounded-sm bg-muted px-1 py-0.5 font-mono text-[10px] text-muted-foreground">
              {selector}
            </code>
          ) : null}
          <ReferenceCopyButton text={reference?.copyText} />
        </span>
      </div>
      <div className="mx-2 h-px bg-sidebar-border/60" />
      <div className="flex flex-col py-0.5">
        {hasMeasure ? (
          <MeasureRow label={measureLabel}>{`${formatNumber(parsed.measureValue)} ${measureUnit}`}</MeasureRow>
        ) : null}
        {radius != null ? <MeasureRow label="Radius">{`${formatNumber(radius)} mm`}</MeasureRow> : null}
        {dims ? (
          <MeasureRow label="Size">
            {`${formatNumber(dims[0])} × ${formatNumber(dims[1])} × ${formatNumber(dims[2])} mm`}
          </MeasureRow>
        ) : null}
        {Array.isArray(pick.center) ? <CoordRow label="Center" vector={pick.center} /> : null}
        {Array.isArray(pick.normal) ? <CoordRow label="Normal" vector={pick.normal} digits={3} /> : null}
        {component ? (
          <MeasureRow label="Component" mono={false}>{component}</MeasureRow>
        ) : null}
      </div>
    </div>
  );
}

export function StepReferenceSection({ references = [] }) {
  const refs = Array.isArray(references) ? references.filter(Boolean) : [];
  const count = refs.length;
  const idsKey = refs.map((reference) => reference?.id || "").join("|");
  const [index, setIndex] = useState(0);

  // When the selection set changes, jump to the most recently added reference.
  useEffect(() => {
    setIndex(count > 0 ? count - 1 : 0);
  }, [idsKey, count]);

  if (!count) {
    return (
      <div className="flex min-h-[7.5rem] flex-col items-center justify-center gap-2 px-6 py-8 text-center">
        <SquareMousePointer className="size-5 text-muted-foreground/45" strokeWidth={1.5} aria-hidden="true" />
        <p className="max-w-[14rem] text-[11px] leading-snug text-muted-foreground">
          Select a face or edge in the model to inspect its measurements.
        </p>
      </div>
    );
  }

  const safeIndex = Math.min(Math.max(index, 0), count - 1);
  const active = refs[safeIndex] || null;

  return (
    <div className="flex min-w-0 flex-col pb-2">
      {count > 1 ? (
        <div className="flex items-center justify-between gap-2 border-b border-sidebar-border/60 px-2 py-1">
          <span className="text-[11px] text-muted-foreground">{count} selected</span>
          <div className="inline-flex items-center gap-0.5">
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              className="size-5 text-muted-foreground hover:text-foreground"
              aria-label="Previous reference"
              title="Previous reference"
              onClick={() => setIndex((current) => (current - 1 + count) % count)}
            >
              <ChevronLeft className="size-3.5" strokeWidth={2} aria-hidden="true" />
            </Button>
            <span className="min-w-[2.75rem] text-center font-mono text-[11px] tabular-nums text-sidebar-foreground">
              {safeIndex + 1} / {count}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              className="size-5 text-muted-foreground hover:text-foreground"
              aria-label="Next reference"
              title="Next reference"
              onClick={() => setIndex((current) => (current + 1) % count)}
            >
              <ChevronRight className="size-3.5" strokeWidth={2} aria-hidden="true" />
            </Button>
          </div>
        </div>
      ) : null}
      <ReferenceDetail reference={active} />
    </div>
  );
}

export function buildStepReferenceTab({ references = [] } = {}) {
  const count = Array.isArray(references) ? references.filter(Boolean).length : 0;
  return {
    id: FILE_SHEET_SECTION_IDS.STEP_REFERENCE,
    title: (
      <span className="flex min-w-0 items-center gap-1.5">
        <span>Reference</span>
        {count > 1 ? (
          <span className="rounded-full bg-accent px-1.5 text-[10px] font-medium tabular-nums text-accent-foreground">
            {count}
          </span>
        ) : null}
      </span>
    ),
    content: <StepReferenceSection references={references} />
  };
}
