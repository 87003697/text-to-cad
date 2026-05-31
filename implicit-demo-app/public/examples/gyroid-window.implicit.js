export default {
  schema: "implicit-cad/v1",
  name: "gyroid window",
  units: "mm",
  params: {
    periodScale: { type: "number", label: "Period scale", min: 5, max: 13, step: 0.1, default: 8.5, unit: "mm" },
    wallThickness: { type: "number", label: "Wall thickness", min: 0.14, max: 0.7, step: 0.01, default: 0.34 },
    windowSize: { type: "number", label: "Window size", min: 36, max: 66, step: 0.5, default: 52, unit: "mm" },
    outerRadius: { type: "number", label: "Outer radius", min: 24, max: 42, step: 0.5, default: 33.5, unit: "mm" },
    roundover: { type: "number", label: "Roundover", min: 0.5, max: 5, step: 0.1, default: 2.5, unit: "mm" }
  },
  animations: {
    windowPulse: {
      label: "Window pulse",
      duration: 5.6,
      loop: true,
      update({ progress, set }) {
        const wave = Math.sin(progress * Math.PI * 2);
        set("wallThickness", 0.34 + wave * 0.16);
        set("roundover", 2.5 + wave * 1.5);
        set("windowSize", 52 + wave * 8);
      }
    }
  },
  bounds: ({ params }) => {
    const extent = Math.max(params.windowSize * 0.5, params.outerRadius) + params.roundover + 4;
    return [[-extent, -extent, -extent], [extent, extent, extent]];
  },
  render: ({ params }) => ({
    steps: 280,
    epsilon: Math.max(0.004, params.periodScale * 0.0007),
    normalEpsilon: Math.max(0.04, params.periodScale * 0.006)
  }),
  glsl: `
float sdf(vec3 p) {
  vec3 q = p / periodScale;
  float gyroid = implicitCadTpmsGyroid(q, vec3(1.0), vec3(1.0));
  float sheet = implicitCadShell(gyroid, wallThickness, 0.0) * periodScale;
  float roundedWindow = implicitCadBoxCentered(p, vec3(windowSize), vec3(0.0));
  float roundover = implicitCadSphere(p, vec3(0.0), outerRadius);
  return implicitCadIntersectRound(max(sheet, roundedWindow), roundover, roundover);
}


vec3 color(vec3 p, vec3 normal) {
  vec3 wave = 0.5 + 0.5 * sin((p * 0.095) + vec3(0.0, 2.1, 4.2));
  vec3 cool = vec3(0.18, 0.72, 0.88);
  vec3 warm = vec3(0.95, 0.74, 0.32);
  return mix(cool, warm, dot(wave, vec3(0.28, 0.36, 0.36)));
}
`
};
