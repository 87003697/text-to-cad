import { VIEWER_PICK_MODE } from "cadjs/lib/viewer/constants.js";

export function viewerPickModeForRenderPane({
  dxfMode = false,
  meshOnlyMode = false,
  panToolActive = false,
  topologySelectionPending = false,
  topologySelectionUnavailable = false,
  topologySelectionDeferred = false,
  topologyPickingActive = false,
  viewerMode = "",
  assemblyPickingActive = false,
  focusedPartIds = ""
} = {}) {
  // While panning, a drag is a camera move — picking on release would select
  // whatever the drag happened to finish over.
  if (panToolActive) {
    return VIEWER_PICK_MODE.NONE;
  }
  if (topologySelectionPending || topologySelectionUnavailable || topologySelectionDeferred) {
    return VIEWER_PICK_MODE.NONE;
  }
  if (dxfMode || meshOnlyMode) {
    return VIEWER_PICK_MODE.NONE;
  }
  if (
    viewerMode === "assembly" &&
    !topologyPickingActive &&
    (
      assemblyPickingActive ||
      !String(focusedPartIds || "").trim()
    )
  ) {
    return VIEWER_PICK_MODE.ASSEMBLY;
  }
  return VIEWER_PICK_MODE.AUTO;
}
