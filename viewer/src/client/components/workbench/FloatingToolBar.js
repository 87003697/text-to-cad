import { useCallback, useEffect, useRef, useState } from "react";
import {
  Crosshair,
  Focus,
  Hand,
  MousePointer2,
  Orbit,
  Pause,
  Play,
  PenTool,
  X
} from "lucide-react";
import { RENDER_FORMAT } from "@/workbench/constants";
import {
  isMeshRenderFormat,
  isRobotRenderFormat
} from "cadjs/lib/fileFormats";
import { TooltipProvider } from "../ui/tooltip";
import DrawingToolbar from "./DrawingToolbar";
import { ToolbarButton } from "./ToolbarButton";
import { CAD_WORKSPACE_TOOLBAR_DESKTOP_WIDTH_CLASS } from "./ToolbarShell";
import { StepExportDropdown } from "./StepExportDropdown";

const FLOATING_TOOL_BAR_SURFACE_CLASS =
  "cad-glass-surface border border-sidebar-border text-sidebar-foreground shadow-sm";
const PREVIEW_TOOLBAR_HIDE_DELAY_MS = 2500;

// In orbit/preview mode the toolbar stays available but auto-hides: it appears
// on any cursor activity and fades out after a short idle delay (and never
// hides while the pointer is over it). Outside preview mode it is always shown.
function usePreviewToolbarVisibility(previewMode) {
  const [visible, setVisible] = useState(true);
  const hideTimerRef = useRef(0);
  const hoveredRef = useRef(false);
  const previewRef = useRef(previewMode);
  previewRef.current = previewMode;

  const scheduleHide = useCallback(() => {
    if (typeof window === "undefined") {
      return;
    }
    window.clearTimeout(hideTimerRef.current);
    if (!previewRef.current || hoveredRef.current) {
      return;
    }
    hideTimerRef.current = window.setTimeout(() => setVisible(false), PREVIEW_TOOLBAR_HIDE_DELAY_MS);
  }, []);

  const reveal = useCallback(() => {
    setVisible(true);
    scheduleHide();
  }, [scheduleHide]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return undefined;
    }
    if (!previewMode) {
      window.clearTimeout(hideTimerRef.current);
      hoveredRef.current = false;
      setVisible(true);
      return undefined;
    }
    reveal();
    const onActivity = () => reveal();
    window.addEventListener("pointermove", onActivity, { passive: true });
    window.addEventListener("pointerdown", onActivity, { passive: true });
    return () => {
      window.clearTimeout(hideTimerRef.current);
      window.removeEventListener("pointermove", onActivity);
      window.removeEventListener("pointerdown", onActivity);
    };
  }, [previewMode, reveal]);

  const onToolbarEnter = useCallback(() => {
    hoveredRef.current = true;
    if (typeof window !== "undefined") {
      window.clearTimeout(hideTimerRef.current);
    }
    setVisible(true);
  }, []);
  const onToolbarLeave = useCallback(() => {
    hoveredRef.current = false;
    scheduleHide();
  }, [scheduleHide]);

  return {
    toolbarHidden: previewMode ? !visible : false,
    onToolbarEnter,
    onToolbarLeave
  };
}
const FLOATING_TOOL_BAR_BUTTON_CLASSES =
  "grid size-6 shrink-0 place-items-center rounded-sm text-sidebar-foreground/70 shadow-none transition hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/45 disabled:pointer-events-none disabled:opacity-50 data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground";

