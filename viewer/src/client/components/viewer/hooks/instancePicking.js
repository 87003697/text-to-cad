// Resolve the occurrence/part id a raycast intersection points at, for both
// per-occurrence THREE.Mesh records and cid-keyed InstancedMesh buckets.
//
// A per-occurrence mesh carries its id as userData.partId. An instanced package
// bucket carries the ordered occurrence ids in userData.cadInstanceOccurrenceIds,
// indexed by the raycast's instanceId (three.js populates intersection.instanceId
// for InstancedMesh hits). Kept as a standalone pure module so it unit-tests in
// Node without the hook's Vite-resolved (extension-less) imports.
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
