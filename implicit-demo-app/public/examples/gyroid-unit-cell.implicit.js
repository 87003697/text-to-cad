export default {
  schema: "implicit-cad/v1",
  name: "gyroid unit cell",
  description: "Finite-thickness gyroid minimal surface clipped to one cubic periodic unit cell.",
  units: "mm",
  params: {
    cellSize: { type: "number", label: "Cell size", min: 32, max: 72, step: 0.5, default: 48, unit: "mm" },
    wallThickness: { type: "number", label: "Wall thickness", min: 1.2, max: 7, step: 0.1, default: 3.6, unit: "mm" },
    fieldScale: { type: "number", label: "Field scale", min: 0.35, max: 0.9, step: 0.01, default: 0.52 }
  },
  animations: {
    wallPulse: {
      label: "Wall pulse",
      duration: 5.4,
      loop: true,
      update({ progress, set }) {
        const wave = Math.sin(progress * Math.PI * 2);
        set("wallThickness", 3.6 + wave * 2.0);
        set("fieldScale", 0.52 + wave * 0.16);
      }
    }
  },
  bounds: ({ params }) => {
    const half = params.cellSize / 2 + Math.max(params.wallThickness, 3);
    return [[-half, -half, -half], [half, half, half]];
  },
  render: ({ params }) => ({
    steps: 340,
    epsilon: Math.max(0.005, params.cellSize * 0.00014),
    normalEpsilon: Math.max(0.04, params.wallThickness * 0.014)
  }),
  glsl: `
float sdf(vec3 p) {
  float field = implicitCadTpmsGyroid(p, vec3(cellSize), vec3(1.0));
  float shell = implicitCadShell(field, wallThickness, 0.0);
  float cell = implicitCadBoxCentered(p, vec3(cellSize), vec3(0.0));
  return max(shell, cell) * fieldScale;
}


vec3 color(vec3 p, vec3 normal) {
  vec3 period = 0.5 + 0.5 * sin((p / cellSize) * 6.283185307179586 + vec3(0.0, 2.1, 4.2));
  vec3 teal = vec3(0.10, 0.72, 0.66);
  vec3 gold = vec3(0.94, 0.72, 0.25);
  return mix(teal, gold, dot(period, vec3(0.28, 0.36, 0.36)));
}
`
};
