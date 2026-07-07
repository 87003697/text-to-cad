// Resolve the occurrence/part id a raycast intersection points at, for both
// per-occurrence THREE.Mesh records and cid-keyed InstancedMesh buckets.
//
// A per-occurrence mesh carries its id as userData.partId. An instanced package
// bucket carries the ordered occurrence ids in userData.cadInstanceOccurrenceIds,
// indexed by the raycast's instanceId (three.js populates intersection.instanceId
// for InstancedMesh hits). Kept as a standalone pure module so it unit-tests in
// Node without the hook's Vite-resolved (extension-less) imports.
// Whether a display record's mesh belongs in the part-pick raycast candidate set.
//
// Per-occurrence records apply the usual bucket-level hidden/focus filtering by
// partId. Instanced package buckets carry no partId (occurrence ids live per
// instance), so those filters would wrongly drop the whole InstancedMesh whenever
// a focus is active — killing pick/hover for every occurrence, including the
// focused one. Keep an instanced bucket in the set whenever its mesh is visible:
// hidden instances are already collapsed to a zero matrix (unhittable) and the
// per-occurrence hidden/focus filter runs downstream in
// pickPartReferenceFromIntersections.
export function shouldRaycastRecordForPick(record, { focusIds, hiddenIds } = {}) {
  if (!record?.mesh?.visible) {
    return false;
  }
  if (record.instanced) {
    return true;
  }
  const partId = String(record?.partId || "").trim();
  if (hiddenIds instanceof Set && hiddenIds.has(partId)) {
    return false;
  }
  if (focusIds instanceof Set && focusIds.size && !focusIds.has(partId)) {
    return false;
  }
  return true;
}

export function partIdFromIntersection(intersection) {
  const object = intersection?.object;
  if (!object) {
    return null;
  }
  const direct = object.userData?.partId;
  if (direct) {
    return direct;
  }
  const occurrenceIds = object.userData?.cadInstanceOccurrenceIds;
  const instanceId = intersection?.instanceId;
  if (
    Array.isArray(occurrenceIds) &&
    Number.isInteger(instanceId) &&
    instanceId >= 0 &&
    instanceId < occurrenceIds.length
  ) {
    return occurrenceIds[instanceId] || null;
  }
  return null;
}
