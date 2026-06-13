export default {
  schema: "implicit.js/0.1.0",
  name: "Pelican on a Bicycle",
  description:
    "A cartoon pelican riding a classic diamond-frame bicycle. The crankAngle " +
    "parameter drives the cranks, level pedals, IK-bent legs, chain-geared " +
    "spoked wheels, and a gentle body bob; the Pedal animation loops one full " +
    "crank revolution seamlessly.",
  units: "mm",
  bounds: {
    min: [-134, -40, -3],
    max: [140, 40, 234],
  },
  params: {
    crankAngle: {
      type: "number",
      label: "Crank angle",
      min: 0,
      max: 360,
      default: 35,
      step: 1,
      unit: "deg",
    },
  },
  animations: {
    pedal: {
      label: "Pedal",
      duration: 2.4,
      update({ progress, set }) {
        set("crankAngle", progress * 360);
      },
    },
  },
  render: { steps: 256, epsilon: 0.003 },
  glsl: `
// ---------- shared helpers ----------
vec2 pb_rot2(vec2 v, float a) {
  float c = cos(a);
  float s = sin(a);
  return vec2(c * v.x - s * v.y, s * v.x + c * v.y);
}

float pb_ellipsoid(vec3 p, vec3 c, vec3 r) {
  vec3 q = (p - c) / r;
  float k0 = length(q);
  float k1 = length(q / r);
  return k0 * (k0 - 1.0) / max(k1, 0.000001);
}

float pb_crank() {
  // Negative = forward pedalling for a bike facing +X.
  return -crankAngle * 0.0174532925199433;
}

float pb_bob() {
  return sin(2.0 * pb_crank()) * 1.4;
}

// Pedal-spindle center (on the bike midplane) for side s (+1 right, -1 left).
vec3 pb_pedal(float s) {
  float a = pb_crank() + (s > 0.0 ? 0.0 : 3.14159265358979);
  return vec3(3.0 + 20.0 * cos(a), 0.0, 45.0 + 20.0 * sin(a));
}

vec3 pb_hip(float s) {
  return vec3(-6.0, s * 13.0, 136.0 + pb_bob());
}

vec3 pb_ankle(float s) {
  vec3 pa = pb_pedal(s);
  return vec3(pa.x, s * 18.0, pa.z + 5.2);
}

// Two-bone IK knee (equal bone lengths), knee pushed forward (+X).
vec3 pb_knee(vec3 hip, vec3 ankle) {
  float L = 54.0;
  vec3 d = ankle - hip;
  float dl = max(length(d), 0.0001);
  float dcl = min(dl, 2.0 * L - 2.0);
  vec3 n = d / dl;
  vec3 e = vec3(-n.z, 0.0, n.x);
  e = normalize(e) * (e.x < 0.0 ? -1.0 : 1.0);
  float h = sqrt(max(L * L - 0.25 * dcl * dcl, 0.0));
  return hip + n * (0.5 * dcl) + e * h;
}

// ---------- bicycle ----------
float pb_dTires(vec3 p) {
  vec3 qr = p - vec3(-72.0, 0.0, 57.0);
  vec3 qf = p - vec3(78.0, 0.0, 57.0);
  float rear = implicit_torus(qr.xzy, 50.0, 7.0);
  float front = implicit_torus(qf.xzy, 50.0, 7.0);
  return min(rear, front);
}

float pb_wheelSilver(vec3 q, float spin) {
  float rim = implicit_torus(q.xzy, 44.0, 2.6);
  float hub = implicit_cylinder_capped(q, vec3(0.0, -9.0, 0.0), vec3(0.0, 9.0, 0.0), 4.5);
  vec2 w = pb_rot2(vec2(q.x, q.z), spin);
  float sect = 0.78539816339745; // 8 spokes
  float ang = atan(w.y, w.x);
  float snapped = floor(ang / sect + 0.5) * sect;
  vec2 r = pb_rot2(w, -snapped);
  float spokes = length(vec3(r.x - clamp(r.x, 8.0, 44.0), q.y, r.y)) - 1.1;
  return min(rim, min(hub, spokes));
}

float pb_dSilver(vec3 p) {
  // 1.25 wheel revs per crank rev: seamless loop (450 deg = 10 x 45 deg spoke
  // symmetry) and reads as forward motion even in low-fps GIF captures.
  // Negated because rotating the sample space by +a shows geometry at -a.
  float spin = -1.25 * pb_crank();
  float d = pb_wheelSilver(p - vec3(-72.0, 0.0, 57.0), spin);
  d = min(d, pb_wheelSilver(p - vec3(78.0, 0.0, 57.0), spin));
  // seatpost
  d = min(d, implicit_capsule(p, vec3(-24.0, 0.0, 108.0), vec3(-26.0, 0.0, 128.0), 3.4));
  // steerer, stem, handlebar
  d = min(d, implicit_capsule(p, vec3(50.0, 0.0, 110.0), vec3(42.4, 0.0, 124.5), 3.4));
  d = min(d, implicit_capsule(p, vec3(42.4, 0.0, 124.5), vec3(50.0, 0.0, 127.0), 3.2));
  d = min(d, implicit_capsule(p, vec3(50.0, -30.0, 127.0), vec3(50.0, 30.0, 127.0), 2.6));
  // crank arms + pedal spindles
  for (int i = 0; i < 2; i++) {
    float s = 1.0 - 2.0 * float(i);
    vec3 pa = pb_pedal(s);
    d = min(d, implicit_cone_capsule(p, vec3(3.0, s * 11.0, 45.0), pa + vec3(0.0, s * 14.0, 0.0), 3.2, 2.7));
    d = min(d, implicit_capsule(p, pa + vec3(0.0, s * 13.0, 0.0), pa + vec3(0.0, s * 23.0, 0.0), 1.7));
  }
  return d;
}

float pb_dFrame(vec3 p) {
  vec3 BB = vec3(3.0, 0.0, 45.0);
  vec3 S = vec3(-24.0, 0.0, 108.0);
  vec3 H1 = vec3(50.0, 0.0, 110.0);
  vec3 H2 = vec3(64.0, 0.0, 83.5);
  float d = implicit_capsule(p, S, H1, 4.0);                 // top tube
  d = min(d, implicit_capsule(p, BB, H2, 4.6));              // down tube
  d = min(d, implicit_capsule(p, BB, S, 4.0));               // seat tube
  d = min(d, implicit_capsule(p, H1, H2, 5.0));              // head tube
  for (int i = 0; i < 2; i++) {
    float s = 1.0 - 2.0 * float(i);
    vec3 axleR = vec3(-72.0, s * 7.0, 57.0);
    d = min(d, implicit_capsule(p, vec3(3.0, s * 5.0, 45.0), axleR, 3.0));    // chainstay
    d = min(d, implicit_capsule(p, vec3(-24.0, s * 3.5, 108.0), axleR, 2.8)); // seatstay
    d = min(d, implicit_capsule(p, vec3(64.0, s * 3.0, 83.5), vec3(78.0, s * 7.0, 57.0), 3.2)); // fork
  }
  return d;
}

float pb_dDark(vec3 p) {
  // chainring, rear cog, chain runs, pedal platforms
  float d = implicit_cylinder_capped(p, vec3(3.0, 9.5, 45.0), vec3(3.0, 13.0, 45.0), 15.0);
  d = min(d, implicit_cylinder_capped(p, vec3(-72.0, 9.5, 57.0), vec3(-72.0, 12.5, 57.0), 7.0));
  d = min(d, implicit_capsule(p, vec3(1.0, 11.2, 60.5), vec3(-72.0, 11.2, 64.5), 1.7));
  d = min(d, implicit_capsule(p, vec3(1.0, 11.2, 29.5), vec3(-72.0, 11.2, 50.5), 1.7));
  for (int i = 0; i < 2; i++) {
    float s = 1.0 - 2.0 * float(i);
    vec3 pa = pb_pedal(s);
    d = min(d, implicit_box_centered(p, vec3(15.0, 6.0, 3.0), pa + vec3(0.0, s * 18.0, 0.0)) - 1.0);
  }
  return d;
}

float pb_dLeather(vec3 p) {
  // saddle
  float d = pb_ellipsoid(p, vec3(-31.0, 0.0, 130.5), vec3(8.5, 7.0, 3.0));
  d = implicit_union_round(d, pb_ellipsoid(p, vec3(-17.0, 0.0, 130.2), vec3(9.0, 3.4, 2.4)), 2.0);
  // grips
  for (int i = 0; i < 2; i++) {
    float s = 1.0 - 2.0 * float(i);
    d = min(d, implicit_capsule(p, vec3(50.0, s * 19.0, 127.0), vec3(50.0, s * 29.5, 127.0), 4.2));
  }
  return d;
}

// ---------- pelican ----------
float pb_dPlumage(vec3 p) {
  float bob = pb_bob();
  // body + breast
  float d = pb_ellipsoid(p, vec3(-18.0, 0.0, 152.0 + bob), vec3(36.0, 24.0, 26.0));
  d = implicit_union_round(d, implicit_sphere(p, vec3(6.0, 0.0, 143.0 + bob), 17.0), 10.0);
  // tail (flattened in y)
  vec3 tq = vec3(p.x, p.y * 1.6, p.z);
  float tail = implicit_cone_capsule(tq, vec3(-46.0, 0.0, 158.0 + bob), vec3(-74.0, 0.0, 174.0 + bob), 11.0, 3.0) * 0.62;
  d = implicit_union_round(d, tail, 5.0);
  // neck (S-curve) + head + crest
  float neck = implicit_cone_capsule(p, vec3(6.0, 0.0, 166.0 + bob), vec3(20.0, 0.0, 186.0 + bob), 9.5, 8.0);
  neck = implicit_union_round(neck, implicit_cone_capsule(p, vec3(20.0, 0.0, 186.0 + bob), vec3(24.0, 0.0, 203.0 + bob), 8.0, 7.2), 4.0);
  neck = implicit_union_round(neck, implicit_cone_capsule(p, vec3(24.0, 0.0, 203.0 + bob), vec3(33.0, 0.0, 213.0 + bob), 7.2, 7.0), 4.0);
  float head = implicit_sphere(p, vec3(37.0, 0.0, 215.0 + bob), 11.5);
  head = implicit_union_round(head, implicit_sphere(p, vec3(29.5, 0.0, 221.0 + bob), 6.5), 4.0);
  neck = implicit_union_round(neck, head, 4.0);
  d = implicit_union_round(d, neck, 7.0);
  for (int i = 0; i < 2; i++) {
    float s = 1.0 - 2.0 * float(i);
    // folded wing bulge on the flank
    d = implicit_union_round(d, pb_ellipsoid(p, vec3(-24.0, s * 19.0, 158.0 + bob), vec3(26.0, 9.0, 17.0)), 6.0);
    // wing-arm reaching to the handlebar grip
    float arm = implicit_cone_capsule(p, vec3(2.0, s * 18.0, 160.0 + bob), vec3(28.0, s * 26.0, 138.0), 8.0, 5.5);
    arm = implicit_union_round(arm, implicit_cone_capsule(p, vec3(28.0, s * 26.0, 138.0), vec3(48.0, s * 27.5, 128.5), 5.5, 4.4), 3.5);
    arm = implicit_union_round(arm, implicit_sphere(p, vec3(52.0, s * 28.0, 127.5), 6.0), 3.5);
    d = implicit_union_round(d, arm, 5.0);
    // feathered thigh
    vec3 hip = pb_hip(s);
    vec3 ankle = pb_ankle(s);
    vec3 knee = pb_knee(hip, ankle);
    d = implicit_union_round(d, implicit_cone_capsule(p, hip, knee, 7.5, 4.6), 6.0);
  }
  return d;
}

float pb_dBill(vec3 p) {
  float bob = pb_bob();
  // upper mandible (flattened in z) + tip hook
  vec3 bq = vec3(p.x, p.y, (p.z - 214.5 - bob) * 1.7 + 214.5 + bob);
  float d = implicit_cone_capsule(bq, vec3(42.0, 0.0, 214.5 + bob), vec3(92.0, 0.0, 203.0 + bob), 4.0, 2.4) * 0.58;
  d = implicit_union_round(d, implicit_sphere(p, vec3(92.5, 0.0, 200.5 + bob), 2.6), 1.5);
  // throat pouch
  float pouch = implicit_cone_capsule(p, vec3(45.0, 0.0, 209.0 + bob), vec3(89.0, 0.0, 200.5 + bob), 2.4, 1.6);
  pouch = implicit_union_round(pouch, pb_ellipsoid(p, vec3(60.0, 0.0, 198.5 + bob), vec3(23.0, 6.5, 10.0)), 8.0);
  pouch = implicit_union_round(pouch, implicit_cone_capsule(p, vec3(28.0, 0.0, 196.0 + bob), vec3(50.0, 0.0, 200.0 + bob), 6.0, 5.0), 7.0);
  return implicit_union_round(d, pouch, 2.5);
}

float pb_dLegs(vec3 p) {
  float d = 1.0e6;
  for (int i = 0; i < 2; i++) {
    float s = 1.0 - 2.0 * float(i);
    vec3 hip = pb_hip(s);
    vec3 ankle = pb_ankle(s);
    vec3 knee = pb_knee(hip, ankle);
    // bare tarsus
    d = min(d, implicit_cone_capsule(p, knee, ankle, 3.4, 2.4));
    // webbed foot (flattened in z)
    vec3 fq = vec3(p.x, p.y, (p.z - ankle.z) * 2.2 + ankle.z);
    d = min(d, implicit_cone_capsule(fq, ankle + vec3(-5.0, 0.0, -1.5), ankle + vec3(12.0, 0.0, -3.8), 2.5, 5.5) * 0.45);
  }
  return d;
}

// ---------- scene ----------
float sdf(vec3 p) {
  float d = pb_dTires(p);
  d = min(d, pb_dSilver(p));
  d = min(d, pb_dFrame(p));
  d = min(d, pb_dDark(p));
  d = min(d, pb_dLeather(p));
  d = min(d, pb_dPlumage(p));
  d = min(d, pb_dBill(p));
  d = min(d, pb_dLegs(p));
  return d;
}

vec3 color(vec3 p, vec3 normal) {
  float bob = pb_bob();
  float dT = pb_dTires(p);
  float dS = pb_dSilver(p);
  float dF = pb_dFrame(p);
  float dD = pb_dDark(p);
  float dL = pb_dLeather(p);
  float dP = pb_dPlumage(p);
  float dB = pb_dBill(p);
  float dG = pb_dLegs(p);
  float best = min(min(min(dT, dS), min(dF, dD)), min(min(dL, dP), min(dB, dG)));

  float up = max(normal.z, 0.0);

  if (best == dP) {
    // warm white plumage
    vec3 col = vec3(0.985, 0.973, 0.95);
    col = mix(col, vec3(0.98, 0.95, 0.875), 0.4 * smoothstep(170.0, 140.0, p.z));
    // folded-wing feather streaks
    float wmask = smoothstep(12.0, 16.0, abs(p.y))
      * (1.0 - smoothstep(176.0, 182.0, p.z))
      * smoothstep(138.0, 146.0, p.z)
      * (1.0 - smoothstep(2.0, 10.0, p.x))
      * smoothstep(-54.0, -46.0, p.x);
    col *= 1.0 - 0.07 * wmask * (0.5 + 0.5 * sin(p.x * 0.5 + p.z * 0.15));
    // grey wing tips at the grips
    float tip = smoothstep(40.0, 50.0, p.x) * smoothstep(17.0, 23.0, abs(p.y)) * (1.0 - smoothstep(150.0, 175.0, p.z));
    col = mix(col, vec3(0.36, 0.37, 0.40), 0.85 * tip);
    // grey tail tips
    float ttip = (1.0 - smoothstep(-72.0, -56.0, p.x)) * smoothstep(146.0, 160.0, p.z);
    col = mix(col, vec3(0.46, 0.47, 0.50), 0.7 * ttip);
    // eyes
    for (int i = 0; i < 2; i++) {
      float s = 1.0 - 2.0 * float(i);
      vec3 E = vec3(43.6, s * 8.6, 219.0 + bob);
      float de = length(p - E);
      col = mix(col, vec3(0.94, 0.87, 0.60), smoothstep(4.2, 3.4, de));
      col = mix(col, vec3(0.06, 0.05, 0.05), smoothstep(2.6, 2.1, de));
      float dc = length(p - (E + vec3(0.8, s * 0.6, 1.1)));
      col = mix(col, vec3(1.0), smoothstep(1.0, 0.5, dc));
    }
    return col * (0.9 + 0.1 * up);
  }
  if (best == dB) {
    vec3 col = mix(vec3(0.95, 0.62, 0.16), vec3(0.99, 0.84, 0.33), smoothstep(40.0, 94.0, p.x));
    col = mix(col, vec3(0.97, 0.52, 0.30), 0.65 * smoothstep(206.0, 193.0, p.z));
    col = mix(col, vec3(0.75, 0.40, 0.12), 0.5 * smoothstep(88.0, 93.0, p.x));
    // painted mouth seam between upper bill and pouch
    float lz = 208.5 - (p.x - 45.0) * 0.16 + bob;
    float seam = smoothstep(1.1, 0.4, abs(p.z - lz)) * smoothstep(45.0, 52.0, p.x) * (1.0 - smoothstep(86.0, 91.0, p.x));
    col = mix(col, vec3(0.62, 0.33, 0.10), 0.6 * seam);
    return col * (0.9 + 0.1 * up);
  }
  if (best == dG) {
    return vec3(0.95, 0.55, 0.16) * (0.87 + 0.13 * up);
  }
  if (best == dF) {
    return vec3(0.72, 0.10, 0.14) * (0.88 + 0.12 * up);
  }
  if (best == dT) {
    float ax = (p.x > 3.0) ? 78.0 : -72.0;
    float rr = length(vec2(p.x - ax, p.z - 57.0));
    return mix(vec3(0.62, 0.50, 0.34), vec3(0.13, 0.13, 0.14), smoothstep(51.5, 53.5, rr));
  }
  if (best == dL) {
    return vec3(0.46, 0.28, 0.14) * (0.88 + 0.12 * up);
  }
  if (best == dD) {
    return vec3(0.15, 0.16, 0.18);
  }
  return vec3(0.78, 0.80, 0.84) * (0.82 + 0.18 * up);
}
`,
};
