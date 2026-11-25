// Painéis das caches L1: estatísticas, último acesso e a tabela de linhas (lê snapshot.caches).

import { escapeHtml, hex, num, percent } from "./format.js";

const TITLES = { instruction: "Cache de Instruções", data: "Cache de Dados" };
const MAPPING = { direct: "mapeamento direto", "2way": "2 vias, LRU" };

export class CachePanel {
  constructor(root, kind) {
    this.root = root;
    this.kind = kind; // "instruction" | "data"
    root.classList.add("cache");
    root.innerHTML = `
      <div class="cache__head">
        <h2>${TITLES[kind]}</h2>
        <span class="cache__cfg"></span>
      </div>
      <dl class="cache__stats">
        <div><dt>Hits</dt><dd data-stat="hits">0</dd></div>
        <div><dt>Misses</dt><dd data-stat="misses">0</dd></div>
        <div><dt>Taxa de acerto</dt><dd data-stat="rate">—</dd></div>
      </dl>
      <div class="cache__meter" aria-hidden="true"><span></span></div>
      <p class="cache__last"></p>
      <p class="cache__bits"></p>
      <div class="cache__table-wrap">
        <table class="cache__lines">
          <thead><tr><th>Índice</th><th>Via</th><th>Valid</th><th>Tag</th><th>Dados</th><th>LRU</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>`;
    this.cfg = root.querySelector(".cache__cfg");
    this.stats = Object.fromEntries(
      [...root.querySelectorAll("[data-stat]")].map((node) => [node.dataset.stat, node]),
    );
    this.meter = root.querySelector(".cache__meter span");
    this.last = root.querySelector(".cache__last");
    this.bits = root.querySelector(".cache__bits");
    this.body = root.querySelector("tbody");
    this.table = root.querySelector(".cache__lines");
  }

  update(snapshot) {
    const cache = snapshot?.caches?.[this.kind];
    if (!cache) {
      this.root.classList.add("is-empty");
      this.cfg.textContent = "sem caches (portas próprias)";
      return;
    }
    const config = cache.config;
    const enabled = config.enabled;
    this.root.classList.toggle("is-disabled", !enabled);
    this.cfg.textContent = enabled
      ? `${config.lines} linhas · bloco de ${config.block_size} · ${MAPPING[config.mapping]} · miss +${config.miss_penalty}`
      : `desligada · miss +${config.miss_penalty} em todo acesso`;
    this.stats.hits.textContent = num(cache.hits);
    this.stats.misses.textContent = num(cache.misses);
    this.stats.rate.textContent = cache.accesses ? percent(cache.hit_rate) : "—";
    this.meter.style.width = `${(cache.accesses ? cache.hit_rate : 0) * 100}%`;

    const bits = cache.address_bits;
    const tagDigits = Math.max(1, Math.ceil(bits.tag / 4));
    this.bits.innerHTML = enabled
      ? `Endereço (12 bits) = <b class="seg-tag">tag ${bits.tag}</b> | <b class="seg-index">índice ${bits.index}</b> | <b class="seg-offset">offset ${bits.offset}</b>`
      : "Cache desligada: todo acesso vai direto à memória principal (bypass).";

    // Último acesso: forte se aconteceu neste passo, apagado se é de antes.
    const access = cache.last_access;
    const fresh = (snapshot.events ?? []).some(
      (event) => event.type === "memory_complete" && event.kind === this.kind,
    );
    const pending = snapshot.memory.busy && snapshot.memory.kind === this.kind ? snapshot.memory : null;
    let html = "";
    if (access) {
      const hit = access.result === "hit";
      const parts = [
        `${access.op.toUpperCase()} end. 0x${hex(access.address, 3)}`,
        `tag 0x${hex(access.tag, tagDigits)}`,
        `índice ${access.index}`,
        `offset ${access.offset}`,
      ];
      if (access.way !== null && config.mapping === "2way") parts.push(`via ${access.way}`);
      if (access.evicted_tag !== null) parts.push(`substituiu tag 0x${hex(access.evicted_tag, tagDigits)}`);
      if (access.bypass) parts.push("bypass");
      html = `<span class="badge ${hit ? "badge--hit" : "badge--miss"}">${hit ? "HIT" : "MISS"}</span> ${escapeHtml(parts.join(" · "))}`;
    } else {
      html = `<span class="badge">—</span> nenhum acesso ainda`;
    }
    if (pending) {
      const miss = pending.stall > 0;
      html += `<span class="cache__pending">em andamento: ${pending.op.toUpperCase()} 0x${hex(pending.address, 3)}${miss ? ` (miss, +${pending.stall} subciclos de espera)` : ""}</span>`;
    }
    this.last.innerHTML = html;
    this.last.classList.toggle("is-fresh", fresh);

    // Tabela de linhas.
    const twoWay = config.mapping === "2way";
    this.table.classList.toggle("is-direct", !twoWay);
    const rows = cache.lines.map((line) => {
      const isLast =
        access && enabled && access.index === line.set && access.way === line.way && access.way !== null;
      const cls = [
        line.valid ? "" : "is-invalid",
        isLast ? (access.result === "hit" ? "is-hit" : "is-miss") : "",
        isLast && fresh ? "is-fresh" : "",
        twoWay && line.way === 0 && line.set > 0 ? "set-start" : "",
      ]
        .filter(Boolean)
        .join(" ");
      const data = line.valid ? line.data.map((word) => hex(word)).join(" ") : "—";
      const lru = twoWay ? (line.lru ? '<span class="lru">LRU</span>' : "") : "—";
      return `<tr class="${cls}">
        <td>${line.way === 0 || !twoWay ? line.set : ""}</td>
        <td>${line.way}</td>
        <td>${line.valid ? "1" : "0"}</td>
        <td>${line.valid ? `0x${hex(line.tag, tagDigits)}` : "—"}</td>
        <td class="mono">${data}</td>
        <td>${lru}</td>
      </tr>`;
    });
    this.body.innerHTML = rows.join("");
  }
}
