export default {
  schema: "implicit-cad/v1",
  name: "Sierpinski tetrahedron approximation",
  description: "Four-level folded implicit approximation of a Sierpinski tetrahedron with readable triangular voids.",
  units: "mm",
  params: {
    size: { type: "number", label: "Size", min: 16, max: 36, step: 0.5, default: 25, unit: "mm" },
    levels: { type: "number", label: "Recursion levels", min: 1, max: 5, step: 1, default: 4 },
    edgeThickness: { type: "number", label: "Edge thickness", min: 0.1, max: 1.4, step: 0.05, default: 0.34, unit: "mm" }
  },
  animations: {
    edgePulse: {
      label: "Edge pulse",
      duration: 5.0,
      loop: true,
      update({ progress, set }) {
        const wave = Math.sin(progress * Math.PI * 2);
        set("size", 25 + wave * 6);
        set("edgeThickness", 0.34 + wave * 0.22);
      }
    }
  },
  bounds: ({ params }) => {
    const extent = params.size + 7;
    return [[-extent, -extent, -extent], [extent, extent, extent]];
  },
  render: ({ params }) => ({
    steps: 360,
    epsilon: Math.max(0.005, params.edgeThickness * 0.016),
    normalEpsilon: Math.max(0.04, params.edgeThickness * 0.12)
  }),
  glsl: `
float implicitCadTetrahedron(vec3 p) {
  vec4 planes = vec4(
    p.x + p.y + p.z,
    p.x - p.y - p.z,
    -p.x + p.y - p.z,
    -p.x - p.y + p.z
  );
  return (max(max(planes.x, planes.y), max(planes.z, planes.w)) - 1.0) * 0.5773502691896258;
}

float sdf(vec3 p) {
  vec3 q = p / size;
  float scale = 1.0;

  for (int level = 0; level < 5; level += 1) {
    if (float(level) < levels) {
      if (q.x + q.y < 0.0) {
        q.xy = -q.yx;
      }
      if (q.x + q.z < 0.0) {
        q.xz = -q.zx;
      }
      if (q.y + q.z < 0.0) {
        q.zy = -q.yz;
      }
      q = q * 2.0 - vec3(1.0);
      scale *= 2.0;
    }
  }

  return implicitCadTetrahedron(q) * size / scale - edgeThickness;
}

vec3 color(vec3 p, vec3 normal) {
  float levelBands = 0.5 + 0.5 * sin((abs(p.x) + abs(p.y) + abs(p.z)) * 0.38);
  vec3 red = vec3(0.92, 0.18, 0.20);
  vec3 amber = vec3(0.98, 0.70, 0.24);
  return mix(red, amber, levelBands);
}
`
};
