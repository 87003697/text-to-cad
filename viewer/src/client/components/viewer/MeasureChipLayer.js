import { X } from "lucide-react";

export default function MeasureChipLayer({ chips = [], onDelete }) {
  if (!chips.length) {
    return null;
  }
  return (
    <div className="pointer-events-none absolute inset-0 z-20 overflow-hidden">
      {chips.map((chip) => (
        <div
          key={chip.id}
          className="pointer-events-auto absolute flex -translate-x-1/2 -translate-y-1/2 flex-col items-stretch rounded-lg border border-[#3b82f6]/70 bg-[#020617]/85 px-2 py-1 text-white shadow-md"
          style={{ left: chip.x, top: chip.y }}
          onPointerDown={(event) => event.stopPropagation()}
        >
          <div className="flex items-center gap-1.5 whitespace-nowrap">
            <span className="text-xs font-medium">{chip.label}</span>
            {chip.deletable ? (
              <button
                type="button"
                aria-label={`Delete measurement ${chip.label}`}
                className="grid size-4 shrink-0 place-items-center rounded-sm text-white/70 transition hover:bg-white/10 hover:text-white"
                onClick={(event) => {
                  event.stopPropagation();
                  onDelete?.(chip.id);
                }}
              >
                <X className="size-3" strokeWidth={2.5} aria-hidden="true" />
              </button>
            ) : null}
          </div>
          {chip.detail ? (
            <div className="whitespace-nowrap text-[10px] leading-tight text-white/75">{chip.detail}</div>
          ) : null}
        </div>
      ))}
    </div>
  );
}
