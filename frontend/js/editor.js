// Editor do programa MAC-1: textarea com numeração de linhas, linha em execução e erros.

import { el } from "./format.js";

const LINE_HEIGHT = 20; // px, igual a --editor-line no CSS
const PADDING_TOP = 8;

export class CodeEditor {
  constructor(root, { onChange, onSubmit } = {}) {
    this.root = root;
    root.classList.add("editor");
    const gutter = el("div", { class: "editor__gutter", "aria-hidden": "true" }, root);
    this.numbers = el("div", { class: "editor__numbers" }, gutter);
    const main = el("div", { class: "editor__main" }, root);
    this.marks = el("div", { class: "editor__marks", "aria-hidden": "true" }, main);
    this.input = el(
      "textarea",
      {
        class: "editor__input",
        spellcheck: "false",
        wrap: "off",
        autocapitalize: "off",
        autocomplete: "off",
        "aria-label": "Código-fonte MAC-1",
      },
      main,
    );
    this.currentLine = null;
    this.errorLines = new Map();
    this.lineCount = 0;

    this.input.addEventListener("input", () => {
      this.refreshNumbers();
      onChange?.(this.value);
    });
    this.input.addEventListener("scroll", () => this.syncScroll());
    this.input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        onSubmit?.();
      }
    });
  }

  get value() {
    return this.input.value;
  }

  set value(text) {
    this.input.value = text;
    this.input.scrollTop = 0;
    this.refreshNumbers();
  }

  /** Linha (1 a n) da macroinstrução em execução, ou null. */
  setCurrentLine(line, { scroll = true } = {}) {
    this.currentLine = line ?? null;
    this.renderMarks();
    if (scroll && this.currentLine) this.scrollToLine(this.currentLine);
  }

  /** Erros de montagem: lista de {line, message}. */
  setErrors(errors) {
    this.errorLines = new Map();
    for (const error of errors ?? []) {
      const list = this.errorLines.get(error.line) ?? [];
      list.push(error.message);
      this.errorLines.set(error.line, list);
    }
    this.refreshNumbers();
    if (errors?.length) this.scrollToLine(errors[0].line);
  }

  focusLine(line) {
    const lines = this.value.split("\n");
    const start = lines.slice(0, line - 1).reduce((sum, text) => sum + text.length + 1, 0);
    this.input.focus();
    this.input.setSelectionRange(start, start + (lines[line - 1]?.length ?? 0));
    this.scrollToLine(line);
  }

  scrollToLine(line) {
    const top = PADDING_TOP + (line - 1) * LINE_HEIGHT;
    const view = this.input;
    if (top < view.scrollTop + LINE_HEIGHT || top + 2 * LINE_HEIGHT > view.scrollTop + view.clientHeight) {
      view.scrollTop = Math.max(0, top - view.clientHeight / 3);
    }
  }

  refreshNumbers() {
    const count = Math.max(1, this.value.split("\n").length);
    if (count !== this.lineCount) {
      this.lineCount = count;
      const fragment = document.createDocumentFragment();
      for (let line = 1; line <= count; line += 1) {
        el("div", { class: "editor__number", text: String(line) }, fragment);
      }
      this.numbers.replaceChildren(fragment);
    }
    this.numbers.querySelectorAll(".is-error").forEach((node) => {
      node.classList.remove("is-error");
      node.removeAttribute("title");
    });
    for (const [line, messages] of this.errorLines) {
      const node = this.numbers.children[line - 1];
      if (!node) continue;
      node.classList.add("is-error");
      node.title = messages.join("\n");
    }
    this.renderMarks();
  }

  renderMarks() {
    const fragment = document.createDocumentFragment();
    const mark = (line, kind) =>
      el(
        "div",
        { class: `editor__mark editor__mark--${kind}`, style: `top:${PADDING_TOP + (line - 1) * LINE_HEIGHT}px` },
        fragment,
      );
    for (const line of this.errorLines.keys()) mark(line, "error");
    if (this.currentLine) mark(this.currentLine, "current");
    this.marks.replaceChildren(fragment);
    this.numbers.querySelectorAll(".is-current").forEach((node) => node.classList.remove("is-current"));
    this.numbers.children[(this.currentLine ?? 0) - 1]?.classList.add("is-current");
    this.syncScroll();
  }

  syncScroll() {
    const offset = `translateY(${-this.input.scrollTop}px)`;
    this.numbers.style.transform = offset;
    this.marks.style.transform = offset;
  }
}
