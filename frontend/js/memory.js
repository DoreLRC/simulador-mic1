// Inspetor da memória principal (4096 palavras) com rolagem virtual: só as linhas visíveis
// são desenhadas. Destaca leituras/escritas recentes, a instrução em execução e o SP.

import { escapeHtml, hex, signed16 } from "./format.js";

const ROW = 22; // px, igual a --mem-row no CSS
const WORDS = 4096;
const FADE_STEPS = 3; // por quantos passos uma posição acessada continua destacada

export class MemoryView {
  constructor(root, { onJump } = {}) {
    this.root = root;
    this.viewport = root.querySelector(".memory__viewport");
    this.spacer = root.querySelector(".memory__spacer");
    this.rows = root.querySelector(".memory__rows");
    this.spInfo = root.querySelector("[data-sp]");
    this.spacer.style.height = `${WORDS * ROW}px`;
    this.words = new Array(WORDS).fill(0);
    this.notes = new Map();
    this.recent = new Map(); // endereço → {op, age}
    this.snapshot = null;
    this.dataStart = null;
    this.onJump = onJump;
    this.viewport.addEventListener("scroll", () => this.render());
    new ResizeObserver(() => this.render()).observe(this.viewport);
  }

  /** Rótulos e o texto do fonte de cada endereço do programa. */
  setProgram(program) {
    this.notes = new Map();
    this.dataStart = null;
    if (!program) return;
    const labels = new Map();
    for (const [name, address] of Object.entries(program.symbols)) {
      labels.set(address, [...(labels.get(address) ?? []), name]);
    }
    // Código: "rótulo: LODD n". Dados: o rótulo e o deslocamento na lista ("a", "a+1"...).
    // Não mostramos o valor inicial do fonte, porque o valor atual já está nas colunas.
    let base = null;
    for (const entry of program.listing) {
      const names = labels.get(entry.address);
      if (names) base = { name: names[0], address: entry.address, line: entry.line };
      let text;
      if (entry.kind === "code") {
        text = `${names ? `${names.join(", ")}: ` : ""}${entry.text}`;
      } else if (names) {
        text = names.join(", ");
      } else if (base && base.line === entry.line) {
        text = `${base.name}+${entry.address - base.address}`;
      } else {
        text = "dado";
      }
      this.notes.set(entry.address, { text, kind: entry.kind });
      if (entry.kind === "data" && this.dataStart === null) this.dataStart = entry.address;
    }
    this.recent.clear();
  }

  update(words, snapshot, { executing = snapshot?.macro?.address ?? null } = {}) {
    this.words = words ?? this.words;
    this.snapshot = snapshot;
    this.executing = executing;
    for (const [address, item] of this.recent) {
      item.age += 1;
      if (item.age > FADE_STEPS) this.recent.delete(address);
    }
    for (const event of snapshot?.events ?? []) {
      if (event.type === "memory_complete") this.recent.set(event.address, { op: event.op, age: 0 });
    }
    const sp = snapshot?.registers?.SP ?? 0;
    this.spInfo.textContent = sp >= WORDS ? `SP = ${sp} (pilha vazia)` : `SP = ${sp} (0x${hex(sp, 3)})`;
    this.render();
  }

  clearHighlights() {
    this.recent.clear();
    this.render();
  }

  goTo(address, { align = "start" } = {}) {
    const target = Math.max(0, Math.min(WORDS - 1, address));
    const top = target * ROW;
    this.viewport.scrollTop = align === "end" ? top - this.viewport.clientHeight + ROW : top;
    this.render();
    this.flash(target);
  }

  flash(address) {
    this.flashAddress = address;
    this.render();
    clearTimeout(this.flashTimer);
    this.flashTimer = setTimeout(() => {
      this.flashAddress = null;
      this.render();
    }, 1200);
  }

  render() {
    const height = this.viewport.clientHeight || 320;
    const first = Math.max(0, Math.floor(this.viewport.scrollTop / ROW) - 4);
    const last = Math.min(WORDS - 1, first + Math.ceil(height / ROW) + 8);
    const snapshot = this.snapshot;
    const sp = snapshot?.registers?.SP ?? WORDS;
    const executing = this.executing ?? null;
    const memory = snapshot?.memory;
    const pending = memory?.busy ? memory.address : null;
    const html = [];
    for (let address = first; address <= last; address += 1) {
      const value = this.words[address] ?? 0;
      const note = this.notes.get(address);
      const recent = this.recent.get(address);
      const cls = ["memory__row"];
      if (note) cls.push(`is-${note.kind}`);
      if (recent) cls.push(recent.op === "wr" ? "is-written" : "is-read", `age-${recent.age}`);
      if (address === executing) cls.push("is-executing");
      if (address === pending) cls.push("is-pending");
      if (address === this.flashAddress) cls.push("is-flash");
      if (address >= sp) cls.push("is-stack");
      const markers = [];
      if (address === executing) markers.push('<span class="mk mk--pc">PC</span>');
      if (address === sp) markers.push('<span class="mk mk--sp">SP</span>');
      if (address === pending) markers.push(`<span class="mk mk--mem">${memory.op.toUpperCase()}</span>`);
      const text = note ? note.text : address >= sp ? "pilha" : "";
      html.push(
        `<div class="${cls.join(" ")}" style="top:${address * ROW}px">` +
          `<span class="memory__addr">${String(address).padStart(4, "0")}</span>` +
          `<span class="memory__haddr">${hex(address, 3)}</span>` +
          `<span class="memory__hex">${hex(value)}</span>` +
          `<span class="memory__dec">${signed16(value)}</span>` +
          `<span class="memory__note">${markers.join("")}${escapeHtml(text)}</span>` +
          `</div>`,
      );
    }
    this.rows.innerHTML = html.join("");
  }
}
