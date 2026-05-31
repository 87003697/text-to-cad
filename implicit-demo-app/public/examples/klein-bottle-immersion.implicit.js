const glslFloat = (value) => Number(value).toFixed(4);
const glslVec3 = (value) => `vec3(${value.map(glslFloat).join(", ")})`;
const bezierPoint = (a, b, c, d, t) => {
  const u = 1 - t;
  return [0, 1, 2].map((index) => (
    u * u * u * a[index] +
    3 * u * u * t * b[index] +
    3 * u * t * t * c[index] +
    t * t * t * d[index]
  ));
};
const tubeDistanceSource = (variableName, points, radius, segments = 9) => {
  const samples = Array.from({ length: segments + 1 }, (_, index) => (
    bezierPoint(points[0], points[1], points[2], points[3], index / segments)
  ));
  const radiusSource = typeof radius === "string" ? radius : glslFloat(radius);
  const lines = [`  float ${variableName} = 1.0e6;`];
  for (let index = 1; index < samples.length; index += 1) {
    lines.push(
      `  ${variableName} = min(${variableName}, implicitCadCapsule(p, ${glslVec3(samples[index - 1])}, ${glslVec3(samples[index])}, ${radiusSource}));`
    );
  }
  return lines.join("\n");
};

const risingNeckDistanceSource = tubeDistanceSource("risingNeck", [
  [0, 0, 14.5],
  [-4, -19, 24],
  [18, -17.5, 11],
  [13, -3.5, 1],
], "neckRadius");

const passingNeckDistanceSource = tubeDistanceSource("passingNeck", [
  [13, -3.5, 1],
  [11, 5, -3],
  [1.5, 8, -7],
  [-1, 0, -13.5],
], "throatRadius");

export default {
  schema: "implicit-cad/v1",
  name: "Klein bottle immersion",
  description: "A render-friendly Klein bottle immersion: bulb, self-passing neck, and printable shell thickness.",
  units: "mm",
  params: {
    wallThickness: { type: "number", label: "Wall thickness", min: 0.8, max: 4.2, step: 0.1, default: 2.2, unit: "mm" },
    bulbScale: { type: "number", label: "Bulb scale", min: 0.72, max: 1.35, step: 0.02, default: 1 },
    neckRadius: { type: "number", label: "Neck radius", min: 1.8, max: 5.2, step: 0.1, default: 3.1, unit: "mm" },
    throatRadius: { type: "number", label: "Throat radius", min: 1.5, max: 4.8, step: 0.1, default: 2.7, unit: "mm" },
    mouthRadius: { type: "number", label: "Mouth radius", min: 0.35, max: 2.2, step: 0.05, default: 0.9, unit: "mm" }
  },
  animations: {
    bottleBreath: {
      label: "Bottle breath",
      duration: 5.8,
      loop: true,
      update({ progress, set }) {
        const wave = Math.sin(progress * Math.PI * 2);
        set("bulbScale", 1 + wave * 0.18);
        set("wallThickness", 2.2 + wave * 0.9);
        set("neckRadius", 3.1 + wave * 1.1);
      }
    }
  },
  bounds: ({ params }) => {
    const extent = 28 + Math.max(params.wallThickness, params.neckRadius) + Math.abs(params.bulbScale - 1) * 12;
    return [[-extent, -extent, -extent], [extent, extent, extent]];
  },
  render: ({ params }) => ({
    steps: 280,
    epsilon: Math.max(0.005, params.wallThickness * 0.0025),
    normalEpsilon: Math.max(0.045, params.wallThickness * 0.022)
  }),
  glsl: `
float implicitCadKleinEllipsoid(vec3 p, vec3 radii) {
  vec3 q = p / radii;
  return (length(q) - 1.0) * min(radii.x, min(radii.y, radii.z));
}

float sdf(vec3 p) {
  float bulb = implicitCadKleinEllipsoid(p - vec3(0.0, 0.0, -4.0 * bulbScale), vec3(15.5, 13.5, 17.5) * bulbScale);
${risingNeckDistanceSource}
${passingNeckDistanceSource}
  float mouth = implicitCadTorus(p - vec3(0.0, 0.0, 15.4), 4.2, mouthRadius);
  float immersedSolid = implicitCadUnionRound(bulb, implicitCadUnionRound(risingNeck, passingNeck, 1.5), 2.4);
  immersedSolid = implicitCadUnionRound(immersedSolid, mouth, 1.0);
  return implicitCadShell(immersedSolid, wallThickness, 0.0);
}


vec3 color(vec3 p, vec3 normal) {
  float neckAccent = smoothstep(4.0, 18.0, length(p.xz));
  vec3 amethyst = vec3(0.48, 0.34, 0.92);
  vec3 aqua = vec3(0.28, 0.86, 0.88);
  vec3 peach = vec3(0.98, 0.57, 0.38);
  return mix(mix(amethyst, aqua, neckAccent), peach, smoothstep(0.55, 0.98, abs(normal.y)) * 0.32);
}
`
};
