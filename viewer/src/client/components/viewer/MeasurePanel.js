import { X } from "lucide-react";

import { formatMeasurementDelta } from "cadjs/lib/viewer/measurement.js";
import { measureLabelText } from "cadjs/lib/viewer/measureDimension.js";

import { MEASURE_RULER_MAX_MEASUREMENTS } from "../../workbench/measureRulerState.js";
import { cn } from "@/ui/utils";

export default function MeasurePanel({
  measurements = [],
  activeId = "",
  onActivate = null,
  onDelete = null,
  measureModeActive = false,
  visible = false
}) {
  if (!visible) {
    return null;
  }
  return (
    <div className="pointer-events-auto absolute left-3 top-3 z-20 w-60 overflow-hidden rounded-lg border border-cyan-400/30 bg-slate-950/90 text-white shadow-lg backdrop-blur-sm">
      <div className="flex items-center justify-between border-b border-white/10 px-3 py-1.5 text-[11px]">
        <span className="font-semibold text-cyan-300">Measurements</span>
        <span className="tabular-nums text-white/50">
          {measurements.length}/{MEASURE_RULER_MAX_MEASUREMENTS}
        </span>
      </div>
      <div className="max-h-52 overflow-y-auto py-1">
        {measurements.length === 0 ? (
          <div className="px-3 py-2 text-[11px] leading-relaxed text-white/50">
            {measureModeActive ? "Click two points on the model to measure" : "No measurements yet"}
          </div>
        ) : (
          measurements.map((item, index) => {
            const active = item.id === activeId;
            return (
              <div
                key={item.id}
                role="button"
                tabIndex={0}
                onClick={() => onActivate?.(item.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onActivate?.(item.id);
                  }
                }}
                className={cn(
                  "mx-1 flex cursor-pointer items-start justify-between gap-2 rounded-md px-2 py-1.5 transition",
                  active
                    ? "border-l-2 border-cyan-300 bg-cyan-400/10"
                    : "border-l-2 border-transparent hover:bg-white/5"
                )}
              >
                <div className="min-w-0">
                  <div className="text-xs font-semibold tabular-nums">{measureLabelText(item.measurement)}</div>
                  <div className="truncate text-[10px] leading-tight text-white/55">
                    {formatMeasurementDelta(item.measurement)}
                  </div>
                </div>
                <button
                  type="button"
                  aria-label={`Delete measurement ${index + 1}`}
                  className="mt-0.5 grid size-4 shrink-0 place-items-center rounded-sm text-white/60 transition hover:bg-white/10 hover:text-white"
                  onClick={(event) => {
                    event.stopPropagation();
                    onDelete?.(item.id);
                  }}
                >
                  <X className="size-3" strokeWidth={2.5} aria-hidden="true" />
                </button>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
