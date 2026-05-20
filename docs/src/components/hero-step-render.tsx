"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import {
  buildCadScene,
  CAD_SCENE_SCALE,
  fitCameraToScene,
} from "@render-viewer/common/cadScene.js";
import {
  loadStepModuleDefinition,
  normalizeParameterValue,
  normalizeStepModuleParameterValues,
} from "@render-viewer/common/stepModule.js";
import { cloneThemeSettings } from "@render-viewer/common/themeSettings.js";
import { buildMeshDataFromGlbBuffer } from "@render-viewer/lib/render/glbMeshData.js";

const HERO_STEP_URL = "/api/hero-step";
const HERO_STEP_MODULE_URL = "/api/hero-step-module";
const HERO_STEP_CAD_PATH = "models/fun/planetary_gear_assembly.step";
const HERO_STEP_DEMO_URL =
  "https://demo.cadskills.xyz/?file=fun%2Fplanetary_gear_assembly.step";
const HERO_STEP_LABEL = "PLANETARY_GEAR_ASSEMBLY.STEP";
const GEAR_MESH_ANIMATION_SPEED = 0.14;
const HERO_STEP_PARAMETER_VALUES = {
  drive: 0,
  explode: 0,
  highlightMeshing: false,
  orbitGuides: false,
  ringVisible: true,
  viewMode: "mesh",
};

type PreviewScheme = "dark" | "light";
type StepModuleDefinition = Awaited<
  ReturnType<typeof loadStepModuleDefinition>
>;
type StepModuleAnimation = StepModuleDefinition["animations"][number];
type StepModuleRuntime = {
  animation: StepModuleAnimation | null;
  animationElapsedSec: number;
  animationState: {
    activeId: string;
    duration: number;
    elapsedSec: number;
    loop: boolean;
    playing: boolean;
    speed: number;
  };
  cadPath: string;
  definition: StepModuleDefinition;
  parameterValues: Record<string, unknown>;
  selectorRuntime: null;
  sourceUrl: string;
};

const STEP_PREVIEW_PALETTES = {
  dark: {
    background: "#111820",
    border: "#3b4553",
    fill: ["#c7d0d8", "#aeb9c3", "#d9dee3", "#8f9ba7"],
    headerBackground: "rgba(17, 24, 32, 0.9)",
    headerText: "#c9d3df",
    keyLight: "#f6f8fb",
    keyLightIntensity: 2.5,
    fillLight: "#7f95ad",
    fillLightIntensity: 0.8,
    ambientLight: "#eef4fa",
    ambientLightIntensity: 1.85,
  },
  light: {
    background: "#eef1f5",
    border: "#c9cfda",
    fill: ["#d7dce0", "#cdd3d8", "#e4e7ea", "#bfc7ce"],
    headerBackground: "rgba(238, 241, 245, 0.9)",
    headerText: "#4c566a",
    keyLight: "#ffffff",
    keyLightIntensity: 2.6,
    fillLight: "#cfd8e3",
    fillLightIntensity: 0.9,
    ambientLight: "#ffffff",
    ambientLightIntensity: 2.2,
  },
} as const;

