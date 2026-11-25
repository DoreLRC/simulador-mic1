// Ponto de entrada: liga os controles à API e redesenha os painéis a cada snapshot.

import { api } from "./api.js";
import { CachePanel } from "./caches.js";
import { DatapathDiagram } from "./diagram.js";
import { CodeEditor } from "./editor.js";
import { escapeHtml, num } from "./format.js";
import { MemoryView } from "./memory.js";
import { MicrocodeView } from "./microcode.js";
import { Splash } from "./splash.js";
import { setupTabs } from "./tabs.js";

const $ = (selector) => document.querySelector(selector);
const DEFAULT_EXAMPLE = "se_i_menor_igual_n.mac1"; // aberto ao carregar a página
const $$ = (selector) => [...document.querySelectorAll(selector)];

const state = {
  loaded: false,
  program: null,
  snapshot: null,
  previous: null,
  memory: null,
  halted: false,
  limitReached: false,
  cycleLimit: 10_000,
  mode: "original",
  microprograms: {},
  examples: [],
  running: false,
  busy: false,
  timer: null,
  dirty: false,
};

const editor = new CodeEditor($("#editor"), { onChange: markDirty, onSubmit: () => assemble() });
const microcode = new MicrocodeView($("#mal-list"), $("#mal-info"), $("#mir-fields"));
const diagram = new DatapathDiagram($("#diagram"));
const caches = [new CachePanel($("#cache-i"), "instruction"), new CachePanel($("#cache-d"), "data")];
const memoryView = new MemoryView($("#memory"));
const splash = new Splash($("#splash"), { covered: [$(".topbar"), $(".workspace")] });

// Configuração

const radio = (name) => document.querySelector(`input[name="${name}"]:checked`)?.value;

function readSettings() {
  const penaltyInput = $("#penalty");
  const penalty = Math.min(50, Math.max(0, Number.parseInt(penaltyInput.value, 10) || 0));
  penaltyInput.value = String(penalty);
  return {
    mode: radio("mode"),
    cache: {
      enabled: radio("caches") === "on",
      lines: Number(radio("lines")),
      block_size: Number(radio("block")),
      mapping: radio("mapping"),
      miss_penalty: penalty,
    },
  };
}

function writeSettings(settings) {
  const check = (name, value) => {
    const input = document.querySelector(`input[name="${name}"][value="${value}"]`);
    if (input) input.checked = true;
  };
  const cache = settings.icache;
  check("mode", settings.mode);
  check("caches", cache.enabled ? "on" : "off");
  check("mapping", cache.mapping);
  check("lines", cache.lines);
  check("block", cache.block_size);
  $("#penalty").value = String(cache.miss_penalty);
  syncCacheOptions();
}

function syncCacheOptions() {
  const enabled = radio("caches") === "on";
  for (const group of $$("[data-cache-option]")) group.classList.toggle("is-muted", !enabled);
}

async function loadMicroprogram(mode) {
  if (!state.microprograms[mode]) state.microprograms[mode] = await api.microprogram(mode);
  if (state.mode === mode) microcode.setProgram(state.microprograms[mode]);
}

// Estado e desenho

// Toda resposta da API passa por aqui: guarda o estado novo (e o anterior, para o diagrama
// piscar os registradores que mudaram) e redesenha a tela inteira a partir dele.
function applyState(data) {
  state.previous = data.program ? null : state.snapshot;
  state.snapshot = data.snapshot;
  state.memory = data.memory;
  state.halted = data.halted;
  state.limitReached = data.limit_reached;
  state.cycleLimit = data.cycle_limit ?? state.cycleLimit;
  state.loaded = true;
  if (data.program) {
    state.program = data.program;
    memoryView.setProgram(data.program);
  }
  if (data.settings) {
    const modeChanged = data.settings.mode !== state.mode || !state.microprograms[data.settings.mode];
    state.mode = data.settings.mode;
    if (modeChanged) loadMicroprogram(state.mode).then(render).catch(showError);
  }
  render();
}

