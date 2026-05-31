export default {
  schema: "implicit-cad/v1",
  name: "genus-2 double torus",
  description: "Two toroidal handles smoothly fused into one symmetric watertight genus-2 solid.",
  units: "mm",
  params: {
    separation: { type: "number", label: "Handle spacing", min: 16, max: 34, step: 0.25, default: 26, unit: "mm" },
    majorRadius: { type: "number", label: "Major radius", min: 7, max: 15, step: 0.25, default: 10.5, unit: "mm" },
    tubeRadius: { type: "number", label: "Tube radius", min: 2.2, max: 6.5, step: 0.1, default: 4.2, unit: "mm" },
    bridgeRadius: { type: "number", label: "Bridge radius", min: 2.6, max: 7, step: 0.1, default: 5.2, unit: "mm" },
    blend: { type: "number", label: "Blend", min: 0.5, max: 6, step: 0.1, default: 3.8, unit: "mm" }
  },
  animations: {
    handleBreath: {
      label: "Handle breath",
      duration: 5.0,
      loop: true,
      update({ progress, set }) {
        const wave = Math.sin(progress * Math.PI * 2);
        set("separation", 26 + wave * 5);
        set("tubeRadius", 4.2 + wave * 1.1);
        set("blend", 3.8 + wave * 2.0);
      }
    }
  },
  bounds: ({ params }) => {
    const halfSpacing = params.separation / 2;
    const x = halfSpacing + params.majorRadius + params.tubeRadius + params.blend + 8;
    const yz = params.majorRadius + params.tubeRadius + params.blend + 8;
    return [[-x, -yz, -yz], [x, yz, yz]];
  },
  render: ({ params }) => ({
    steps: 240,
    epsilon: Math.max(0.004, params.tubeRadius * 0.0014),
    normalEpsilon: Math.max(0.035, params.tubeRadius * 0.011)
  }),
  glsl: `
float sdf(vec3 p) {
  float left = implicitCadTorus(p - vec3(-(separation * 0.5), 0.0, 0.0), majorRadius, tubeRadius);
  float right = implicitCadTorus(p - vec3((separation * 0.5), 0.0, 0.0), majorRadius, tubeRadius);
  float bridge = implicitCadCapsule(p, vec3(-max((separation * 0.5) - tubeRadius * 0.6, tubeRadius), 0.0, 0.0), vec3(max((separation * 0.5) - tubeRadius * 0.6, tubeRadius), 0.0, 0.0), bridgeRadius);
  float handles = implicitCadUnionRound(left, right, blend);
  return implicitCadUnionRound(handles, bridge, max(blend * 0.78, 0.4));
}


vec3 color(vec3 p, vec3 normal) {
  float side = smoothstep(-18.0, 18.0, p.x);
  vec3 rose = vec3(0.96, 0.38, 0.68);
  vec3 violet = vec3(0.50, 0.38, 0.92);
  vec3 accent = vec3(0.98, 0.83, 0.38);
  return mix(mix(rose, violet, side), accent, smoothstep(0.65, 0.98, abs(normal.z)) * 0.28);
}
`
};