function currentPreviewScheme(): PreviewScheme {
  if (typeof document === "undefined") {
    return "dark";
  }

  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

function buildWorkbenchTheme(scheme: PreviewScheme) {
  const palette = STEP_PREVIEW_PALETTES[scheme];
  const theme = cloneThemeSettings("workbench");
  const materials =
    theme.materials && typeof theme.materials === "object"
      ? theme.materials
      : {};

  return {
    ...theme,
    materials: {
      ...materials,
      defaultColor: palette.fill[0],
      fillColors: [...palette.fill],
      overrideSourceColors: false,
      saturation: 1,
      contrast: 1,
      brightness: 1,
      tintStrength: 0,
      roughness: 0.92,
      metalness: 0,
      clearcoat: 0,
      envMapIntensity: 0,
    },
  };
}

async function loadHeroStepModuleRuntime(): Promise<StepModuleRuntime> {
  const moduleUrl = `${HERO_STEP_MODULE_URL}?v=${Date.now()}`;
  const definition = await loadStepModuleDefinition(moduleUrl, {
    cadPath: HERO_STEP_CAD_PATH,
  });
  const animation =
    definition.animations.find((item) => item.id === "meshCycle") ??
    definition.animations[0] ??
    null;
  const duration = Math.max(Number(animation?.duration) || 6, 0.001);

  return {
    animation,
    animationElapsedSec: 0,
    animationState: {
      activeId: animation?.id ?? "",
      duration,
      elapsedSec: 0,
      loop: animation?.loop !== false,
      playing: false,
      speed: GEAR_MESH_ANIMATION_SPEED,
    },
    cadPath: HERO_STEP_CAD_PATH,
    definition,
    parameterValues: normalizeStepModuleParameterValues(
      definition,
      HERO_STEP_PARAMETER_VALUES
    ),
    selectorRuntime: null,
    sourceUrl: moduleUrl,
  };
}

function advanceStepModuleRuntime(
  runtime: StepModuleRuntime,
  deltaSeconds: number
) {
  const animation = runtime.animation;
  if (!animation?.update) {
    return;
  }

  const duration = Math.max(Number(animation.duration) || 6, 0.001);
  const nextElapsed =
    (runtime.animationElapsedSec +
      Math.max(deltaSeconds, 0) * GEAR_MESH_ANIMATION_SPEED) %
    duration;
  const progress = nextElapsed / duration;
  const currentValues = normalizeStepModuleParameterValues(
    runtime.definition,
    runtime.parameterValues
  );
  const nextValues = { ...currentValues };

  const set = (parameterId: string, value: unknown) => {
    const id = String(parameterId || "").trim();
    const parameter = runtime.definition.parameterMap?.[id];
    if (!parameter) {
      return;
    }
    nextValues[id] = normalizeParameterValue(parameter, value);
  };

  animation.update({
    cycle: nextElapsed / duration,
    duration,
    elapsed: nextElapsed,
    elapsedSec: nextElapsed,
    loop: animation.loop !== false,
    params: currentValues,
    progress,
    set,
  });

  runtime.animationElapsedSec = nextElapsed;
  // The hero drives sidecar params directly; keep time.playing false so
  // sidecar-only inspection highlights do not override native STEP colors.
  runtime.animationState = {
    activeId: animation.id,
    duration,
    elapsedSec: nextElapsed,
    loop: animation.loop !== false,
    playing: false,
    speed: GEAR_MESH_ANIMATION_SPEED,
  };
  runtime.parameterValues = nextValues;
}

export function HeroStepRender() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const [scheme, setScheme] = useState<PreviewScheme>("dark");
  const [status, setStatus] = useState("loading step");
  const palette = STEP_PREVIEW_PALETTES[scheme];

  useEffect(() => {
    const syncScheme = () => {
      setScheme(currentPreviewScheme());
    };

    syncScheme();

    const observer = new MutationObserver(syncScheme);
    observer.observe(document.documentElement, {
      attributeFilter: ["class"],
      attributes: true,
    });

    return () => {
      observer.disconnect();
    };
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }

    let disposed = false;
    let frame = 0;
    let resizeObserver: ResizeObserver | null = null;
    let cadScene: ReturnType<typeof buildCadScene> | null = null;
    let stepModuleRuntime: StepModuleRuntime | null = null;
    let lastRenderTime = performance.now();
    const dragState = {
      active: false,
      lastX: 0,
      lastY: 0,
      pitch: 0.18,
      pointerId: -1,
      yaw: -0.18,
    };

    const renderer = new THREE.WebGLRenderer({
      canvas,
      alpha: false,
      antialias: true,
      powerPreference: "high-performance",
    });
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.setClearColor(new THREE.Color(palette.background), 1);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(palette.background);

    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.01, 1000);
    scene.add(
      new THREE.AmbientLight(
        palette.ambientLight,
        palette.ambientLightIntensity
      )
    );

    const keyLight = new THREE.DirectionalLight(
      palette.keyLight,
      palette.keyLightIntensity
    );
    keyLight.position.set(-120, 180, 240);
    scene.add(keyLight);

    const fillLight = new THREE.DirectionalLight(
      palette.fillLight,
      palette.fillLightIntensity
    );
    fillLight.position.set(180, -120, 120);
    scene.add(fillLight);

    const resize = () => {
      if (!viewportRef.current || !cadScene) {
        return;
      }
      const rect = viewportRef.current.getBoundingClientRect();
      const width = Math.max(1, Math.floor(rect.width));
      const height = Math.max(1, Math.floor(rect.height));
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.setSize(width, height, false);
      const fit = fitCameraToScene(THREE, camera, cadScene.bounds, {
        direction: [1, -1, 0.65],
        up: [0, 0, 1],
        width,
        height,
        padding: 0.1,
        scale: CAD_SCENE_SCALE.CAD,
      });
      fitCameraToScene(THREE, camera, cadScene.bounds, {
        direction: [1, -1, 0.65],
        lockedHalfHeight: fit.halfHeight * 0.86,
        up: [0, 0, 1],
        width,
        height,
        padding: 0.1,
        scale: CAD_SCENE_SCALE.CAD,
      });
    };

    const render = () => {
      if (disposed || !cadScene) {
        return;
      }
      const now = performance.now();
      const deltaSeconds = Math.min((now - lastRenderTime) / 1000, 0.1);
      lastRenderTime = now;

      if (stepModuleRuntime) {
        advanceStepModuleRuntime(stepModuleRuntime, deltaSeconds);
        cadScene.update({ parameters: stepModuleRuntime });
      }

      if (!dragState.active) {
        dragState.yaw += 0.0018;
      }
      cadScene.root.rotation.x = dragState.pitch;
      cadScene.root.rotation.z = dragState.yaw;
      renderer.render(scene, camera);
      frame = window.requestAnimationFrame(render);
    };

    const handlePointerDown = (event: PointerEvent) => {
      dragState.active = true;
      dragState.lastX = event.clientX;
      dragState.lastY = event.clientY;
      dragState.pointerId = event.pointerId;
      canvas.setPointerCapture(event.pointerId);
    };

    const handlePointerMove = (event: PointerEvent) => {
      if (!dragState.active || event.pointerId !== dragState.pointerId) {
        return;
      }

      const dx = event.clientX - dragState.lastX;
      const dy = event.clientY - dragState.lastY;
      dragState.lastX = event.clientX;
      dragState.lastY = event.clientY;
      dragState.yaw += dx * 0.008;
      dragState.pitch = Math.max(
        -0.85,
        Math.min(0.85, dragState.pitch + dy * 0.006)
      );
    };

    const handlePointerEnd = (event: PointerEvent) => {
      if (event.pointerId !== dragState.pointerId) {
        return;
      }

      dragState.active = false;
      dragState.pointerId = -1;
      if (canvas.hasPointerCapture(event.pointerId)) {
        canvas.releasePointerCapture(event.pointerId);
      }
    };

    const load = async () => {
      try {
        setStatus("loading step");
        const [response, loadedStepModuleRuntime] = await Promise.all([
          fetch(HERO_STEP_URL, { cache: "no-store" }),
          loadHeroStepModuleRuntime(),
        ]);
        if (!response.ok) {
          const message = await response
            .json()
            .then((body: { detail?: string; error?: string }) =>
              String(body.detail || body.error || "").trim()
            )
            .catch(() => "");

          throw new Error(message || `HTTP ${response.status}`);
        }

        const meshData = await buildMeshDataFromGlbBuffer(
          await response.arrayBuffer()
        );
        if (disposed) {
          return;
        }
        stepModuleRuntime = loadedStepModuleRuntime;

        cadScene = buildCadScene(THREE, meshData, {
          theme: buildWorkbenchTheme(scheme),
          displayMode: "solid",
          parameters: stepModuleRuntime,
          scale: CAD_SCENE_SCALE.CAD,
          selection: {
            showEdges: true,
          },
        });
        cadScene.root.rotation.x = dragState.pitch;
        cadScene.root.rotation.z = dragState.yaw;
        scene.add(cadScene.root);
        resize();
        resizeObserver = new ResizeObserver(resize);
        resizeObserver.observe(viewportRef.current ?? canvas);
        setStatus(HERO_STEP_LABEL);
        render();
      } catch (error) {
        setStatus(error instanceof Error ? error.message : "render failed");
      }
    };

    canvas.addEventListener("pointerdown", handlePointerDown);
    canvas.addEventListener("pointermove", handlePointerMove);
    canvas.addEventListener("pointerup", handlePointerEnd);
    canvas.addEventListener("pointercancel", handlePointerEnd);
    void load();

    return () => {
      disposed = true;
      canvas.removeEventListener("pointerdown", handlePointerDown);
      canvas.removeEventListener("pointermove", handlePointerMove);
      canvas.removeEventListener("pointerup", handlePointerEnd);
      canvas.removeEventListener("pointercancel", handlePointerEnd);
      window.cancelAnimationFrame(frame);
      resizeObserver?.disconnect();
      cadScene?.dispose();
      renderer.dispose();
    };
  }, [palette, scheme]);

  return (
    <div
      className="flex h-full min-h-0 flex-col overflow-hidden"
      style={{ backgroundColor: palette.background }}
    >
      <div ref={viewportRef} className="relative min-h-0 flex-1 overflow-hidden">
        <canvas
          ref={canvasRef}
          aria-label="Light 3D render of a sample STEP planetary gear assembly"
          className="absolute inset-0 h-full w-full cursor-grab touch-none active:cursor-grabbing"
        />
      </div>
      <div
        className="flex min-h-8 shrink-0 items-center justify-between gap-3 border-t px-3 py-[7px] text-label uppercase leading-none tracking-[1.5px]"
        style={{
          backgroundColor: palette.headerBackground,
          borderColor: palette.border,
          color: palette.headerText,
        }}
      >
        {status === HERO_STEP_LABEL ? (
          <a
            className="min-w-0 truncate transition hover:text-primary"
            href={HERO_STEP_DEMO_URL}
            target="_blank"
            rel="noreferrer"
            title="Open planetary gear assembly in the CAD Skills demo"
          >
            {status}
          </a>
        ) : (
          <span className="min-w-0 truncate">{status}</span>
        )}
      </div>
    </div>
  );
}
