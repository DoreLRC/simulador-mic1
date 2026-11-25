// Diagrama SVG do MIC-1 (Tanenbaum, 3ª ed.): via de dados à esquerda, controle à direita.
// Fios e componentes acendem conforme o subciclo; os valores vêm do snapshot da CPU.

import { CONSTANT_REGISTERS, REGISTER_NAMES, bin, el, hex, signed16 } from "./format.js";
import { MIR_FIELDS, fieldMeaning } from "./microcode.js";

const svgEl = (tag, attrs, parent) => el(tag, attrs, parent, true);

// Geometria (unidades do viewBox 1000 × 720)
const REG = { x: 132, y: 58, w: 180, h: 22 };
const BUS = { c: 104, a: 344, b: 444, top: 50 };
const LATCH_Y = 436;
const MIR = { x: 612, y: 196, w: 376, h: 50 };
const BIT = MIR.w / 32;

const FIELD = {};
{
  let offset = 0;
  for (const [name, width, label] of MIR_FIELDS) {
    FIELD[name] = { name, width, label, x: MIR.x + offset * BIT, w: width * BIT };
    offset += width;
  }
}
const fieldX = (name) => FIELD[name].x + FIELD[name].w / 2;
const regCenter = (index) => REG.y + index * REG.h + REG.h / 2;

const CAPTIONS = {
  0: "Aguardando o primeiro subciclo: use os botões de passo",
  1: "Δw: o MIR recebe a microinstrução apontada pelo MPC",
  2: "Δx: registradores → barramentos A e B → latches",
  3: "Δy: AMUX → ULA (N, Z) → deslocador; MAR ← Latch B",
  4: "Δz: C → registrador e/ou MBR; RD/WR; MPC ← MMUX",
  stall: "Espera: miss de cache, o bloco vem da memória principal",
};

const h4 = (value) => `0x${hex(value, 4)}`;

function tip(x, y, direction, size = 4) {
  const long = size * 1.7;
  switch (direction) {
    case "right":
      return `M${x},${y} l${-long},${-size} v${2 * size} z`;
    case "left":
      return `M${x},${y} l${long},${-size} v${2 * size} z`;
    case "down":
      return `M${x},${y} l${-size},${-long} h${2 * size} z`;
    default: // up
      return `M${x},${y} l${-size},${long} h${2 * size} z`;
  }
}

function label(parent, x, y, content, cls = "", anchor = "middle") {
  return svgEl("text", { x, y, class: cls, "text-anchor": anchor, text: content }, parent);
}

export class DatapathDiagram {
  constructor(svg) {
    this.svg = svg;
    svg.setAttribute("viewBox", "0 0 1000 720");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Diagrama da microarquitetura MIC-1");
    this.wires = {};
    this.parts = {};
    this.regs = {};
    this.mirCells = {};
    this.text = {};
    this.wireLayer = svgEl("g", { class: "layer-wires" }, svg);
    this.partLayer = svgEl("g", { class: "layer-parts" }, svg);
    this.buildDatapath();
    this.buildMemory();
    this.buildControl();
    this.buildLegend();
  }

  // Construção do SVG (uma vez só)

  wire(key, cls, d, { tips = [], dots = [], title = null } = {}) {
    const group = svgEl("g", { class: `wire ${cls}`, "data-wire": key }, this.wireLayer);
    if (title) svgEl("title", { text: title }, group);
    svgEl("path", { d, class: "wire__line" }, group);
    for (const [x, y, direction] of tips) svgEl("path", { d: tip(x, y, direction), class: "wire__tip" }, group);
    for (const [x, y] of dots) svgEl("circle", { cx: x, cy: y, r: 2.6, class: "wire__dot" }, group);
    this.wires[key] = group;
    return group;
  }

  part(key, x, y, w, h, cls = "", title = null) {
    const group = svgEl("g", { class: `part ${cls}`, "data-part": key }, this.partLayer);
    if (title) svgEl("title", { text: title }, group);
    svgEl("rect", { x, y, width: w, height: h, rx: 4, class: "part__box" }, group);
    this.parts[key] = group;
    return group;
  }

