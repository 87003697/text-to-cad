import { Download } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger
} from "@/components/ui/dropdown-menu";
import {
  DXF_EXPORT_FORMATS,
  STEP_EXPORT_FORMATS,
  exportItemLabel
} from "@/workbench/modelExport";

// Dedicated "download" icon dropdown for exporting the open STEP model (part, assembly, or
// imported STEP) to STEP/3MF/STL/GLB, or a generated DXF drawing to DXF. Hidden unless an
// export-capable entry is selected and export is wired.
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
  const isStepEntry = kind === "step" || kind === "assembly" || kind === "part";
  // Only generated drawings export (a raw imported .dxf is already the deliverable).
  const isGeneratedDxfEntry = kind === "dxf" && selectedEntry?.sourceKind === "python";
  if (!selectedEntry || (!isStepEntry && !isGeneratedDxfEntry) || typeof onExportStepFile !== "function") {
    return null;
  }
  const exportFormats = isGeneratedDxfEntry ? DXF_EXPORT_FORMATS : STEP_EXPORT_FORMATS;
  const fileRef = String(selectedEntry?.file || selectedEntry?.id || "").trim();
  const label = isGeneratedDxfEntry ? "Export drawing" : "Export model";
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
        {exportFormats.map((format) => {
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
              <span className="min-w-0 truncate">{exportItemLabel(format)}</span>
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
