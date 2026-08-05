import { useEffect, useRef } from "react";

import {
  MEASURE_LINE_DRAFT_ID,
  MEASURE_LINE_RENDER_ORDER,
  clampMeasureChipPosition,
  clearMeasureLineGroup,
  createMeasureLineSegments,
  midpointOfPoints
} from "cadjs/lib/viewer/measureLines.js";
import { formatMeasurement, formatMeasurementDelta } from "cadjs/lib/viewer/measurement.js";
import { projectWorldPointToClient, redrawMeasureRuler } from "cadjs/lib/viewer/measureRuler.js";

import { measureRulerDraftMeasurement } from "../../../workbench/measureRulerState.js";

function buildMeasureChips(state, camera, rect) {
  if (!state || !camera) {
    return [];
  }
  const chips = [];
  const draftMeasurement = measureRulerDraftMeasurement(state);
  if (draftMeasurement && state.draft?.hover) {
    const draftChip = measureChipForSegment({
      id: MEASURE_LINE_DRAFT_ID,
      start: state.draft.anchor.point,
      end: state.draft.hover.point
    }, draftMeasurement, camera, rect);
    if (draftChip) {
      chips.push(draftChip);
    }
  }
  for (const item of state.measurements || []) {
    const chip = measureChipForSegment({
      id: item.id,
      start: item.pickA?.point,
      end: item.pickB?.point
    }, item.measurement, camera, rect);
    if (chip) {
      chips.push(chip);
    }
  }
  return chips;
}

function measureChipForSegment(segment, measurement, camera, rect) {
  const mid = midpointOfPoints(segment.start, segment.end);
  if (!mid) {
    return null;
  }
  const client = projectWorldPointToClient(mid, camera, rect);
  if (!client) {
    return null;
  }
  const clamped = clampMeasureChipPosition(
    {
      x: client.x - (Number(rect?.left) || 0),
      y: client.y - (Number(rect?.top) || 0)
    },
    {
      width: Number(rect?.width) || 1,
      height: Number(rect?.height) || 1
    }
  );
  if (!clamped) {
    return null;
  }
  return {
    id: segment.id,
    x: clamped.x,
    y: clamped.y,
    label: formatMeasurement(measurement),
    detail: formatMeasurementDelta(measurement),
    deletable: segment.id !== MEASURE_LINE_DRAFT_ID
  };
}

export function useViewerMeasureOverlay({
  measureCanvasRef,
  measureState,
  onMeasureChipsChange,
  runtimeRef,
  mountRef,
  previewMode,
  viewerReadyTick
}) {
  const measureStateRef = useRef(measureState);
  measureStateRef.current = measureState;
  const chipsChangeRef = useRef(onMeasureChipsChange);
  chipsChangeRef.current = onMeasureChipsChange;
  const chipsSignatureRef = useRef("");

  useEffect(() => {
    const canvas = measureCanvasRef.current;
    if (!canvas) {
      return undefined;
    }

    let rafId = 0;

    const emitChips = (chips) => {
      const signature = JSON.stringify(chips);
      if (signature === chipsSignatureRef.current) {
        return;
      }
      chipsSignatureRef.current = signature;
      chipsChangeRef.current?.(chips);
    };

    const render = (now) => {
      const state = measureStateRef.current;
      if (!state || previewMode) {
        emitChips([]);
        return;
      }
      const runtime = runtimeRef.current;
      const rendererCanvas = runtime?.renderer?.domElement;
      const width = rendererCanvas?.width || mountRef.current?.clientWidth || 1;
      const height = rendererCanvas?.height || mountRef.current?.clientHeight || 1;
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
      const localRect = { left: 0, top: 0, width: canvas.width, height: canvas.height };
      redrawMeasureRuler(canvas, {
        anchorPoint: state.draft?.anchor?.point || null,
        endPoint: state.draft?.hover?.point || null,
        camera: runtime?.camera || null,
        rect: localRect,
        pulse: !!state.draft,
        now
      });
      const mountRect = mountRef.current?.getBoundingClientRect?.() || null;
      emitChips(buildMeasureChips(state, runtime?.camera || null, mountRect));
      rafId = window.requestAnimationFrame(render);
    };

    if (measureStateRef.current && !previewMode) {
      rafId = window.requestAnimationFrame(render);
    }

    return () => {
      window.cancelAnimationFrame(rafId);
    };
  }, [measureCanvasRef, measureState, mountRef, previewMode, runtimeRef, viewerReadyTick]);

  useEffect(() => {
    const canvas = measureCanvasRef.current;
    if (!canvas) {
      return undefined;
    }
    if (measureState?.draft && !previewMode) {
      return undefined;
    }
    const context = canvas.getContext("2d");
    if (context) {
      context.clearRect(0, 0, canvas.width || 1, canvas.height || 1);
    }
    return undefined;
  }, [measureCanvasRef, measureState, previewMode, viewerReadyTick]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime?.THREE || !runtime?.edgesGroup) {
      return undefined;
    }
    const { THREE, edgesGroup } = runtime;
    if (!runtime.measureLineGroup || runtime.measureLineGroup.parent !== edgesGroup) {
      runtime.measureLineGroup = new THREE.Group();
      runtime.measureLineGroup.renderOrder = MEASURE_LINE_RENDER_ORDER;
      edgesGroup.add(runtime.measureLineGroup);
    }
    const group = runtime.measureLineGroup;
    clearMeasureLineGroup(runtime, group);

    if (!previewMode && measureState) {
      const { committed, draft } = createMeasureLineSegments(runtime, measureState);
      if (committed) {
        group.add(committed);
      }
      if (draft) {
        group.add(draft);
      }
    }
    group.visible = group.children.length > 0;
    runtime.requestRender?.();

    return () => {
      clearMeasureLineGroup(runtime, group);
      runtime.requestRender?.();
    };
  }, [measureState, previewMode, runtimeRef, viewerReadyTick]);
}