  shape(key, points, cls = "", title = null) {
    const group = svgEl("g", { class: `part ${cls}`, "data-part": key }, this.partLayer);
    if (title) svgEl("title", { text: title }, group);
    svgEl("polygon", { points, class: "part__box" }, group);
    this.parts[key] = group;
    return group;
  }

  buildDatapath() {
    const right = REG.x + REG.w;
    const last = regCenter(REGISTER_NAMES.length - 1);

    // Barramentos (sob os componentes).
    this.wire("busA", "bus-a", `M${BUS.a},${BUS.top} V${LATCH_Y}`, {
      tips: [[BUS.a, LATCH_Y, "down"]],
      title: "Barramento A",
    });
    this.wire("busB", "bus-b", `M${BUS.b},${BUS.top} V${LATCH_Y}`, {
      tips: [[BUS.b, LATCH_Y, "down"]],
      title: "Barramento B",
    });
    label(this.partLayer, BUS.a, BUS.top - 6, "A", "t-bus bus-a-text");
    label(this.partLayer, BUS.b, BUS.top - 6, "B", "t-bus bus-b-text");
    label(this.partLayer, BUS.c, BUS.top - 6, "C", "t-bus bus-c-text");
    label(this.partLayer, REG.x + REG.w / 2, BUS.top - 6, "16 registradores × 16 bits", "t-title");

    REGISTER_NAMES.forEach((name, index) => {
      const y = REG.y + index * REG.h;
      const center = y + REG.h / 2;
      this.wire(`stubC:${name}`, "bus-c", `M${BUS.c},${center} H${REG.x}`, {
        tips: [[REG.x, center, "right"]],
      });
      this.wire(`stubA:${name}`, "bus-a", `M${right},${center - 4} H${BUS.a}`, {
        dots: [[BUS.a, center - 4]],
      });
      this.wire(`stubB:${name}`, "bus-b", `M${right},${center + 4} H${BUS.b}`, {
        dots: [[BUS.b, center + 4]],
      });
      const constant = CONSTANT_REGISTERS.has(name);
      const group = this.part(
        `reg:${name}`,
        REG.x,
        y,
        REG.w,
        REG.h,
        constant ? "reg reg--const" : "reg",
        constant ? `${name}: constante (somente leitura)` : `Registrador ${name}`,
      );
      label(group, REG.x + 8, center + 4, name, "t-name", "start");
      const dec = label(group, REG.x + 118, center + 4, "0", "t-val", "end");
      const hx = label(group, right - 8, center + 4, "0000", "t-hex", "end");
      this.regs[name] = { group, dec, hx };
    });
    this.text.busA = label(this.partLayer, BUS.a + 6, LATCH_Y - 14, "", "t-small bus-a-text", "start");
    this.text.busB = label(this.partLayer, BUS.b + 6, LATCH_Y - 14, "", "t-small bus-b-text", "start");

    // Latches, AMUX, ULA, deslocador.
    const latch = (key, title, cx) => {
      const group = this.part(key, cx - 40, LATCH_Y, 80, 30, "latch", title);
      label(group, cx, LATCH_Y + 11, title, "t-small");
      return label(group, cx, LATCH_Y + 25, "0x0000", "t-val");
    };
    this.text.latchA = latch("latchA", "Latch A", BUS.a);
    this.text.latchB = latch("latchB", "Latch B", BUS.b);

    this.wire("la2amux", "bus-a", `M${BUS.a},466 V514`, { tips: [[BUS.a, 514, "down"]] });
    this.wire("mbr2amux", "mem", "M228,528 H304", { tips: [[304, 528, "right"]] });
    this.wire("amux2alu", "bus-a", `M${BUS.a},542 V575`, { tips: [[BUS.a, 575, "down"]] });
    this.wire("lb2alu", "bus-b", `M${BUS.b},466 V575`, { tips: [[BUS.b, 575, "down"]] });
    this.wire("lb2mar", "bus-b", `M${BUS.b},486 H228`, {
      tips: [[228, 486, "left"]],
      dots: [[BUS.b, 486]],
      title: "MAR ← Latch B (12 bits)",
    });
    this.wire("alu2sh", "bus-c", "M394,625 V640", { tips: [[394, 640, "down"]] });
    this.wire("alu2flags", "ctl", "M472,599 H506", { tips: [[506, 599, "right"]] });

    const amux = this.shape("amux", "294,514 394,514 374,542 314,542", "mux", "AMUX: escolhe Latch A ou MBR");
    label(amux, BUS.a, 532, "AMUX", "t-name");
    this.text.amux = label(this.partLayer, 338, 562, "", "t-small", "end");

    const alu = this.shape("alu", "294,575 384,575 394,590 404,575 494,575 449,625 339,625", "alu", "ULA");
    label(alu, 394, 606, "ULA", "t-name");
    this.text.alu = label(alu, 394, 619, "A + B", "t-op");

    const flag = (key, x, letter) => {
      const group = this.part(key, x, 588, 28, 22, "flag", `Flag ${letter}`);
      return label(group, x + 14, 603, `${letter} 0`, "t-flag");
    };
    this.text.flagN = flag("flagN", 506, "N");
    this.text.flagZ = flag("flagZ", 540, "Z");

    const shifter = this.part("shifter", 334, 640, 120, 30, "", "Deslocador");
    label(shifter, 394, 652, "Deslocador", "t-small");
    this.text.shifter = label(shifter, 394, 665, "—", "t-op");

    // Barramento C: deslocador → registradores e → MBR.
    this.wire("sh2c", "bus-c", "M394,670 V694 H180", {});
    this.wire("busC", "bus-c", `M180,694 H${BUS.c} V${regCenter(0)}`, {
      dots: [[BUS.c, last]],
      title: "Barramento C",
    });
    this.wire("c2mbr", "bus-c", "M180,694 V542", { tips: [[180, 542, "up"]], dots: [[180, 694]] });
    this.text.busC = label(this.partLayer, 250, 710, "", "t-small bus-c-text", "start");

    // MAR e MBR.
    const mar = this.part("mar", 132, 472, 96, 28, "reg-like", "MAR: endereço de 12 bits");
    label(mar, 140, 490, "MAR", "t-name", "start");
    this.text.mar = label(mar, 220, 490, "000", "t-hex", "end");
    this.text.marSource = label(this.partLayer, 180, 464, "", "t-small", "middle");
    const mbr = this.part("mbr", 132, 514, 96, 28, "reg-like", "MBR: dado de/para a memória");
    label(mbr, 140, 532, "MBR", "t-name", "start");
    this.text.mbr = label(mbr, 220, 532, "0000", "t-hex", "end");
  }

