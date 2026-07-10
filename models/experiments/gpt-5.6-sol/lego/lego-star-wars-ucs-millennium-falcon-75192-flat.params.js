const TAU = Math.PI * 2;

// Measured from the grounded STEP package. The storm stays centered over the
// ship so the final identity transform returns every occurrence to its source
// assembly placement without storing a second pose.
const ASSEMBLY_CENTER = [
  23.28431470045358,
  18.89282682810125,
  117.30239092951913
];
const STORM_BASE_Z = 65;
const STORM_HEIGHT = 700;
const STORM_RADIUS_MIN = 175;
const STORM_RADIUS_MAX = 560;

let cachedSignature = "";
let cachedMotions = [];

function finite(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function clamp(value, min, max) {
  return Math.min(Math.max(finite(value, min), min), max);
}

function clamp01(value) {
  return clamp(value, 0, 1);
}

function smootherstep(value) {
  const t = clamp01(value);
  return t * t * t * (t * (t * 6 - 15) + 10);
}

function hashText(value) {
  let hash = 2166136261;
  const text = String(value || "");
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function random01(seed, salt) {
  let value = (seed ^ Math.imul(salt + 1, 0x9e3779b1)) >>> 0;
  value ^= value >>> 16;
  value = Math.imul(value, 0x21f0aaad);
  value ^= value >>> 15;
  value = Math.imul(value, 0x735a2d97);
  value ^= value >>> 15;
  return (value >>> 0) / 4294967295;
}

function recordCenter(record) {
  const direct = record?.partCenter;
  if (direct && [direct.x, direct.y, direct.z].every(Number.isFinite)) {
    return [direct.x, direct.y, direct.z];
  }
  const bounds = record?.partBounds;
  if (Array.isArray(bounds?.min) && Array.isArray(bounds?.max)) {
    return [0, 1, 2].map((axis) => (
      finite(bounds.min[axis]) + finite(bounds.max[axis])
    ) / 2);
  }
  return [...ASSEMBLY_CENTER];
}

function buildMotion(record) {
  const partId = String(record?.partId || "").trim();
  const seed = hashText(partId);
  const layer = random01(seed, 1);
  const radiusBias = Math.pow(layer, 0.68);
  const axis = [
    random01(seed, 7) * 2 - 1,
    random01(seed, 8) * 2 - 1,
    random01(seed, 9) * 1.4 - 0.2
  ];
  const axisLength = Math.hypot(axis[0], axis[1], axis[2]) || 1;

  return {
    partId,
    center: recordCenter(record),
    baseAngle: random01(seed, 2) * TAU,
    radius: STORM_RADIUS_MIN +
      (STORM_RADIUS_MAX - STORM_RADIUS_MIN) * radiusBias +
      (random01(seed, 3) - 0.5) * 54,
    height: STORM_BASE_Z + layer * STORM_HEIGHT,
    orbitTurns: 3.4 + random01(seed, 4) * 2.8,
    tumbleTurns: 1.2 + random01(seed, 5) * 3.8,
    launchDelay: random01(seed, 6) * 0.055,
    settleStart: 0.56 + random01(seed, 10) * 0.105,
    bobPhase: random01(seed, 11) * TAU,
    axis: axis.map((component) => component / axisLength),
    matrix: new Array(16).fill(0)
  };
}

function motionsFor(runtime) {
  const records = Array.isArray(runtime?.displayRecords)
    ? runtime.displayRecords
    : [];
  const firstId = String(records[0]?.partId || "");
  const lastId = String(records.at(-1)?.partId || "");
  const signature = `${records.length}:${firstId}:${lastId}`;
  if (signature !== cachedSignature) {
    cachedSignature = signature;
    cachedMotions = records
      .filter((record) => String(record?.partId || "").trim())
      .map(buildMotion);
  }
  return cachedMotions;
}

function writeDeltaMatrix(target, axis, angleRad, center, translate) {
  const [x, y, z] = axis;
  const cosine = Math.cos(angleRad);
  const sine = Math.sin(angleRad);
  const oneMinusCosine = 1 - cosine;

  const r00 = cosine + x * x * oneMinusCosine;
  const r01 = x * y * oneMinusCosine - z * sine;
  const r02 = x * z * oneMinusCosine + y * sine;
  const r10 = y * x * oneMinusCosine + z * sine;
  const r11 = cosine + y * y * oneMinusCosine;
  const r12 = y * z * oneMinusCosine - x * sine;
  const r20 = z * x * oneMinusCosine - y * sine;
  const r21 = z * y * oneMinusCosine + x * sine;
  const r22 = cosine + z * z * oneMinusCosine;

  const [cx, cy, cz] = center;
  const tx = cx - (r00 * cx + r01 * cy + r02 * cz) + translate[0];
  const ty = cy - (r10 * cx + r11 * cy + r12 * cz) + translate[1];
  const tz = cz - (r20 * cx + r21 * cy + r22 * cz) + translate[2];

  target[0] = r00;
  target[1] = r01;
  target[2] = r02;
  target[3] = tx;
  target[4] = r10;
  target[5] = r11;
  target[6] = r12;
  target[7] = ty;
  target[8] = r20;
  target[9] = r21;
  target[10] = r22;
  target[11] = tz;
  target[12] = 0;
  target[13] = 0;
  target[14] = 0;
  target[15] = 1;
}

export default {
  manifest: {
    schemaVersion: 1,
    step: {
      path: "models/lego/lego-star-wars-ucs-millennium-falcon-75192-flat.step"
    },
    label: "Millennium Falcon hurricane assembly",
    description: "Every LEGO occurrence releases into a broad, ordered vortex, then spirals and eases back to its exact STEP assembly placement.",
    units: {
      length: "mm",
      angle: "deg",
      time: "s"
    },
    parameters: {
      phase: {
        type: "number",
        label: "Assembly phase",
        description: "Scrubs the full assembled-to-hurricane-to-assembled sequence.",
        default: 0,
        min: 0,
        max: 1,
        step: 0.001
      },
      storm_size: {
        type: "number",
        label: "Storm size",
        description: "Scales the vortex radius and height while preserving the final assembly pose.",
        default: 1,
        min: 0.7,
        max: 1.35,
        step: 0.05
      }
    },
    animations: {
      hurricaneAssembly: {
        label: "Hurricane assembly",
        description: "The complete Falcon enters a tall spiral storm, tumbles in ordered streams, and floats cleanly back together.",
        duration: 18,
        loop: false,
        update({ progress, set }) {
          set("phase", clamp01(progress));
        }
      }
    }
  },

  setup({ cleanup }) {
    cleanup(() => {
      cachedSignature = "";
      cachedMotions = [];
    });
  },

  update({ params, runtime, effects }) {
    const phase = clamp01(params.phase);
    if (phase <= 0.000001 || phase >= 0.999999) {
      return;
    }

    const stormSize = clamp(params.storm_size, 0.7, 1.35);
    for (const motion of motionsFor(runtime)) {
      const launch = smootherstep((phase - motion.launchDelay) / 0.18);
      const settle = smootherstep((phase - motion.settleStart) / 0.285);
      const envelope = launch * (1 - settle);
      if (envelope <= 0.000001) {
        continue;
      }

      const orbitAngle = motion.baseAngle + motion.orbitTurns * TAU * phase;
      const stormRadius = motion.radius * stormSize;
      const stormCenter = [
        ASSEMBLY_CENTER[0] + Math.cos(orbitAngle) * stormRadius,
        ASSEMBLY_CENTER[1] + Math.sin(orbitAngle) * stormRadius,
        ASSEMBLY_CENTER[2] +
          (motion.height - ASSEMBLY_CENTER[2]) * stormSize +
          Math.sin(orbitAngle * 1.7 + motion.bobPhase) * 28 * stormSize
      ];
      const translate = [
        (stormCenter[0] - motion.center[0]) * envelope,
        (stormCenter[1] - motion.center[1]) * envelope,
        (stormCenter[2] - motion.center[2]) * envelope
      ];
      const tumbleAngle = (
        motion.tumbleTurns * TAU * phase +
        Math.sin(orbitAngle + motion.bobPhase) * 0.35
      ) * envelope;

      writeDeltaMatrix(
        motion.matrix,
        motion.axis,
        tumbleAngle,
        motion.center,
        translate
      );
      effects.transform({ partId: motion.partId }, { matrix: motion.matrix });
    }
  }
};