function render() {
  const snapshot = state.snapshot;
  if (snapshot) {
    diagram.update(snapshot, state.previous, { microprogram: state.microprograms[state.mode] });
    microcode.update(snapshot);
    for (const panel of caches) panel.update(snapshot);
    // Entre duas instruções o destaque fica na que acabou de executar; no HALT, vai para o
    // laço final (o PC), que é onde o programa "parou".
    let executing = snapshot.macro.address;
    let line = snapshot.macro.line;
    if (state.halted) {
      executing = snapshot.registers.PC & 0xfff;
      line = snapshot.program.source_map[String(executing)] ?? line;
    }
    memoryView.update(state.memory, snapshot, { executing });
    editor.setCurrentLine(state.dirty ? null : line, { scroll: !state.dirty });
    const clock = snapshot.clock;
    $("#c-instructions").textContent = num(clock.completed_instructions);
    $("#c-cycles").textContent = num(clock.completed_cycles);
    $("#c-subcycles").textContent = num(clock.elapsed_subcycles);
    $("#c-stall").textContent = num(clock.stall_subcycles);
    $("#c-phase").textContent = clock.subcycle ? `ciclo ${num(clock.cycle)} · ${clock.subcycle_name}` : "—";
  }
  renderStatus();
  renderButtons();
}

function renderStatus() {
  let text = "Sem programa";
  let kind = "idle";
  if (state.loaded) {
    if (state.halted) [text, kind] = ["HALT", "halt"];
    else if (state.limitReached) [text, kind] = [`Limite de ${num(state.cycleLimit)} ciclos`, "warn"];
    else if (state.running) [text, kind] = ["Rodando", "running"];
    else if (state.snapshot.clock.elapsed_subcycles === 0) [text, kind] = ["Pronto", "ready"];
    else [text, kind] = ["Pausado", "paused"];
  }
  const status = $("#status");
  status.textContent = text;
  status.dataset.kind = kind;
}

function renderButtons() {
  const idle = state.loaded && !state.running;
  for (const button of $$("[data-step]")) button.disabled = !idle;
  $("#btn-halt").disabled = !idle;
  $("#btn-reset").disabled = !state.loaded;
  $("#btn-assemble").disabled = state.running;
  const run = $("#btn-run");
  run.disabled = !state.loaded;
  run.textContent = state.running ? "❚❚ Pausar" : "▶ Iniciar";
  run.setAttribute("aria-pressed", String(state.running));
  run.classList.toggle("is-running", state.running);
}

function markDirty() {
  if (state.dirty) return;
  state.dirty = true;
  $("#editor-hint").innerHTML =
    "<strong>Código alterado:</strong> clique em <em>Montar e Carregar</em> para usar a nova versão.";
  editor.setCurrentLine(null, { scroll: false });
}

function clearDirty() {
  state.dirty = false;
  $("#editor-hint").innerHTML =
    "Ctrl+Enter monta o programa. Rótulos: <code>laco:</code>, dados: <code>v: 1, 2, 3</code>, comentários <code>;</code> ou <code>//</code>.";
}

function showAssemblyErrors(errors) {
  const box = $("#asm-errors");
  editor.setErrors(errors);
  if (!errors?.length) {
    box.hidden = true;
    box.replaceChildren();
    return;
  }
  box.hidden = false;
  box.innerHTML =
    `<strong>${errors.length} erro(s) de montagem</strong><ul>` +
    errors
      .map((error) => `<li><button type="button" data-line="${error.line}">linha ${error.line}</button> ${escapeHtml(error.message)}</li>`)
      .join("") +
    "</ul>";
}

let toastTimer = null;
function toast(message, kind = "info") {
  const node = $("#toast");
  node.textContent = message;
  node.dataset.kind = kind;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    node.hidden = true;
  }, kind === "error" ? 6000 : 3200);
}

