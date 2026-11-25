// Formatação de valores de 16 bits e utilidades de DOM compartilhadas pelos módulos.

export const REGISTER_NAMES = [
  "PC", "AC", "SP", "IR", "TIR", "0", "+1", "-1",
  "AMASK", "SMASK", "A", "B", "C", "D", "E", "F",
];
export const CONSTANT_REGISTERS = new Set(["0", "+1", "-1", "AMASK", "SMASK"]);

/** Hexadecimal com zeros à esquerda: hex(9, 3) → "009". */
export function hex(value, digits = 4) {
  if (value === null || value === undefined) return "—";
  return (value >>> 0).toString(16).toUpperCase().padStart(digits, "0");
}

/** Valor de 16 bits interpretado em complemento de 2. */
export function signed16(value) {
  const v = value & 0xffff;
  return v & 0x8000 ? v - 0x10000 : v;
}

export function bin(value, width) {
  return (value >>> 0).toString(2).padStart(width, "0");
}

/** Número em pt-BR: 1234 → "1.234". */
export function num(value) {
  return Number(value ?? 0).toLocaleString("pt-BR");
}

export function percent(fraction) {
  return `${(fraction * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`;
}

export function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

/** Cria um elemento (HTML ou SVG) com atributos e o anexa a ``parent``. */
export function el(tag, attrs = {}, parent = null, svg = false) {
  const node = svg
    ? document.createElementNS("http://www.w3.org/2000/svg", tag)
    : document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "text") node.textContent = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  if (parent) parent.appendChild(node);
  return node;
}

/** Rola ``container`` só o necessário para mostrar ``child`` (sem mexer na página). */
export function scrollIntoContainer(container, child, margin = 24) {
  if (!container || !child) return;
  const top = child.offsetTop;
  const bottom = top + child.offsetHeight;
  if (top - margin < container.scrollTop) {
    container.scrollTop = Math.max(0, top - margin);
  } else if (bottom + margin > container.scrollTop + container.clientHeight) {
    container.scrollTop = bottom + margin - container.clientHeight;
  }
}
