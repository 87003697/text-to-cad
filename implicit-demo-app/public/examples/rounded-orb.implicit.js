const GLSL = `
float sdf(vec3 p) {
  float sphereBody = implicitCadSphere(p, vec3(0.0, 0.0, 0.0), radius);
  float cutBox = implicitCadBoxCentered(p, vec3(radius * 1.58, radius * 1.58, baseHeight), vec3(0.0, 0.0, -radius * 0.75));
  float bore = implicitCadCylinderCapped(p, vec3(-radius * 1.25, 0.0, radius * 0.2), vec3(radius * 1.25, 0.0, radius * 0.2), boreRadius);
  return implicitCadIntersectRound(
    implicitCadUnionRound(sphereBody, cutBox, blend),
    -bore,
    max(blend * 0.5, 0.35)
  );
}


vec3 color(vec3 p, vec3 normal) {
  float sphereBlend = smoothstep(-18.0, 18.0, p.z);
  vec3 base = mix(vec3(0.04, 0.50, 0.74), vec3(0.92, 0.34, 0.76), sphereBlend);
  float plinthMask = 1.0 - smoothstep(-17.0, -7.0, p.z);
  base = mix(base, vec3(0.05, 0.35, 0.32), plinthMask * 0.9);
  float boreAccent = smoothstep(0.72, 0.98, abs(normal.x)) * smoothstep(-2.0, 12.0, p.z);
  vec3 accent = vec3(1.0, 0.68, 0.12);
  return mix(base, accent, boreAccent);
}
`;

export default {
  schema: "implicit.js/0.1.0",
  name: "rounded orb",
  units: "mm",
  params: {
    radius: { type: "number", label: "Radius", min: 14, max: 32, step: 0.25, default: 24, unit: "mm" },
    baseHeight: { type: "number", label: "Base height", min: 8, max: 24, step: 0.25, default: 16, unit: "mm" },
    boreRadius: { type: "number", label: "Bore radius", min: 2, max: 11, step: 0.1, default: 7, unit: "mm" },
    blend: { type: "number", label: "Blend", min: 0.5, max: 6, step: 0.1, default: 3, unit: "mm" }
  },
  animations: {
    boreBreath: {
      label: "Bore breath",
      duration: 4.2,
      loop: true,
      update({ progress, set }) {
        const wave = Math.sin(progress * Math.PI * 2);
        set("boreRadius", 7 + wave * 3);
        set("blend", 3 + wave * 2);
        set("baseHeight", 16 + wave * 5);
      }
    }
  },
  bounds: ({ params }) => {
    const extent = params.radius + params.blend + 9;
    return [[-extent, -extent, -extent], [extent, extent, extent]];
  },
  render: ({ params }) => ({
    steps: 224,
    epsilon: Math.max(0.004, params.radius * 0.00025),
    normalEpsilon: Math.max(0.035, params.radius * 0.0018)
  }),
  glsl: GLSL
};