  buildMemory() {
    this.wire("mar2mem", "mem", "M132,486 H84", { tips: [[84, 486, "left"]], title: "Endereço para a memória" });
    this.wire("mbr2mem", "mem", "M132,528 H84", {
      tips: [
        [84, 528, "left"],
        [132, 528, "right"],
      ],
      title: "Dado de/para a memória",
    });
    const mem = this.part("memory", 8, 430, 76, 232, "memsys", "Memória principal e caches L1");
    label(mem, 46, 446, "Memória", "t-name");
    label(mem, 46, 458, "4096 × 16", "t-tiny");
    const light = (key, x, text) => {
      const group = svgEl("g", { class: "light", "data-light": key }, mem);
      svgEl("rect", { x, y: 465, width: 28, height: 14, rx: 3 }, group);
      label(group, x + 14, 475, text, "t-tiny");
      return group;
    };
    this.lights = { rd: light("rd", 14, "RD"), wr: light("wr", 48, "WR") };
    const sub = (key, y, text, title) => {
      const group = svgEl("g", { class: "sub", "data-part": key }, mem);
      svgEl("title", { text: title }, group);
      svgEl("rect", { x: 14, y, width: 62, height: 22, rx: 3 }, group);
      label(group, 45, y + 15, text, "t-small");
      this.parts[key] = group;
    };
    sub("icache", 488, "I-Cache", "Cache de instruções: RD com MAR vindo do PC");
    sub("dcache", 516, "D-Cache", "Cache de dados: demais RD e todo WR (write-through)");
    sub("ram", 544, "RAM", "Memória principal");
    this.text.memOp = label(mem, 46, 588, "ocioso", "t-val");
    this.text.memProgress = label(mem, 46, 604, "", "t-tiny");
    this.text.memKind = label(mem, 46, 618, "", "t-tiny");
    this.text.memStall = label(mem, 46, 634, "", "t-tiny t-warn");
  }