function showError(error) {
  if (error.status === 409 && /nenhum programa/.test(error.message)) {
    state.loaded = false;
    stopRun();
    toast("A sessão no servidor expirou. Clique em Montar e Carregar.", "error");
    render();
    return;
  }
  toast(error.message, "error");
}

// Ações

async function busy(action) {
  if (state.busy) return false;
  state.busy = true;
  try {
    await action();
    return true;
  } catch (error) {
    stopRun();
    showError(error);
    return false;
  } finally {
    state.busy = false;
    renderButtons();
  }
}

async function assemble({ quiet = false } = {}) {
  stopRun();
  await busy(async () => {
    try {
      const data = await api.assemble(editor.value, readSettings());
      showAssemblyErrors([]);
      clearDirty();
      memoryView.clearHighlights();
      applyState(data);
      memoryView.goTo(0);
      if (!quiet) toast(`Programa montado: ${data.program.words.length} palavras carregadas a partir do endereço 0.`);
    } catch (error) {
      if (error.errors) {
        showAssemblyErrors(error.errors);
        toast(error.message, "error");
        return;
      }
      throw error;
    }
  });
}

async function step(granularity) {
  return busy(async () => {
    const data = await api.step(granularity);
    applyState(data);
    if (granularity === "halt") {
      if (data.limit_reached) {
        toast(`Parado após ${num(data.cycle_limit)} ciclos sem chegar ao HALT (laço “JUMP para si mesmo”).`, "warn");
      } else {
        toast(`HALT: ${num(data.snapshot.clock.completed_cycles)} ciclos, ${num(data.snapshot.clock.stall_subcycles)} subciclos de stall.`);
      }
    }
  });
}

async function reset({ message = "Simulação reiniciada com o programa montado." } = {}) {
  stopRun();
  await busy(async () => {
    memoryView.clearHighlights();
    applyState(await api.reset(readSettings()));
    toast(message);
  });
}

function speedSeconds() {
  return Number($("#speed").value);
}

function startRun() {
  if (!state.loaded || state.running) return;
  if (state.halted) {
    toast("O programa já está em HALT. Use Resetar para rodar de novo.", "warn");
    return;
  }
  state.running = true;
  render();
  tick();
}

function stopRun() {
  if (!state.running) return;
  state.running = false;
  clearTimeout(state.timer);
  render();
}

async function tick() {
  if (!state.running) return;
  let granularity = $("#run-granularity").value;
  // No laço final, a micro/macro seguinte já executaria o JUMP: termina como o botão HALT.
  if (granularity !== "subcycle" && state.snapshot?.halt_loop) granularity = "halt";
  const ok = await step(granularity === "halt" ? "halt" : granularity);
  if (!state.running) return;
  if (!ok) return stopRun();
  if (state.halted) {
    stopRun();
    toast("HALT: o programa chegou ao laço final.");
    return;
  }
  state.timer = setTimeout(tick, speedSeconds() * 1000);
}

async function selectExample(name, { assembleNow = true } = {}) {
  const example = state.examples.find((item) => item.name === name);
  if (!example) return;
  $("#example-select").value = name;
  editor.value = example.source;
  showAssemblyErrors([]);
  if (assembleNow) await assemble({ quiet: true });
}

// Eventos

$("#btn-assemble").addEventListener("click", () => assemble());
for (const button of $$("[data-step]")) {
  button.addEventListener("click", () => step(button.dataset.step));
}
$("#btn-run").addEventListener("click", () => (state.running ? stopRun() : startRun()));
$("#btn-halt").addEventListener("click", () => step("halt"));
$("#btn-reset").addEventListener("click", () => reset());

$("#speed").addEventListener("input", () => {
  $("#speed-value").textContent = `${speedSeconds().toLocaleString("pt-BR", { minimumFractionDigits: 1 })} s/passo`;
});

$("#config").addEventListener("change", async (event) => {
  syncCacheOptions();
  if (event.target.name === "mode") {
    state.mode = radio("mode");
    await loadMicroprogram(state.mode).catch(showError);
  }
  if (state.loaded) await reset({ message: "Configuração aplicada, simulação reiniciada." });
});

