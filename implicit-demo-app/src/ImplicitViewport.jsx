import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import {
  configureImplicitCamera,
  createImplicitFullscreenScene,
  implicitModelShaderKey,
  updateImplicitAppearanceUniforms,
  updateImplicitGraphicsUniforms,
  updateImplicitMaterialUniforms,
  updateImplicitModelUniforms
} from "implicitjs";

function themeSettingsForMode(mode) {
  const dark = mode === "dark";
  return {
    background: dark
      ? {
          type: "linear",
          solidColor: "#1a1e24",
          linearStart: "#1a1e24",
          linearEnd: "#11151b",
          linearAngle: 145,
          radialInner: "#21262e",
          radialOuter: "#11151b"
        }
      : {
          type: "linear",
          solidColor: "#eceff4",
          linearStart: "#eceff4",
          linearEnd: "#d8dee9",
          linearAngle: 145,
          radialInner: "#f4f6fa",
          radialOuter: "#d8dee9"
        },
    materials: {
      defaultColor: dark ? "#88c0d0" : "#4c6d94",
      fillColors: [dark ? "#88c0d0" : "#4c6d94"],
      overrideSourceColors: false,
      roughness: 0.62,
      metalness: 0.02,
      clearcoat: 0.22
    }
  };
}

function disposeRuntime(runtime) {
  if (!runtime) {
    return;
  }
  runtime.controls?.dispose?.();
  runtime.fullscreen?.dispose?.();
  runtime.renderer?.dispose?.();
}

function resizeRuntime(runtime, container, graphics) {
  const rect = container.getBoundingClientRect();
  const cssWidth = Math.max(Math.floor(rect.width), 1);
  const cssHeight = Math.max(Math.floor(rect.height), 1);
  const interacting = runtime.controlsDragging === true;
  const scale = Math.max(
    interacting ? graphics.interactionResolutionScale : graphics.resolutionScale,
    0.25
  );
  const width = Math.max(Math.floor(cssWidth * scale), 1);
  const height = Math.max(Math.floor(cssHeight * scale), 1);

  runtime.renderer.setSize(width, height, false);
  runtime.renderer.domElement.style.width = `${cssWidth}px`;
  runtime.renderer.domElement.style.height = `${cssHeight}px`;
  runtime.camera.aspect = width / height;
  runtime.camera.updateProjectionMatrix();
  runtime.width = width;
  runtime.height = height;
}

function renderRuntime(runtime) {
  if (!runtime?.fullscreen?.material || !runtime?.model) {
    return;
  }
  updateImplicitMaterialUniforms(
    runtime.fullscreen.material,
    runtime.camera,
    runtime.width || 1,
    runtime.height || 1
  );
  runtime.renderer.render(runtime.fullscreen.scene, runtime.screenCamera);
}

