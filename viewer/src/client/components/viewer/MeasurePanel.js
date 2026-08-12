import { X } from "lucide-react";

import {
  MEASURE_SNAP_LABELS,
  formatMeasurementAngle,
  formatMeasurementDelta
} from "cadjs/lib/viewer/measurement.js";
import { measureLabelText } from "cadjs/lib/viewer/measureDimension.js";

import { MEASURE_RULER_MAX_MEASUREMENTS } from "../../workbench/measureRulerState.js";
import { cn } from "@/ui/utils";

// What each endpoint snapped to, so an exact edge or face reading is visibly
// different from a free point taken off the tessellated surface.
function snapPairLabel(measurement) {
  const first = MEASURE_SNAP_LABELS[measurement?.pickA?.snapKind];
  const second = MEASURE_SNAP_LABELS[measurement?.pickB?.snapKind];
  if (!first || !second) {
    return "";
  }
  return `${first} → ${second}`;
}

export default function MeasurePanel({
  measurements = [],
  activeId = "",
  onActivate = null,
  onDelete = null,
  onClear = null,
  draftEntity = null,
  draftEntityText = "",
  draftInProgress = false,
  measureModeActive = false
}) {
  const emptyText = draftInProgress
    ? "Click a second point to measure"
    : (measureModeActive ? "Click two points on model to measure" : "No measurements");
  return (
    <div className="pointer-events-auto inline-flex w-60 max-w-[calc(100vw-2rem)] flex-col overflow-hidden rounded-md border border-sidebar-border cad-glass-surface text-sidebar-foreground shadow-sm">
      <div className="flex items-center justify-between gap-1.5 px-2.5 pt-1.5 pb-1 text-[10px] font-medium text-sidebar-foreground/60">
        <span>Measurements</span>
        <span className="flex items-center gap-1.5">
          {measurements.length ? (
            <button
              type="button"
              className="rounded-sm px-1 text-[10px] text-sidebar-foreground/50 transition hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              onClick={() => onClear?.()}
            >
              Clear
            </button>
          ) : null}
          <span className="tabular-nums">
            {measurements.length}/{MEASURE_RULER_MAX_MEASUREMENTS}
          </span>
        </span>
      </div>

      {/* One entity under the cursor reads out on its own, before any second
          pick — the half of a measure tool that answers "how big is this". */}
      {draftEntityText ? (
        <div className="border-t border-sidebar-border/60 px-2.5 py-1 text-[11px] leading-snug">
          <span className="mr-1.5 text-[10px] uppercase tracking-wide text-sidebar-foreground/50">
            {draftEntity?.typeLabel || "entity"}
          </span>
          <span className="tabular-nums text-sidebar-foreground/90">{draftEntityText}</span>
        </div>
      ) : null}

      <div className="max-h-40 overflow-y-auto px-1 pb-1">
        {measurements.length === 0 ? (
          <div className="px-1.5 py-1 text-[11px] leading-snug text-sidebar-foreground/60">
            {emptyText}
          </div>
        ) : (
          measurements.map((item, index) => {
            const active = item.id === activeId;
            const labelText = measureLabelText(item.measurement);
            const angleText = formatMeasurementAngle(item.measurement);
            const deltaText = formatMeasurementDelta(item.measurement);
            const snapText = snapPairLabel(item);
            const title = [labelText, angleText, deltaText, snapText].filter(Boolean).join("  ·  ");
            return (
              <div
                key={item.id}
                role="button"
                tabIndex={0}
                title={title}
                onClick={() => onActivate?.(item.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onActivate?.(item.id);
                  }
                }}
                className={cn(
                  "flex cursor-pointer items-center justify-between gap-1.5 rounded-sm px-1.5 py-1 text-[11px] transition",
                  active
                    ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                    : "text-sidebar-foreground/80 hover:bg-sidebar-accent/50 hover:text-sidebar-accent-foreground"
                )}
              >
                <span className="min-w-0 truncate tabular-nums">
                  <span className="mr-1 text-[10px] text-sidebar-foreground/50">#{index + 1}</span>
                  {labelText}
                  {angleText ? <span className="ml-1.5">{angleText}</span> : null}
                  {snapText ? (
                    <span className="ml-1.5 text-[10px] font-normal text-sidebar-foreground/45">{snapText}</span>
                  ) : null}
                </span>
                <button
                  type="button"
                  aria-label={`Delete measurement ${index + 1}`}
                  className="grid size-4 shrink-0 place-items-center rounded-sm text-sidebar-foreground/50 transition hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
                  onClick={(event) => {
                    event.stopPropagation();
                    onDelete?.(item.id);
                  }}
                >
                  <X className="size-3" strokeWidth={2} aria-hidden="true" />
                </button>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