$("#example-select").addEventListener("change", (event) => {
  if (event.target.value) selectExample(event.target.value);
});

$("#asm-errors").addEventListener("click", (event) => {
  const line = event.target.closest("[data-line]")?.dataset.line;
  if (line) editor.focusLine(Number(line));
});

$("#memory").addEventListener("click", (event) => {
  const jump = event.target.closest("[data-jump]")?.dataset.jump;
  if (!jump) return;
  if (jump === "code") memoryView.goTo(0);
  else if (jump === "data") memoryView.goTo(memoryView.dataStart ?? 0);
  else memoryView.goTo(4095, { align: "end" });
});

$("#memory-goto").addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  const text = event.target.value.trim();
  const value = /^0x/i.test(text) ? Number.parseInt(text, 16) : Number.parseInt(text, 10);
  if (Number.isInteger(value) && value >= 0 && value < 4096) memoryView.goTo(value);
  else toast("Endereço inválido: use 0–4095 ou 0x000–0xFFF.", "warn");
});

// Abas: ao trocar, redesenha o que estava escondido (rolagem do MAL e linhas da memória).
for (const root of $$("[data-tabs]")) {
  setupTabs(root, {
    onChange: () => {
      if (state.snapshot) microcode.update(state.snapshot);
      memoryView.render();
    },
  });
}

$("#btn-advanced").addEventListener("click", (event) => {
  const button = event.currentTarget;
  const open = button.getAttribute("aria-expanded") !== "true";
  button.setAttribute("aria-expanded", String(open));
  $("#advanced").hidden = !open;
});

$("#btn-menu").addEventListener("click", () => {
  stopRun(); // a apresentação volta ao menu com a simulação pausada
  splash.open();
});

document.addEventListener("keydown", (event) => {
  if (splash.isOpen) return;
  if (event.target.closest("textarea, input, select, button")) return;
  if (event.key === " " && state.loaded) {
    event.preventDefault();
    state.running ? stopRun() : startRun();
  }
});

// Tema: claro (padrão) → escuro → automático (segue o sistema).
const THEMES = ["light", "dark", "auto"];
const THEME_NAMES = { auto: "automático", light: "claro", dark: "escuro" };
function applyTheme(theme) {
  if (theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  $("#btn-theme").title = `Tema: ${THEME_NAMES[theme]}`;
}
const THEME_KEY = "mic1-tema";
let theme = "light";
try {
  theme = THEMES.includes(localStorage.getItem(THEME_KEY)) ? localStorage.getItem(THEME_KEY) : "light";
} catch {
  theme = "light";
}
applyTheme(theme);
$("#btn-theme").addEventListener("click", () => {
  theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
  applyTheme(theme);
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* armazenamento indisponível: o tema vale só nesta aba */
  }
});

// Início

async function start() {
  render();
  try {
    const { examples } = await api.examples();
    state.examples = examples;
    const select = $("#example-select");
    for (const example of examples) {
      const option = document.createElement("option");
      option.value = example.name;
      option.textContent = example.name;
      option.title = example.title;
      select.appendChild(option);
    }
  } catch (error) {
    showError(error);
  }

  // Recarregou a página? Retoma a sessão que já existe no servidor.
  const saved = await api.state().catch(() => null);
  if (saved?.loaded) {
    writeSettings(saved.settings);
    state.mode = saved.settings.mode;
    await loadMicroprogram(state.mode).catch(showError);
    editor.value = saved.source;
    const match = state.examples.find((item) => item.source === saved.source);
    if (match) $("#example-select").value = match.name;
    applyState(saved);
    return;
  }
  syncCacheOptions();
  await loadMicroprogram(state.mode).catch(showError);
  const first = state.examples.find((item) => item.name === DEFAULT_EXAMPLE) ?? state.examples[0];
  if (first) await selectExample(first.name);
}

start();
