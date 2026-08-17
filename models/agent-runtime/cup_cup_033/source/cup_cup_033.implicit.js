export default {
  schema: "implicit.js/0.1.0",
  name: "sealed runtime cup conformance fixture",
  units: "unitless",
  bounds: [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
  render: { steps: 240, epsilon: 0.0015 },
  glsl: `
float sdf(vec3 p) {
  float body = implicit_cone_capped(
    p,
    vec3(0.0, 0.0, -0.47),
    vec3(0.0, 0.0, 0.30),
    0.245,
    0.315
  );
  float foot = implicit_cylinder_capped(
    p,
    vec3(0.0, 0.0, -0.49),
    vec3(0.0, 0.0, -0.455),
    0.258
  );
  float shoulder = implicit_cylinder_capped(
    p,
    vec3(0.0, 0.0, 0.10),
    vec3(0.0, 0.0, 0.17),
    0.323
  );
  float lidFlange = implicit_cylinder_capped(
    p,
    vec3(0.0, 0.0, 0.30),
    vec3(0.0, 0.0, 0.37),
    0.365
  );
  float lidTop = implicit_cone_capped(
    p,
    vec3(0.0, 0.0, 0.365),
    vec3(0.0, 0.0, 0.48),
    0.340,
    0.298
  );
  return min(min(min(body, foot), shoulder), min(lidFlange, lidTop));
}

vec3 color(vec3 p, vec3 normal) {
  float lid = smoothstep(0.27, 0.40, p.z);
  return mix(vec3(0.72, 0.56, 0.34), vec3(0.22, 0.18, 0.14), lid);
}
`,
};
