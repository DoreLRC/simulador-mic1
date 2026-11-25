// Microprograma MAL (com a microinstrução em execução e a próxima) e os campos do MIR.

import { REGISTER_NAMES, bin, el, escapeHtml, scrollIntoContainer } from "./format.js";

export const MIR_FIELDS = [
  ["amux", 1, "AMUX"],
  ["cond", 2, "COND"],
  ["alu", 2, "ULA"],
  ["sh", 2, "DESL"],
  ["mbr", 1, "MBR"],
  ["mar", 1, "MAR"],
  ["rd", 1, "RD"],
  ["wr", 1, "WR"],
  ["enc", 1, "ENC"],
  ["c", 4, "C"],
  ["b", 4, "B"],
  ["a", 4, "A"],
  ["addr", 8, "ADDR"],
];

const ALU_TEXT = ["A + B", "A AND B", "A", "NOT A"];
const SHIFT_TEXT = ["sem deslocamento", "rshift (→ 1 bit)", "lshift (← 1 bit)", "inválido"];
const COND_TEXT = ["sem desvio", "desvia se N", "desvia se Z", "desvia sempre"];

/** Significado de cada campo, em português, para a tabela e as dicas do diagrama. */
export function fieldMeaning(name, value, fields) {
  switch (name) {
    case "amux":
      return value ? "ULA ← MBR" : "ULA ← Latch A";
    case "cond":
      return COND_TEXT[value];
    case "alu":
      return ALU_TEXT[value];
    case "sh":
      return SHIFT_TEXT[value];
    case "mbr":
      return value ? "MBR ← deslocador" : "—";
    case "mar":
      return value ? "MAR ← Latch B" : "—";
    case "rd":
      return value ? "leitura na memória" : "—";
    case "wr":
      return value ? "escrita na memória" : "—";
    case "enc":
      return value ? "grava o barramento C" : "C desligado";
    case "c":
      return fields.enc ? `C → ${REGISTER_NAMES[value]}` : `(${REGISTER_NAMES[value]}, ignorado)`;
    case "b":
      return `B ← ${REGISTER_NAMES[value]}`;
    case "a":
      return `A ← ${REGISTER_NAMES[value]}`;
    case "addr":
      return `endereço ${value}`;
    default:
      return "";
  }
}

export class MicrocodeView {
  constructor(list, info, fieldsRoot) {
    this.list = list;
    this.info = info;
    this.fieldsRoot = fieldsRoot;
    this.items = new Map();
    this.buildFields();
  }

  setProgram(microprogram) {
    this.items = new Map();
    const fragment = document.createDocumentFragment();
    for (const step of microprogram.steps) {
      const item = el("li", { class: "mal__item", "data-addr": step.address }, fragment);
      item.innerHTML =
        `<span class="mal__addr">${step.address}</span>` +
        `<span class="mal__src">${escapeHtml(step.mal)}</span>` +
        (step.comment ? `<span class="mal__cmt">${escapeHtml(step.comment)}</span>` : "");
      this.items.set(step.address, item);
    }
    this.list.replaceChildren(fragment);
    const mode = microprogram.mode === "case" ? "case" : "original";
    this.info.textContent = `${mode} · ${microprogram.size} µinstruções · ${microprogram.used_bits.toLocaleString("pt-BR")} bits · busca em ${microprogram.fetch_address}`;
  }

  buildFields() {
    this.cells = {};
    const fragment = document.createDocumentFragment();
    for (const [name, width, label] of MIR_FIELDS) {
      const row = el("div", { class: "mir-field", "data-field": name }, fragment);
      el("span", { class: "mir-field__name", text: label }, row);
      const bits = el("span", { class: "mir-field__bits", text: "0".repeat(width) }, row);
      const meaning = el("span", { class: "mir-field__meaning", text: "—" }, row);
      this.cells[name] = { row, bits, meaning, width };
    }
    this.fieldsRoot.replaceChildren(fragment);
  }

  update(snapshot) {
    const mir = snapshot?.mir;
    const current = mir?.address ?? null;
    const next = snapshot?.mpc ?? null;
    for (const node of this.list.querySelectorAll(".is-current, .is-next")) {
      node.classList.remove("is-current", "is-next");
    }
    const currentItem = current === null ? null : this.items.get(current);
    currentItem?.classList.add("is-current");
    const nextItem = this.items.get(next);
    if (nextItem && next !== current) nextItem.classList.add("is-next");
    scrollIntoContainer(this.list, currentItem ?? nextItem);

    for (const [name, cell] of Object.entries(this.cells)) {
      const value = mir ? mir.fields[name] : 0;
      cell.bits.textContent = bin(value, cell.width);
      cell.meaning.textContent = mir ? fieldMeaning(name, value, mir.fields) : "—";
      cell.row.classList.toggle("is-set", Boolean(mir) && value !== 0);
    }
  }
}
