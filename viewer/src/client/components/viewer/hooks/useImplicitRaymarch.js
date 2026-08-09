import { useEffect, useMemo, useRef } from "react";
import {
  createImplicitCadFullscreenScene,
  estimateImplicitCadFrameBoundsAsync,
  implicitCadModelShaderKey,
  refreshImplicitCadFloorBounds,
  updateImplicitCadAppearanceUniforms,
  updateImplicitCadGraphicsUniforms,
  updateImplicitCadMaterialUniforms,
  updateImplicitCadModelUniforms
} from "cadjs/implicit/render";
import {
  implicitGraphicsRenderResolutionScale,
  implicitGraphicsRenderSettings,
  normalizeImplicitGraphicsSettings
} from "@/workbench/implicitGraphicsSettings";

// Hosts the implicit raymarch pass inside the SHARED viewer runtime instead of a
// second renderer. The pass is a fullscreen quad whose vertex shader already
// emits clip space, so it covers the viewport under any camera; the fragment
// shader builds its rays purely from camera uniforms. That means the shared
// OrbitControls, zoom defaults, fit/reset logic and view cube drive it with no
// per-format camera code — the whole point of making implicit a render type
// rather than a parallel viewer.
//
// The quad paints its own background and stage-floor shadow (deliberately
// matched to the mesh stage), so CadViewer suppresses the three.js stage while
// this pass is active rather than trying to blend the two.

function boundsFromModel(model) {
  const min = model?.bounds?.min;
  const max = model?.bounds?.max;
  if (!Array.isArray(min) || !Array.isArray(max) || min.length < 3 || max.length < 3) {
    return null;
  }
  return { min: [...min], max: [...max] };
}

// Quantised so a re-fit only happens when the envelope actually moves. Animation
// advances uniforms every frame without changing the envelope, and re-fitting per
// frame would fight the user's camera.
function boundsKey(model) {
  const bounds = boundsFromModel(model);
  if (!bounds) {
    return "";
  }
  const radius = Math.max(Number(model?.radius) || 1, 1e-6);
  const quantum = Math.max(radius * 0.02, 1e-6);
  return [...bounds.min, ...bounds.max]
    .map((value) => Math.round((Number(value) || 0) / quantum))
    .join(",");
}

