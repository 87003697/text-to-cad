declare module "@render-viewer/common/cadScene.js" {
  type CadSceneRoot = {
    rotation: {
      x: number;
      y: number;
      z: number;
    };
  };

  export const CAD_SCENE_SCALE: {
    CAD: string;
    URDF: string;
  };

  export function buildCadScene(
    THREE: unknown,
    meshData: unknown,
    settings?: Record<string, unknown>
  ): {
    root: CadSceneRoot;
    bounds: { min: number[]; max: number[] };
    radius: number;
    dispose: () => void;
    update: (settings?: Record<string, unknown>) => unknown;
  };

  export function fitCameraToScene(
    THREE: unknown,
    camera: unknown,
    bounds: { min: number[]; max: number[] },
    options?: Record<string, unknown>
  ): {
    center: unknown;
    radius: number;
    halfHeight: number;
    distance: number;
  };
}

declare module "@render-viewer/common/themeSettings.js" {
  export function cloneThemeSettings(themeId: string): Record<
    string,
    unknown
  > & {
    materials?: Record<string, unknown>;
  };
}

declare module "@render-viewer/common/stepModule.js" {
  export type StepModuleParameter = {
    id: string;
    type: string;
    defaultValue: unknown;
  };

  export type StepModuleAnimation = {
    id: string;
    duration: number;
    loop: boolean;
    update?: (context: {
      cycle: number;
      duration: number;
      elapsed: number;
      elapsedSec: number;
      loop: boolean;
      params: Record<string, unknown>;
      progress: number;
      set: (parameterId: string, value: unknown) => void;
    }) => void;
  };

  export type StepModuleDefinition = {
    animations: StepModuleAnimation[];
    parameterMap: Record<string, StepModuleParameter>;
  };

  export function loadStepModuleDefinition(
    url: string,
    options?: Record<string, unknown>
  ): Promise<StepModuleDefinition>;

  export function normalizeParameterValue(
    definition: StepModuleParameter,
    value: unknown
  ): unknown;

  export function normalizeStepModuleParameterValues(
    definition: StepModuleDefinition,
    values?: Record<string, unknown>
  ): Record<string, unknown>;
}

declare module "@render-viewer/lib/render/glbMeshData.js" {
  export function buildMeshDataFromGlbBuffer(buffer: ArrayBuffer): Promise<unknown>;
}

declare module "@render-viewer/node_modules/three/build/three.module.js" {
  type ColorValue = string | number;
  type RendererPowerPreference = "default" | "high-performance" | "low-power";

  export const PCFSoftShadowMap: unknown;
  export const SRGBColorSpace: unknown;

  export class Color {
    constructor(color?: ColorValue);
  }

  export class AmbientLight {
    constructor(color?: ColorValue, intensity?: number);
  }

  export class DirectionalLight {
    position: {
      set(x: number, y: number, z: number): void;
    };

    constructor(color?: ColorValue, intensity?: number);
  }

  export class OrthographicCamera {
    constructor(
      left: number,
      right: number,
      top: number,
      bottom: number,
      near?: number,
      far?: number
    );
  }

  export class Scene {
    background: Color | null;

    add(object: unknown): void;
  }

  export class WebGLRenderer {
    outputColorSpace: unknown;
    shadowMap: {
      enabled: boolean;
      type: unknown;
    };

    constructor(parameters?: {
      canvas?: HTMLCanvasElement;
      alpha?: boolean;
      antialias?: boolean;
      powerPreference?: RendererPowerPreference;
    });

    dispose(): void;
    render(scene: Scene, camera: OrthographicCamera): void;
    setClearColor(color: Color, alpha?: number): void;
    setPixelRatio(value: number): void;
    setSize(width: number, height: number, updateStyle?: boolean): void;
  }
}
