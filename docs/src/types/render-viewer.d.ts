declare module "@render-viewer/common/cadScene.js" {
  import type { Group, Vector3 } from "three";

  type CadBounds = {
    min: number[];
    max: number[];
  };

  export const CAD_SCENE_SCALE: {
    CAD: string;
    URDF: string;
  };

  export type CadSceneApi = {
    root: Group;
    modelGroup: Group;
    edgesGroup: Group;
    displayRecords: unknown[];
    records: unknown[];
    bounds: CadBounds;
    radius: number;
    runtime: unknown;
    dispose: () => void;
    update: (settings?: Record<string, unknown>) => CadSceneApi;
  };

  export function buildCadScene(
    THREE: unknown,
    meshData: unknown,
    settings?: Record<string, unknown>
  ): CadSceneApi;

  export function fitCameraToScene(
    THREE: unknown,
    camera: unknown,
    bounds: CadBounds,
    options?: Record<string, unknown>
  ): {
    center: Vector3;
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