export function useImplicitRaymarch({
  runtimeRef,
  viewerReadyTick = 0,
  enabled = false,
  model = null,
  themeSettings = null,
  graphicsSettings = null,
  dynamicRenderActive = false,
  previewMode = false,
  onModelBounds,
  onShaderError
}) {
  const normalizedGraphicsSettings = useMemo(
    () => normalizeImplicitGraphicsSettings(graphicsSettings),
    [graphicsSettings]
  );
  const graphicsSettingsRef = useRef(normalizedGraphicsSettings);
  graphicsSettingsRef.current = normalizedGraphicsSettings;
  const interactionQualityRef = useRef(false);
  interactionQualityRef.current = dynamicRenderActive === true || previewMode === true;
  const dynamicRenderActiveRef = useRef(dynamicRenderActive === true);
  dynamicRenderActiveRef.current = dynamicRenderActive === true;
  const modelBoundsRef = useRef(onModelBounds);
  modelBoundsRef.current = onModelBounds;
  const shaderErrorRef = useRef(onShaderError);
  shaderErrorRef.current = onShaderError;
  const passRef = useRef(null);

  const applyUniforms = (runtime, activeModel) => {
    const material = passRef.current?.material;
    if (!material || !runtime?.THREE || !activeModel) {
      return;
    }
    const interaction = interactionQualityRef.current;
    updateImplicitCadAppearanceUniforms(runtime.THREE, material, activeModel, {
      themeSettings,
      graphicsSettings: implicitGraphicsRenderSettings(graphicsSettingsRef.current, { interaction })
    });
    updateImplicitCadGraphicsUniforms(
      material,
      activeModel,
      implicitGraphicsRenderSettings(graphicsSettingsRef.current, { interaction })
    );
  };

  // Resolution cap: the raymarcher is fill-rate bound, so it renders at a lower
  // pixel ratio while the camera or a parameter is moving and restores on idle.
  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) {
      return undefined;
    }
    if (!enabled) {
      if (runtime.resolveExtraPixelRatioCap) {
        runtime.resolveExtraPixelRatioCap = null;
        runtime.refreshRenderQuality?.();
      }
      return undefined;
    }
    runtime.resolveExtraPixelRatioCap = (interaction) => implicitGraphicsRenderResolutionScale(
      graphicsSettingsRef.current,
      { interaction: interaction === true || interactionQualityRef.current }
    );
    runtime.refreshRenderQuality?.();
    return () => {
      const activeRuntime = runtimeRef.current;
      if (activeRuntime?.resolveExtraPixelRatioCap) {
        activeRuntime.resolveExtraPixelRatioCap = null;
        activeRuntime.refreshRenderQuality?.();
      }
    };
  }, [enabled, normalizedGraphicsSettings, runtimeRef, viewerReadyTick]);

  // Dynamic render (slider drag, animation playback) swaps in the interaction
  // quality tier for as long as it is active.
  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!enabled || !runtime) {
      return;
    }
    runtime.refreshRenderQuality?.();
    applyUniforms(runtime, model);
    runtime.requestRender?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dynamicRenderActive, previewMode, enabled, viewerReadyTick]);

  // Build or update the pass. A model whose GLSL is unchanged (a parameter or
  // animation frame) only updates uniforms; rebuilding the material there would
  // recompile the shader every frame.
  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!enabled || !runtime?.THREE || !runtime?.scene || !model) {
      return;
    }

    const { THREE } = runtime;
    let nextShaderKey = "";
    try {
      nextShaderKey = implicitCadModelShaderKey(model);
    } catch (error) {
      shaderErrorRef.current?.(error instanceof Error ? error.message : String(error));
      return;
    }

    const existing = passRef.current;
    if (existing && existing.shaderKey === nextShaderKey) {
      updateImplicitCadModelUniforms(THREE, existing.material, model);
      applyUniforms(runtime, model);
      runtime.requestRender?.();
      return;
    }

    let nextPass = null;
    try {
      nextPass = createImplicitCadFullscreenScene(THREE, model);
    } catch (error) {
      shaderErrorRef.current?.(error instanceof Error ? error.message : String(error));
      return;
    }

    if (existing) {
      existing.quad.removeFromParent?.();
      existing.dispose?.();
    }

    const { quad, material } = nextPass;
    quad.frustumCulled = false;
    // Nothing else is in the scene for an implicit, but keep the pass from
    // participating in depth so a future overlay composites predictably.
    material.depthTest = false;
    material.depthWrite = false;
    quad.renderOrder = -1000;
    // Feed the SHARED camera to the shader every frame. This single line is what
    // replaces the deleted viewer's entire camera stack.
    quad.onBeforeRender = (renderer, _scene, camera) => {
      const size = renderer.getDrawingBufferSize(new THREE.Vector2());
      updateImplicitCadMaterialUniforms(material, camera, size.x, size.y);
    };
    // Hide until the program links: the shared loop would otherwise compile it
    // synchronously on the next frame and freeze the tab on a heavy model.
    quad.visible = false;
    runtime.scene.add(quad);
    passRef.current = nextPass;
    shaderErrorRef.current?.(null);
    applyUniforms(runtime, model);

    const reveal = () => {
      if (passRef.current === nextPass) {
        quad.visible = true;
        runtime.requestRender?.();
      }
    };
    if (typeof runtime.renderer?.compileAsync === "function") {
      runtime.renderer.compileAsync(runtime.scene, runtime.camera)
        .catch(() => {})
        .finally(reveal);
    } else {
      reveal();
    }
    runtime.requestRender?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, model, runtimeRef, themeSettings, viewerReadyTick]);

  // Theme and graphics settings are uniform-only updates.
  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!enabled || !runtime) {
      return;
    }
    applyUniforms(runtime, model);
    runtime.requestRender?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, model, normalizedGraphicsSettings, themeSettings, viewerReadyTick]);

  // Publish the model envelope for the shared fit. The declared bounds land
  // immediately so the first frame is framed; the CPU SDF scan then reports a
  // tighter envelope and the caller re-fits once. Never runs during dynamic
  // render: an animation would otherwise spawn a scan per frame.
  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!enabled || !runtime || !model) {
      return;
    }
    const declared = boundsFromModel(model);
    if (declared) {
      modelBoundsRef.current?.(declared, { refined: false });
    }
    if (dynamicRenderActiveRef.current) {
      return;
    }
    const key = boundsKey(model);
    if (key && runtime.implicitRefinedBoundsKey === key) {
      return;
    }
    runtime.implicitRefinedBoundsKey = key;
    const token = (runtime.implicitRefineToken = (runtime.implicitRefineToken || 0) + 1);
    const pass = passRef.current;
    let cancelled = false;

    Promise.resolve()
      .then(async () => {
        if (pass?.material) {
          await refreshImplicitCadFloorBounds(pass.material, model);
        }
        return estimateImplicitCadFrameBoundsAsync(model);
      })
      .then((refined) => {
        const activeRuntime = runtimeRef.current;
        if (
          cancelled ||
          activeRuntime !== runtime ||
          runtime.implicitRefineToken !== token ||
          passRef.current !== pass
        ) {
          return;
        }
        runtime.requestRender?.();
        if (!refined || dynamicRenderActiveRef.current) {
          return;
        }
        modelBoundsRef.current?.({ min: [...refined.min], max: [...refined.max] }, { refined: true });
      })
      .catch(() => {
        if (!cancelled && runtimeRef.current === runtime && runtime.implicitRefinedBoundsKey === key) {
          runtime.implicitRefinedBoundsKey = "";
        }
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, model, runtimeRef, viewerReadyTick]);

  // Tear the pass down when the entry stops being an implicit, when the runtime
  // is recreated, or on unmount.
  useEffect(() => {
    if (enabled) {
      return undefined;
    }
    const pass = passRef.current;
    if (pass) {
      pass.quad.removeFromParent?.();
      pass.dispose?.();
      passRef.current = null;
      runtimeRef.current?.requestRender?.();
    }
    return undefined;
  }, [enabled, runtimeRef, viewerReadyTick]);

  useEffect(() => () => {
    const pass = passRef.current;
    if (pass) {
      pass.quad.removeFromParent?.();
      pass.dispose?.();
      passRef.current = null;
    }
  }, []);
}

export default useImplicitRaymarch;