  buildControl() {
    // Relógio.
    label(this.partLayer, MIR.x, 31, "Relógio", "t-name", "start");
    this.clockCells = {};
    const cells = [
      [1, "Δw", 664, 54],
      [2, "Δx", 722, 54],
      [3, "Δy", 780, 54],
      [4, "Δz", 838, 54],
      ["stall", "espera", 896, 92],
    ];
    for (const [key, text, x, w] of cells) {
      const group = this.part(`clock:${key}`, x, 14, w, 26, key === "stall" ? "clock clock--stall" : "clock");
      label(group, x + w / 2, 32, text, "t-name");
      this.clockCells[key] = group;
    }
    this.text.caption = label(this.partLayer, MIR.x, 62, "", "t-caption", "start");

    // Decodificadores A, B e C.
    const decoder = (key, title, y) => {
      const group = this.part(key, 490, y, 100, 26, "decoder", `${title}: 4 → 16 linhas`);
      label(group, 498, y + 10, title, "t-tiny", "start");
      const value = label(group, 540, y + 22, "—", "t-small");
      this.wire(`${key}Out`, "ctl", `M490,${y + 13} H458`, { tips: [[458, y + 13, "left"]] });
      label(this.partLayer, 474, y + 8, "16", "t-tiny");
      return value;
    };
    this.text.decA = decoder("decA", "Decod. A", 70);
    this.text.decB = decoder("decB", "Decod. B", 106);
    this.text.decC = decoder("decC", "Decod. C", 142);
    this.wire("mir2decA", "ctl", `M${fieldX("a")},${MIR.y} V83 H590`, { tips: [[590, 83, "left"]] });
    this.wire("mir2decB", "ctl", `M${fieldX("b")},${MIR.y} V119 H590`, { tips: [[590, 119, "left"]] });
    this.wire("mir2decC", "ctl", `M${fieldX("c")},${MIR.y} V155 H590`, { tips: [[590, 155, "left"]] });
    this.wire("enc", "ctl", `M${fieldX("enc")},${MIR.y} V180 H540 V168`, {
      tips: [[540, 168, "up"]],
      title: "ENC habilita o decodificador C",
    });

    // MIR.
    label(this.partLayer, MIR.x, MIR.y - 6, "MIR", "t-name", "start");
    const mir = this.part("mir", MIR.x, MIR.y, MIR.w, MIR.h, "mir", "Registrador de microinstrução (32 bits)");
    for (const field of Object.values(FIELD)) {
      const cx = field.x + field.w / 2;
      const cell = svgEl("g", { class: "mir-cell", "data-field": field.name }, mir);
      svgEl("rect", { x: field.x, y: MIR.y, width: field.w, height: MIR.h, class: "mir-cell__box" }, cell);
      const title = svgEl("title", { text: field.label }, cell);
      if (field.width === 1) {
        label(cell, cx + 3, MIR.y + 17, field.label, "t-tiny").setAttribute(
          "transform",
          `rotate(-90 ${cx + 3} ${MIR.y + 17})`,
        );
      } else {
        label(cell, cx, MIR.y + 13, field.label, "t-tiny");
      }
      const value = label(cell, cx, MIR.y + 43, "0".repeat(field.width), "t-bits");
      this.mirCells[field.name] = { cell, value, title };
    }

    // Memória de controle, MPC, incrementador, MMUX e lógica de microssequenciamento.
    const cs = this.part("cs", 650, 284, 270, 84, "", "Memória de controle: 256 palavras de 32 bits");
    label(cs, 660, 300, "Memória de controle · 256 × 32", "t-name", "start");
    this.text.csLine = label(cs, 660, 320, "—", "t-mono", "start");
    this.text.csComment = label(cs, 660, 337, "", "t-small t-muted", "start");
    this.text.csInfo = label(cs, 660, 358, "", "t-tiny t-muted", "start");
    this.wire("cs2mir", "ctl", `M780,284 V${MIR.y + MIR.h}`, { tips: [[780, MIR.y + MIR.h, "up"]] });

    const mpc = this.part("mpc", 740, 404, 80, 26, "reg-like", "MPC: endereço da próxima microinstrução");
    this.text.mpc = label(mpc, 780, 421, "MPC = 0", "t-name");
    this.wire("mpc2cs", "ctl", "M780,404 V368", { tips: [[780, 368, "up"]] });

    const inc = this.part("inc", 850, 446, 60, 24, "", "Incrementador: MPC + 1");
    label(inc, 880, 462, "+1", "t-name");
    this.wire("mpc2inc", "ctl", "M820,417 H880 V446", { tips: [[880, 446, "down"]] });

    const mmux = this.shape("mmux", "760,486 800,486 820,512 740,512", "mux", "MMUX: MPC + 1 ou ADDR");
    label(mmux, 780, 504, "MMUX", "t-tiny");
    this.text.mmux = label(this.partLayer, 782, 572, "", "t-small", "middle");
    this.wire("mmux2mpc", "ctl", "M780,486 V430", { tips: [[780, 430, "up"]] });
    this.wire("inc2mmux", "ctl", "M880,470 V532 H805 V512", { tips: [[805, 512, "up"]] });
    this.wire("addr2mmux", "ctl", `M${fieldX("addr")},${MIR.y + MIR.h} V548 H755 V512`, {
      tips: [[755, 512, "up"]],
      title: "ADDR: endereço de desvio",
    });

    const seq = this.part("seq", 610, 484, 104, 30, "", "Lógica de microssequenciamento: COND, N e Z");
    label(seq, 662, 496, "Lógica de µseq", "t-tiny");
    this.text.seq = label(seq, 662, 509, "", "t-small");
    this.wire("seq2mmux", "ctl", "M714,499 H750", { tips: [[750, 499, "right"]] });
    this.wire("cond2seq", "ctl", `M${fieldX("cond")},${MIR.y + MIR.h} V484`, { tips: [[fieldX("cond"), 484, "down"]] });
    this.wire("flags2seq", "ctl", "M568,599 H662 V514", { tips: [[662, 514, "up"]] });
    label(this.partLayer, 606, 593, "N, Z", "t-tiny", "middle");

    // Macroinstrução em execução.
    this.text.macro = label(this.partLayer, MIR.x, 622, "", "t-name", "start");
    this.text.macroLine = label(this.partLayer, MIR.x, 640, "", "t-small t-muted", "start");
  }