function installOrbitControls(runtime, container, model, getGraphics) {
  runtime.controls?.dispose?.();
  const rect = container.getBoundingClientRect();
  runtime.camera = configureImplicitCamera(
    THREE,
    model,
    Math.max(rect.width, 1),
    Math.max(rect.height, 1),
    "iso",
    { zoom: 1.08 }
  );
  const controls = new OrbitControls(runtime.camera, runtime.renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.12;
  controls.screenSpacePanning = true;
  controls.target.set(...model.center);
  controls.addEventListener("change", () => {
    runtime.dirtyFrames = Math.max(runtime.dirtyFrames, 2);
  });
  controls.addEventListener("start", () => {
    runtime.controlsDragging = true;
    resizeRuntime(runtime, container, getGraphics());
    runtime.dirtyFrames = Math.max(runtime.dirtyFrames, 4);
  });
  controls.addEventListener("end", () => {
    runtime.controlsDragging = false;
    resizeRuntime(runtime, container, getGraphics());
    runtime.dirtyFrames = Math.max(runtime.dirtyFrames, 8);
  });
  runtime.controls = controls;
}

export function ImplicitViewport({
  model,
  graphics,
  themeMode = "dark",
  cameraResetToken = 0
}) {
  const containerRef = useRef(null);
  const runtimeRef = useRef(null);
  const latestRef = useRef({ model, graphics, themeMode });
  const themeSettings = useMemo(() => themeSettingsForMode(themeMode), [themeMode]);

  useEffect(() => {
    latestRef.current = { model, graphics, themeMode, themeSettings };
  }, [graphics, model, themeMode, themeSettings]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return undefined;
    }
    const renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: true,
      powerPreference: "high-performance"
    });
    renderer.setPixelRatio(1);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.domElement.className = "implicit-canvas";
    container.appendChild(renderer.domElement);

    const screenCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    const runtime = {
      renderer,
      screenCamera,
      camera: null,
      controls: null,
      fullscreen: null,
      model: null,
      shaderKey: "",
      width: 1,
      height: 1,
      dirtyFrames: 2,
      controlsDragging: false
    };
    runtimeRef.current = runtime;

    const requestRender = (frames = 2) => {
      runtime.dirtyFrames = Math.max(runtime.dirtyFrames, frames);
    };

    const resizeObserver = new ResizeObserver(() => {
      if (runtime.model) {
        resizeRuntime(runtime, container, latestRef.current.graphics);
        requestRender(3);
      }
    });
    resizeObserver.observe(container);

    const animate = () => {
      runtime.rafId = window.requestAnimationFrame(animate);
      const changed = runtime.controls?.update?.() === true;
      if (changed) {
        runtime.dirtyFrames = Math.max(runtime.dirtyFrames, 2);
      }
      if (runtime.dirtyFrames > 0) {
        runtime.dirtyFrames -= 1;
        renderRuntime(runtime);
      }
    };
    runtime.rafId = window.requestAnimationFrame(animate);

    return () => {
      resizeObserver.disconnect();
      window.cancelAnimationFrame(runtime.rafId);
      if (renderer.domElement.parentNode === container) {
        container.removeChild(renderer.domElement);
      }
      disposeRuntime(runtime);
      runtimeRef.current = null;
    };
  }, []);

  useEffect(() => {
    const runtime = runtimeRef.current;
    const container = containerRef.current;
    if (!runtime || !container) {
      return;
    }
    if (!model) {
      runtime.model = null;
      runtime.dirtyFrames = 0;
      runtime.renderer.clear();
      return;
    }
    const nextShaderKey = implicitModelShaderKey(model);
    const needsScene = !runtime.fullscreen || runtime.shaderKey !== nextShaderKey;
    if (needsScene) {
      runtime.fullscreen?.dispose?.();
      runtime.fullscreen = createImplicitFullscreenScene(THREE, model);
      runtime.shaderKey = nextShaderKey;
    } else {
      updateImplicitModelUniforms(THREE, runtime.fullscreen.material, model);
    }

    if (!runtime.camera || needsScene) {
      installOrbitControls(runtime, container, model, () => latestRef.current.graphics);
    }

    runtime.model = model;
    resizeRuntime(runtime, container, graphics);
    updateImplicitModelUniforms(THREE, runtime.fullscreen.material, model);
    updateImplicitAppearanceUniforms(THREE, runtime.fullscreen.material, model, {
      themeSettings,
      graphicsSettings: graphics
    });
    updateImplicitGraphicsUniforms(runtime.fullscreen.material, model, graphics);
    runtime.dirtyFrames = Math.max(runtime.dirtyFrames, 3);
  }, [graphics, model, themeSettings]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    const container = containerRef.current;
    if (!runtime?.model || !container) {
      return;
    }
    installOrbitControls(runtime, container, runtime.model, () => latestRef.current.graphics);
    resizeRuntime(runtime, container, latestRef.current.graphics);
    runtime.dirtyFrames = Math.max(runtime.dirtyFrames, 8);
  }, [cameraResetToken]);

  return (
    <div className="viewport-canvas-host" ref={containerRef}>
      {!model ? <div className="viewport-empty">waiting for a valid implicit module</div> : null}
    </div>
  );
}
