// Headless snapshots use the shared mesh renderer.
export function resolveHeadlessJobKind(job) {
  void job;
  return "mesh";
}