  buildLegend() {
    const items = [
      ["bus-a", "barramento A"],
      ["bus-b", "barramento B"],
      ["bus-c", "barramento C"],
      ["ctl", "controle"],
      ["mem", "memória"],
    ];
    let x = MIR.x;
    let y = 672;
    for (const [cls, text] of items) {
      const group = svgEl("g", { class: `legend ${cls}` }, this.partLayer);
      svgEl("line", { x1: x, y1: y, x2: x + 20, y2: y, class: "legend__line" }, group);
      label(group, x + 26, y + 4, text, "t-tiny", "start");
      x += 122;
      if (x > 900) {
        x = MIR.x;
        y += 18;
      }
    }
  }

  // Atualização (a cada snapshot)

  update(snapshot, previous, { microprogram = null } = {}) {
    if (!snapshot) return;
    const clock = snapshot.clock;
    const stalled = clock.stalled;
    const sub = stalled ? 0 : clock.subcycle;
    const active = new Set(snapshot.active);
    const mir = snapshot.mir;
    const f = mir?.fields ?? Object.fromEntries(MIR_FIELDS.map(([name]) => [name, 0]));
    const { A: busA, B: busB, C: busC } = snapshot.buses;
    const memory = snapshot.memory;
    const events = snapshot.events ?? [];
    const readDone = events.some((event) => event.type === "memory_complete" && event.op === "rd");
    // Mesma regra do MMUX no núcleo: COND diz se desvia sempre, se N ou se Z.
    const taken = f.cond === 3 || (f.cond === 1 && snapshot.flags.N) || (f.cond === 2 && snapshot.flags.Z);
    const dispatch = events.find((event) => event.type === "case_dispatch");

    // Um fio acende se trabalhou no subciclo atual: Δx usa os barramentos A e B, Δy o
    // caminho AMUX → ULA → deslocador, e Δz o barramento C e o MMUX. Num stall nada anda,
    // por isso "sub" vira 0 e só a memória fica acesa.
    const on = (key, condition) => this.wires[key]?.classList.toggle("is-active", Boolean(condition));
    const lit = (key, condition) => this.parts[key]?.classList.toggle("is-active", Boolean(condition));

    // Registradores.
    for (const name of REGISTER_NAMES) {
      const value = snapshot.registers[name];
      const reg = this.regs[name];
      reg.dec.textContent = String(signed16(value));
      reg.hx.textContent = hex(value);
      const srcA = sub === 2 && busA.source === name;
      const srcB = sub === 2 && busB.source === name;
      const dstC = sub === 4 && f.enc && busC.target === name;
      reg.group.classList.toggle("is-src-a", srcA);
      reg.group.classList.toggle("is-src-b", srcB);
      reg.group.classList.toggle("is-dst-c", Boolean(dstC));
      on(`stubA:${name}`, srcA);
      on(`stubB:${name}`, srcB);
      on(`stubC:${name}`, dstC);
      if (previous && previous.registers[name] !== value) {
        reg.group.classList.remove("is-changed");
        void reg.group.getBBox(); // reinicia a animação
        reg.group.classList.add("is-changed");
      }
    }

    // Barramentos e caminhos da via de dados.
    on("busA", sub === 2);
    on("busB", sub === 2);
    on("la2amux", sub === 3 && !f.amux);
    on("mbr2amux", sub === 3 && f.amux);
    on("amux2alu", sub === 3);
    on("lb2alu", sub === 3);
    on("lb2mar", sub === 3 && f.mar);
    on("alu2sh", sub === 3);
    on("alu2flags", sub === 3);
    on("sh2c", sub === 4 && (f.enc || f.mbr));
    on("busC", sub === 4 && f.enc);
    on("c2mbr", sub === 4 && f.mbr);
    on("mar2mem", memory.busy);
    on("mbr2mem", (memory.busy && memory.op === "wr") || readDone);

    this.text.busA.textContent = busA.source ? `${busA.source} = ${h4(busA.value)}` : "";
    this.text.busB.textContent = busB.source ? `${busB.source} = ${h4(busB.value)}` : "";
    this.text.busC.textContent = sub === 4 && (f.enc || f.mbr)
      ? `C = ${h4(busC.value)} → ${[f.enc ? busC.target : null, f.mbr ? "MBR" : null].filter(Boolean).join(" e ")}`
      : `C = ${h4(busC.value)}`;
    this.text.latchA.textContent = h4(snapshot.latches.A);
    this.text.latchB.textContent = h4(snapshot.latches.B);
    this.text.amux.textContent = `sel.: ${snapshot.amux.select}`;
    this.text.alu.textContent = snapshot.alu.op;
    this.text.shifter.textContent = snapshot.shifter.op === "" ? "—" : snapshot.shifter.op;
    this.text.flagN.textContent = `N ${snapshot.flags.N ? 1 : 0}`;
    this.text.flagZ.textContent = `Z ${snapshot.flags.Z ? 1 : 0}`;
    this.parts.flagN.classList.toggle("is-on", snapshot.flags.N);
    this.parts.flagZ.classList.toggle("is-on", snapshot.flags.Z);

    this.text.mar.textContent = `0x${hex(snapshot.mar.value, 3)}`;
    this.text.mbr.textContent = h4(snapshot.mbr);
    const source = snapshot.mar.source;
    this.text.marSource.textContent = source
      ? `origem: ${source} → ${snapshot.mar.from_pc ? "I-Cache" : "D-Cache"}`
      : "";
    this.text.marSource.classList.toggle("t-pc", snapshot.mar.from_pc);
    this.parts.mar.classList.toggle("is-from-pc", snapshot.mar.from_pc);

    for (const key of ["latchA", "latchB", "amux", "alu", "shifter", "mar", "mbr"]) {
      const name = { latchA: "latch_a", latchB: "latch_b" }[key] ?? key;
      lit(key, !stalled && active.has(name));
    }

    // Memória e caches.
    lit("memory", memory.busy);
    lit("icache", active.has("icache"));
    lit("dcache", active.has("dcache"));
    lit("ram", memory.busy && (memory.stall > 0 || snapshot.caches === null));
    this.lights.rd.classList.toggle("is-on", Boolean(f.rd));
    this.lights.wr.classList.toggle("is-on", Boolean(f.wr));
    if (memory.busy) {
      this.text.memOp.textContent = `${memory.op.toUpperCase()} ${hex(memory.address, 3)}`;
      this.text.memProgress.textContent = `${Math.min(memory.elapsed, memory.latency + memory.stall)}/${memory.latency + memory.stall} subciclos`;
      this.text.memKind.textContent = memory.kind === "instruction" ? "instrução" : "dado";
      this.text.memStall.textContent = memory.stall ? `miss: +${memory.stall}` : "";
    } else {
      this.text.memOp.textContent = "ocioso";
      this.text.memProgress.textContent = "";
      this.text.memKind.textContent = "";
      this.text.memStall.textContent = "";
    }

    // Controle.
    for (const [key, cell] of Object.entries(this.clockCells)) {
      cell.classList.toggle("is-active", key === "stall" ? stalled : !stalled && clock.subcycle === Number(key));
    }
    this.text.caption.textContent = stalled ? CAPTIONS.stall : CAPTIONS[clock.subcycle] ?? "";

    on("mpc2cs", sub === 1);
    on("cs2mir", sub === 1);
    on("mir2decA", sub === 2);
    on("mir2decB", sub === 2);
    on("decAOut", sub === 2);
    on("decBOut", sub === 2);
    on("mir2decC", sub === 4 && f.enc);
    on("enc", sub === 4 && f.enc);
    on("decCOut", sub === 4 && f.enc);
    on("cond2seq", sub === 4);
    on("flags2seq", sub === 4 && (f.cond === 1 || f.cond === 2));
    on("seq2mmux", sub === 4);
    on("mpc2inc", sub === 4);
    on("inc2mmux", sub === 4 && !taken && !dispatch);
    on("addr2mmux", sub === 4 && taken && !dispatch);
    on("mmux2mpc", sub === 4);
    for (const [key, name] of [
      ["mpc", "mpc"],
      ["cs", "control_store"],
      ["mir", "mir"],
      ["decA", "decoder_a"],
      ["decB", "decoder_b"],
      ["decC", "decoder_c"],
    ]) {
      lit(key, !stalled && active.has(name));
    }
    lit("mmux", sub === 4);
    lit("seq", sub === 4);
    lit("inc", sub === 4);

    this.text.decA.textContent = `${bin(f.a, 4)} → ${REGISTER_NAMES[f.a]}`;
    this.text.decB.textContent = `${bin(f.b, 4)} → ${REGISTER_NAMES[f.b]}`;
    this.text.decC.textContent = f.enc ? `${bin(f.c, 4)} → ${REGISTER_NAMES[f.c]}` : "ENC = 0";
    for (const [name, cell] of Object.entries(this.mirCells)) {
      const value = f[name];
      cell.value.textContent = bin(value, FIELD[name].width);
      cell.cell.classList.toggle("is-set", Boolean(mir) && value !== 0);
      cell.title.textContent = `${FIELD[name].label} = ${value}: ${mir ? fieldMeaning(name, value, f) : "—"}`;
    }
    this.text.mpc.textContent = `MPC = ${snapshot.mpc}`;
    const mal = mir ? `${mir.address}: ${mir.mal}` : "—";
    this.text.csLine.textContent = mal.length > 46 ? `${mal.slice(0, 45)}…` : mal;
    this.text.csComment.textContent = mir?.comment ?? "";
    if (microprogram) {
      this.text.csInfo.textContent = `${microprogram.size} µinstruções ocupadas · ${microprogram.used_bits.toLocaleString("pt-BR")} bits`;
    }
    this.text.seq.textContent = `COND ${bin(f.cond, 2)} · N${snapshot.flags.N ? 1 : 0} Z${snapshot.flags.Z ? 1 : 0}`;
    if (dispatch) this.text.mmux.textContent = `case: IR[15:12] × 4 = ${dispatch.target}`;
    else if (sub === 4) this.text.mmux.textContent = taken ? "MMUX ← ADDR (desvia)" : "MMUX ← MPC + 1";
    else this.text.mmux.textContent = "";

    const macro = snapshot.macro;
    this.text.macro.textContent = `Instrução: ${macro.text}  ·  end. ${macro.address}  ·  IR = ${h4(macro.ir)}`;
    this.text.macroLine.textContent = macro.line ? `linha ${macro.line} do fonte` : "";
  }
}
