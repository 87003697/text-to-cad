import { Download } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger
} from "@/components/ui/dropdown-menu";
import {
  STEP_EXPORT_FORMATS,
  isImportedStepEntry,
  stepExportItemLabel
} from "@/workbench/stepExport";

// Dedicated "download" icon dropdown for exporting the open STEP/assembly model to
// STEP/3MF/STL/GLB. Hidden unless a STEP/assembly entry is selected and export is wired.
// Lives in the viewer floating toolbar (styled via triggerClassName, like DisplayProjectionControl).
export function StepExportDropdown({
  selectedEntry,
  fileAccessBusyKey = "",
  onExportStepFile,
  triggerClassName = "",
  iconClassName = "size-3",
  contentAlign = "end",
  contentSide = "bottom",
  contentSideOffset = 6
}) {
  const kind = String(selectedEntry?.kind || "").trim().toLowerCase();
  const isStepEntry = kind === "step" || kind === "assembly";
  if (!selectedEntry || !isStepEntry || typeof onExportStepFile !== "function") {
    return null;
  }
  const fileRef = String(selectedEntry?.file || selectedEntry?.id || "").trim();
  const imported = isImportedStepEntry(selectedEntry);
  const label = "Export model";
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={label}
          title={label}
          className={triggerClassName}
          onPointerDown={(event) => {
            event.stopPropagation();
          }}
        >
          <Download className={iconClassName} strokeWidth={2} aria-hidden="true" />
          <span className="sr-only">{label}</span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align={contentAlign}
        side={contentSide}
        sideOffset={contentSideOffset}
        className="w-max"
      >
        {STEP_EXPORT_FORMATS.map((format) => {
          const key = `${fileRef}:export:${format}`;
          return (
            <DropdownMenuItem
              key={format}
              className="text-xs"
              disabled={fileAccessBusyKey === key}
              onSelect={() => {
                onExportStepFile(selectedEntry, format);
              }}
            >
              <span className="min-w-0 truncate">{stepExportItemLabel(format, { imported })}</span>
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