function DesktopFloatingToolBar({
  renderFormat,
  floatingCadToolbarPosition,
  drawingViewToggle = false,
  drawingViewMode = "3d",
  onDrawingViewModeChange,
  previewMode = false,
  toolbarHidden = false,
  onToolbarEnter,
  onToolbarLeave,
  handleExitPreviewMode,
  selectionToolActive,
  referenceSelectionPending = false,
  referenceSelectionUnavailable = false,
  referenceSelectionDeferred = false,
  urdfPosePickerAvailable = false,
  urdfPosePickerActive = false,
  handleToggleUrdfPosePicker,
  stepAnimationAvailable = false,
  stepAnimationPlaying = false,
  stepAnimationDisabled = false,
  handleStepAnimationPlayToggle,
  drawToolActive,
  panToolActive,
  handleSelectTabToolMode,
  displayMode,
  onDisplayModeChange,
  projection,
  onProjectionChange,
  viewerLoading,
  selectedMeshData,
  selectedDxfData,
  selectedImplicitModel,
  drawingToolOptions,
  drawingTool,
  handleSelectDrawingTool,
  handleUndoDrawing,
  handleRedoDrawing,
  handleClearDrawings,
  canUndoDrawing,
  canRedoDrawing,
  drawingStrokes,
  handleEnterPreviewMode,
  handleScreenshotCopy,
  selectedEntry,
  onExportStepFile,
  fileAccessBusyKey = ""
}) {
  const dxfMode = renderFormat === RENDER_FORMAT.DXF;
  const implicitMode = renderFormat === RENDER_FORMAT.IMPLICIT;
  const urdfMode = renderFormat === RENDER_FORMAT.URDF;
  const robotMode = isRobotRenderFormat(renderFormat);
  const meshOnlyMode = isMeshRenderFormat(renderFormat);
  // One question for every format now that DXF and implicit render a baked package GLB.
  const captureDisabled = viewerLoading || !selectedMeshData;
  const selectDisabled = viewerLoading ||
    !selectedMeshData ||
    referenceSelectionPending ||
    referenceSelectionUnavailable ||
    referenceSelectionDeferred;
  const posePickerDisabled = viewerLoading || !selectedMeshData || !urdfPosePickerAvailable;
  const selectLabel = referenceSelectionPending ? "Preparing selection" : "Select";
  const showStepAnimationPlay = renderFormat === RENDER_FORMAT.STEP && stepAnimationAvailable;
  const stepAnimationPlayDisabled = viewerLoading || !selectedMeshData || stepAnimationDisabled;
  const stepAnimationLabel = stepAnimationPlaying ? "Pause" : "Play";

  // Buttons shared between the full toolbar and the reduced orbit-mode toolbar.
  const stepAnimationButton = showStepAnimationPlay ? (
    <ToolbarButton
      label={stepAnimationLabel}
      active={stepAnimationPlaying}
      onClick={handleStepAnimationPlayToggle}
      disabled={stepAnimationPlayDisabled}
      aria-pressed={stepAnimationPlaying}
    >
      {stepAnimationPlaying ? (
        <Pause className="size-3" strokeWidth={2} aria-hidden="true" />
      ) : (
        <Play className="size-3" strokeWidth={2} aria-hidden="true" />
      )}
    </ToolbarButton>
  ) : null;

  const screenshotButton = (
    <ToolbarButton
      label="Copy screenshot"
      onClick={() => {
        void handleScreenshotCopy();
      }}
      disabled={captureDisabled}
    >
      <Focus className="size-3" strokeWidth={2} aria-hidden="true" />
    </ToolbarButton>
  );

  // A drawing's own toolbar, in its own pill to the LEFT of the shared one: 2D and 3D are a
  // property of the drawing being viewed, not a tool that acts on it, so grouping them with
  // select/pan/draw would read as a fourth mode of the same kind.
  const drawingViewToolbar = drawingViewToggle ? (
    <div
      className={`${toolbarHidden ? "pointer-events-none" : "pointer-events-auto"} inline-flex h-8 w-fit items-center gap-0.5 rounded-md p-1 ${FLOATING_TOOL_BAR_SURFACE_CLASS}`}
      onPointerEnter={onToolbarEnter}
      onPointerLeave={onToolbarLeave}
    >
      {/* `active`, not `isActive`: ToolbarButton switches variant on `active`, and an unknown
          prop is silently dropped -- which is why neither button looked selected. */}
      <ToolbarButton
        label="Top-down 2D view"
        active={drawingViewMode === "2d"}
        onClick={() => onDrawingViewModeChange?.("2d")}
      >
        <span className="text-[10px] font-medium leading-none">2D</span>
      </ToolbarButton>
      <ToolbarButton
        label="3D view"
        active={drawingViewMode !== "2d"}
        onClick={() => onDrawingViewModeChange?.("3d")}
      >
        <span className="text-[10px] font-medium leading-none">3D</span>
      </ToolbarButton>
    </div>
  ) : null;

  return (
    <div
      className={`absolute z-20 flex flex-col items-end gap-1 transition-opacity duration-300 ${toolbarHidden ? "opacity-0" : "opacity-100"}`}
      style={floatingCadToolbarPosition}
    >
      <TooltipProvider delayDuration={250}>
        <div className="flex w-fit items-center gap-1 self-end">
        {drawingViewToolbar}
        <div
          className={`${toolbarHidden ? "pointer-events-none" : "pointer-events-auto"} inline-flex h-8 w-fit items-center gap-0.5 self-end rounded-md p-1 ${FLOATING_TOOL_BAR_SURFACE_CLASS}`}
          onPointerEnter={onToolbarEnter}
          onPointerLeave={onToolbarLeave}
        >
          {previewMode ? (
            // Orbit mode: only tools that make sense while orbiting, plus an
            // explicit exit (X). No select/draw/pose/orbit/export here.
            <>
              {stepAnimationButton}
              {screenshotButton}
              <ToolbarButton label="Exit orbit" onClick={handleExitPreviewMode}>
                <X className="size-3" strokeWidth={2} aria-hidden="true" />
              </ToolbarButton>
            </>
          ) : (
            <>
              {!dxfMode && !implicitMode && !robotMode && !meshOnlyMode ? (
                <>
                  <ToolbarButton
                    label={selectLabel}
                    active={referenceSelectionDeferred ? false : selectionToolActive}
                    onClick={() => handleSelectTabToolMode("references")}
                    disabled={selectDisabled}
                    aria-pressed={referenceSelectionDeferred ? false : selectionToolActive}
                  >
                    <MousePointer2 className="size-3" strokeWidth={2} aria-hidden="true" />
                  </ToolbarButton>

                  <ToolbarButton
                    label="Pan"
                    active={panToolActive}
                    onClick={() => handleSelectTabToolMode("pan")}
                    disabled={viewerLoading || !selectedMeshData}
                    aria-pressed={panToolActive}
                  >
                    <Hand className="size-3" strokeWidth={2} aria-hidden="true" />
                  </ToolbarButton>

                  <ToolbarButton
                    label="Draw"
                    active={drawToolActive}
                    onClick={() => handleSelectTabToolMode("draw")}
                    disabled={viewerLoading || !selectedMeshData}
                    aria-pressed={drawToolActive}
                  >
                    <PenTool className="size-3" strokeWidth={2} aria-hidden="true" />
                  </ToolbarButton>

                  {stepAnimationButton}
                </>
              ) : null}

              {!dxfMode && urdfMode ? (
                <ToolbarButton
                  label="Select Pose"
                  active={urdfPosePickerActive}
                  onClick={handleToggleUrdfPosePicker}
                  disabled={posePickerDisabled}
                  aria-pressed={urdfPosePickerActive}
                >
                  <Crosshair className="size-3" strokeWidth={2} aria-hidden="true" />
                </ToolbarButton>
              ) : null}

              {!dxfMode ? (
                <ToolbarButton
                  label="Orbit"
                  onClick={handleEnterPreviewMode}
                  disabled={captureDisabled}
                >
                  <Orbit className="size-3" strokeWidth={2} aria-hidden="true" />
                </ToolbarButton>
              ) : null}

              {screenshotButton}

              <StepExportDropdown
                selectedEntry={selectedEntry}
                onExportStepFile={onExportStepFile}
                fileAccessBusyKey={fileAccessBusyKey}
                triggerClassName={FLOATING_TOOL_BAR_BUTTON_CLASSES}
                iconClassName="size-3"
                contentAlign="end"
                contentSide="bottom"
                contentSideOffset={6}
              />
            </>
          )}
        </div>
        </div>
      </TooltipProvider>

      {!previewMode && !dxfMode && !meshOnlyMode && drawToolActive ? (
        <DrawingToolbar
          className={CAD_WORKSPACE_TOOLBAR_DESKTOP_WIDTH_CLASS}
          drawingToolOptions={drawingToolOptions}
          drawingTool={drawingTool}
          handleSelectDrawingTool={handleSelectDrawingTool}
          handleUndoDrawing={handleUndoDrawing}
          handleRedoDrawing={handleRedoDrawing}
          handleClearDrawings={handleClearDrawings}
          canUndoDrawing={canUndoDrawing}
          canRedoDrawing={canRedoDrawing}
          drawingStrokes={drawingStrokes}
        />
      ) : null}
    </div>
  );
}

export default function FloatingToolBar({
  previewMode,
  selectedEntry,
  ...toolbarProps
}) {
  const { toolbarHidden, onToolbarEnter, onToolbarLeave } = usePreviewToolbarVisibility(previewMode);
  if (!selectedEntry) {
    return null;
  }

  return (
    <DesktopFloatingToolBar
      selectedEntry={selectedEntry}
      previewMode={previewMode}
      toolbarHidden={toolbarHidden}
      onToolbarEnter={onToolbarEnter}
      onToolbarLeave={onToolbarLeave}
      {...toolbarProps}
    />
  );
}
