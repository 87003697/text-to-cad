import {
  buildPartTransformMatrix
} from "./stepModuleEffects.js";
import { syncRecordInstanceTransform } from "../lib/assembly/instancedRecordBatches.js";

function applyObjectMatrix(THREE, object3d, matrix) {
  if (!object3d || !(matrix instanceof THREE.Matrix4)) {
    return;
  }
  object3d.matrixAutoUpdate = false;
  const targetMatrix = object3d.matrix instanceof THREE.Matrix4 ? object3d.matrix : new THREE.Matrix4();
  targetMatrix.copy(matrix);
  object3d.matrix = targetMatrix;
  object3d.matrixWorldNeedsUpdate = true;
}

// baseTransform is fixed at record creation; cache its Matrix4 so per-frame
// animation passes over thousands of records stop allocating.
function baseMatrixForRecord(THREE, record) {
  if (record.__baseMatrix && record.__baseMatrixSource === record.baseTransform) {
    return record.__baseMatrix;
  }
  record.__baseMatrix = buildPartTransformMatrix(THREE, record.baseTransform);
  record.__baseMatrixSource = record.baseTransform;
  return record.__baseMatrix;
}

export function composeDisplayRecordEffectMatrix(THREE, record) {
  if (!record || !THREE?.Matrix4) {
    return null;
  }
  const matrices = [
    record.effectMatrix,
    record.explodedViewMatrix
  ].filter((matrix) => matrix instanceof THREE.Matrix4);
  if (!matrices.length) {
    return null;
  }
  const combined = new THREE.Matrix4();
  for (const matrix of matrices) {
    combined.premultiply(matrix);
  }
  return combined;
}

export function composeDisplayRecordObjectMatrix(THREE, record) {
  const baseMatrix = buildPartTransformMatrix(THREE, record?.baseTransform);
  const effectMatrix = composeDisplayRecordEffectMatrix(THREE, record);
  return effectMatrix ? effectMatrix.multiply(baseMatrix) : baseMatrix;
}

export function applyDisplayRecordTransform(THREE, record) {
  if (!record) {
    return;
  }
  const baseMatrix = baseMatrixForRecord(THREE, record);
  const effectMatrix = record.effectMatrix instanceof THREE.Matrix4 ? record.effectMatrix : null;
  const explodedMatrix = record.explodedViewMatrix instanceof THREE.Matrix4 ? record.explodedViewMatrix : null;
  let combined = baseMatrix;
  if (effectMatrix || explodedMatrix) {
    const scratch = record.__composedMatrix || (record.__composedMatrix = new THREE.Matrix4());
    scratch.copy(baseMatrix);
    if (effectMatrix) {
      scratch.premultiply(effectMatrix);
    }
    if (explodedMatrix) {
      scratch.premultiply(explodedMatrix);
    }
    combined = scratch;
  }
  applyObjectMatrix(THREE, record.mesh, combined);
  applyObjectMatrix(THREE, record.edges, combined);
  applyObjectMatrix(THREE, record.silhouette, combined);
  if (record.instanceSlot) {
    syncRecordInstanceTransform(record);
  }
}
