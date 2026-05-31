export const GLSL_PLACEHOLDER = "IMPLICIT_GLSL";

function hasOddBackslashRun(source, index) {
  let count = 0;
  for (let cursor = index - 1; cursor >= 0 && source[cursor] === "\\"; cursor -= 1) {
    count += 1;
  }
  return count % 2 === 1;
}

function findGlslTemplate(source) {
  const text = String(source || "");
  const match = /glsl\s*:\s*`/u.exec(text);
  if (!match) {
    return null;
  }
  const openIndex = match.index + match[0].length - 1;
  for (let index = openIndex + 1; index < text.length; index += 1) {
    if (text[index] === "`" && !hasOddBackslashRun(text, index)) {
      return {
        openIndex,
        closeIndex: index,
        valueStart: openIndex,
        valueEnd: index + 1
      };
    }
  }
  return null;
}

function escapeTemplateLiteral(value) {
  return `\`${String(value || "")
    .replace(/\\/g, "\\\\")
    .replace(/`/g, "\\`")
    .replace(/\$\{/g, "\\${")}\``;
}

export function splitImplicitSource(source) {
  const text = String(source || "");
  const range = findGlslTemplate(text);
  if (!range) {
    return {
      jsCode: text,
      glslCode: "",
      split: false
    };
  }
  return {
    jsCode: `${text.slice(0, range.valueStart)}${GLSL_PLACEHOLDER}${text.slice(range.valueEnd)}`,
    glslCode: text.slice(range.openIndex + 1, range.closeIndex),
    split: true
  };
}

export function combineImplicitSource(jsCode, glslCode) {
  const js = String(jsCode || "");
  if (!js.includes(GLSL_PLACEHOLDER)) {
    return js;
  }
  return js.replace(GLSL_PLACEHOLDER, escapeTemplateLiteral(glslCode));
}
