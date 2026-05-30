function finiteNumber(value, fallback = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function clamp01(value) {
  return Math.min(Math.max(finiteNumber(value), 0), 1);
}

function hexToRgb01(hex, fallback = "#d4d4d8") {
  const raw = String(hex || fallback).trim();
  const value = /^#(?:[0-9a-fA-F]{3}){1,2}$/.test(raw) ? raw : fallback;
  const expanded = value.length === 4
    ? `${value[1]}${value[1]}${value[2]}${value[2]}${value[3]}${value[3]}`
    : value.slice(1);
  return [
    parseInt(expanded.slice(0, 2), 16) / 255,
    parseInt(expanded.slice(2, 4), 16) / 255,
    parseInt(expanded.slice(4, 6), 16) / 255,
  ];
}

function hexTo3mfDisplayColor(hex, fallback = "#d4d4d8") {
  const toChannel = (component) => Math.round(clamp01(component) * 255)
    .toString(16)
    .padStart(2, "0")
    .toUpperCase();
  const rgb = hexToRgb01(hex, fallback);
  return `#${toChannel(rgb[0])}${toChannel(rgb[1])}${toChannel(rgb[2])}FF`;
}

function triangleNormal(positions, offset) {
  const ax = positions[offset];
  const ay = positions[offset + 1];
  const az = positions[offset + 2];
  const bx = positions[offset + 3];
  const by = positions[offset + 4];
  const bz = positions[offset + 5];
  const cx = positions[offset + 6];
  const cy = positions[offset + 7];
  const cz = positions[offset + 8];
  const ux = bx - ax;
  const uy = by - ay;
  const uz = bz - az;
  const vx = cx - ax;
  const vy = cy - ay;
  const vz = cz - az;
  const nx = uy * vz - uz * vy;
  const ny = uz * vx - ux * vz;
  const nz = ux * vy - uy * vx;
  const length = Math.hypot(nx, ny, nz);
  return length > 1e-12 ? [nx / length, ny / length, nz / length] : [0, 0, 1];
}

function sanitizeName(value, fallback = "implicit-cad") {
  return String(value || fallback).trim().replace(/[\x00-\x1f<>:"/\\|?*]+/g, "-") || fallback;
}

function align4Buffer(buffer, fill = 0x20) {
  const padding = (4 - (buffer.length % 4)) % 4;
  return padding ? Buffer.concat([buffer, Buffer.alloc(padding, fill)]) : buffer;
}

function boundsForPositions(positions) {
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (let index = 0; index < positions.length; index += 3) {
    min[0] = Math.min(min[0], positions[index]);
    min[1] = Math.min(min[1], positions[index + 1]);
    min[2] = Math.min(min[2], positions[index + 2]);
    max[0] = Math.max(max[0], positions[index]);
    max[1] = Math.max(max[1], positions[index + 1]);
    max[2] = Math.max(max[2], positions[index + 2]);
  }
  return {
    min: min.map((value) => Number.isFinite(value) ? value : 0),
    max: max.map((value) => Number.isFinite(value) ? value : 0),
  };
}

export function meshToBinaryStl(mesh, { name = "implicit-cad" } = {}) {
  const positions = mesh.positions || new Float32Array();
  const triangleCount = Math.floor(positions.length / 9);
  const buffer = Buffer.alloc(84 + triangleCount * 50);
  buffer.write(`implicit-cad ${sanitizeName(name)}`.slice(0, 80), 0, "ascii");
  buffer.writeUInt32LE(triangleCount, 80);
  let offset = 84;
  for (let triangle = 0; triangle < triangleCount; triangle += 1) {
    const positionOffset = triangle * 9;
    const normal = triangleNormal(positions, positionOffset);
    for (const component of normal) {
      buffer.writeFloatLE(component, offset);
      offset += 4;
    }
    for (let vertex = 0; vertex < 9; vertex += 1) {
      buffer.writeFloatLE(positions[positionOffset + vertex], offset);
      offset += 4;
    }
    buffer.writeUInt16LE(0, offset);
    offset += 2;
  }
  return buffer;
}

export function meshToGlb(mesh, { name = "Implicit CAD", color = "#d4d4d8" } = {}) {
  const positions = mesh.positions || new Float32Array();
  const normals = mesh.normals && mesh.normals.length === positions.length
    ? mesh.normals
    : new Float32Array(positions.length);
  const vertexCount = Math.floor(positions.length / 3);
  const positionBuffer = Buffer.from(positions.buffer, positions.byteOffset, positions.byteLength);
  const normalBuffer = Buffer.from(normals.buffer, normals.byteOffset, normals.byteLength);
  const binaryChunk = align4Buffer(Buffer.concat([positionBuffer, normalBuffer]), 0);
  const positionBounds = boundsForPositions(positions);
  const baseColor = hexToRgb01(color);
  const gltf = {
    asset: { version: "2.0", generator: "implicitjs exporter" },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [{
      mesh: 0,
      name: sanitizeName(name, "Implicit CAD"),
      extras: {
        cadOccurrenceId: "implicit-cad:0",
        cadSourceKind: "implicit-cad",
        cadUnits: "mm",
      },
    }],
    meshes: [{
      primitives: [{
        attributes: { POSITION: 0, NORMAL: 1 },
        material: 0,
        mode: 4,
      }],
    }],
    materials: [{
      name: "Implicit material",
      doubleSided: true,
      extras: { cadSourceColor: true },
      pbrMetallicRoughness: {
        baseColorFactor: [...baseColor.map(clamp01), 1],
        roughnessFactor: 0.72,
        metallicFactor: 0.02,
      },
    }],
    buffers: [{ byteLength: binaryChunk.length }],
    bufferViews: [
      { buffer: 0, byteOffset: 0, byteLength: positionBuffer.length, target: 34962 },
      { buffer: 0, byteOffset: positionBuffer.length, byteLength: normalBuffer.length, target: 34962 },
    ],
    accessors: [
      {
        bufferView: 0,
        byteOffset: 0,
        componentType: 5126,
        count: vertexCount,
        type: "VEC3",
        min: positionBounds.min,
        max: positionBounds.max,
      },
      {
        bufferView: 1,
        byteOffset: 0,
        componentType: 5126,
        count: vertexCount,
        type: "VEC3",
      },
    ],
  };
  const jsonChunk = align4Buffer(Buffer.from(JSON.stringify(gltf), "utf-8"), 0x20);
  const totalLength = 12 + 8 + jsonChunk.length + 8 + binaryChunk.length;
  const header = Buffer.alloc(12);
  header.writeUInt32LE(0x46546c67, 0);
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(totalLength, 8);
  const jsonHeader = Buffer.alloc(8);
  jsonHeader.writeUInt32LE(jsonChunk.length, 0);
  jsonHeader.writeUInt32LE(0x4e4f534a, 4);
  const binHeader = Buffer.alloc(8);
  binHeader.writeUInt32LE(binaryChunk.length, 0);
  binHeader.writeUInt32LE(0x004e4942, 4);
  return Buffer.concat([header, jsonHeader, jsonChunk, binHeader, binaryChunk], totalLength);
}

function xmlEscape(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function crc32(buffer) {
  let table = crc32.table;
  if (!table) {
    table = new Uint32Array(256);
    for (let index = 0; index < 256; index += 1) {
      let c = index;
      for (let bit = 0; bit < 8; bit += 1) {
        c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      }
      table[index] = c >>> 0;
    }
    crc32.table = table;
  }
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc = table[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function dosDateTime(date = new Date()) {
  const year = Math.max(date.getFullYear(), 1980);
  const dosTime = (date.getHours() << 11) | (date.getMinutes() << 5) | Math.floor(date.getSeconds() / 2);
  const dosDate = ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate();
  return { dosTime, dosDate };
}

function zipStore(files) {
  const localParts = [];
  const centralParts = [];
  let offset = 0;
  const { dosTime, dosDate } = dosDateTime();
  for (const file of files) {
    const nameBuffer = Buffer.from(file.name, "utf-8");
    const body = Buffer.isBuffer(file.body) ? file.body : Buffer.from(String(file.body || ""), "utf-8");
    const crc = crc32(body);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0, 6);
    local.writeUInt16LE(0, 8);
    local.writeUInt16LE(dosTime, 10);
    local.writeUInt16LE(dosDate, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(body.length, 18);
    local.writeUInt32LE(body.length, 22);
    local.writeUInt16LE(nameBuffer.length, 26);
    local.writeUInt16LE(0, 28);
    localParts.push(local, nameBuffer, body);

    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0, 8);
    central.writeUInt16LE(0, 10);
    central.writeUInt16LE(dosTime, 12);
    central.writeUInt16LE(dosDate, 14);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(body.length, 20);
    central.writeUInt32LE(body.length, 24);
    central.writeUInt16LE(nameBuffer.length, 28);
    central.writeUInt16LE(0, 30);
    central.writeUInt16LE(0, 32);
    central.writeUInt16LE(0, 34);
    central.writeUInt16LE(0, 36);
    central.writeUInt32LE(0, 38);
    central.writeUInt32LE(offset, 42);
    centralParts.push(central, nameBuffer);
    offset += local.length + nameBuffer.length + body.length;
  }
  const centralStart = offset;
  const centralDirectory = Buffer.concat(centralParts);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(0, 4);
  end.writeUInt16LE(0, 6);
  end.writeUInt16LE(files.length, 8);
  end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(centralDirectory.length, 12);
  end.writeUInt32LE(centralStart, 16);
  end.writeUInt16LE(0, 20);
  return Buffer.concat([...localParts, centralDirectory, end]);
}

export function meshTo3mf(mesh, { name = "Implicit CAD", color = "#d4d4d8" } = {}) {
  const positions = mesh.positions || new Float32Array();
  const vertexCount = Math.floor(positions.length / 3);
  const triangleCount = Math.floor(positions.length / 9);
  const displayColor = hexTo3mfDisplayColor(color);
  const vertices = [];
  for (let index = 0; index < vertexCount; index += 1) {
    const offset = index * 3;
    vertices.push(`<vertex x="${positions[offset]}" y="${positions[offset + 1]}" z="${positions[offset + 2]}"/>`);
  }
  const triangles = [];
  for (let index = 0; index < triangleCount; index += 1) {
    const base = index * 3;
    triangles.push(`<triangle v1="${base}" v2="${base + 1}" v3="${base + 2}" pid="2" p1="0"/>`);
  }
  const modelXml = `<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <metadata name="Title">${xmlEscape(name)}</metadata>
  <resources>
    <basematerials id="2">
      <base name="Implicit material" displaycolor="${displayColor}"/>
    </basematerials>
    <object id="1" type="model" name="${xmlEscape(name)}">
      <mesh>
        <vertices>
          ${vertices.join("\n          ")}
        </vertices>
        <triangles>
          ${triangles.join("\n          ")}
        </triangles>
      </mesh>
    </object>
  </resources>
  <build>
    <item objectid="1"/>
  </build>
</model>`;
  const contentTypes = `<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>`;
  const relationships = `<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>`;
  return zipStore([
    { name: "[Content_Types].xml", body: contentTypes },
    { name: "_rels/.rels", body: relationships },
    { name: "3D/3dmodel.model", body: modelXml },
  ]);
}

export function meshToFormat(mesh, format, options = {}) {
  const normalized = String(format || "").trim().toLowerCase().replace(/^\./, "");
  if (normalized === "stl") {
    return {
      body: meshToBinaryStl(mesh, options),
      contentType: "model/stl",
      extension: ".stl",
    };
  }
  if (normalized === "glb" || normalized === "gltf") {
    return {
      body: meshToGlb(mesh, options),
      contentType: "model/gltf-binary",
      extension: ".glb",
    };
  }
  if (normalized === "3mf") {
    return {
      body: meshTo3mf(mesh, options),
      contentType: "model/3mf",
      extension: ".3mf",
    };
  }
  throw new Error(`Unsupported implicit CAD export format: ${format || "(missing)"}`);
}
