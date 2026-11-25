"""A CPU completa: junta unidade de controle, via de dados, memória e caches.

O relógio tem 4 subciclos::

    Δw (1)  o MIR recebe a microinstrução apontada pelo MPC
    Δx (2)  barramentos A e B -> Latches A e B
    Δy (3)  AMUX, ULA (N, Z) e deslocador; MAR <- Latch B quando MAR = 1
    Δz (4)  barramento C -> registrador (se ENC) e/ou MBR; RD/WR; MPC <- MMUX

A memória anda um passo em todo subciclo (latência de 6, ver :mod:`mic1.memory`). Num miss
de cache, a CPU fica parada alguns subciclos (*stall*) antes do Δw que receberia o dado.
Esses subciclos não contam como ciclos do microprograma (``cycles``); eles vão para
``stall_subcycles``.

Formas de executar, todas devolvendo um snapshot que vira JSON:

* :meth:`CPU.step_subcycle`: um subciclo (ou um subciclo de espera);
* :meth:`CPU.step_micro`: até o fim do ciclo atual (uma microinstrução);
* :meth:`CPU.step_macro`: até o MPC voltar à busca (uma macroinstrução);
* :meth:`CPU.run`: até o HALT (ver :attr:`CPU.halted`), com trava de 10.000 ciclos.

O MAC-1 não tem instrução de parada; por convenção o programa termina num laço
``fim: JUMP fim``, e a CPU considera o programa encerrado quando o PC chega nesse JUMP.
"""

from collections.abc import Callable, Iterable, Mapping

from mic1.assembler.mac1 import Program, assemble_mac1
from mic1.assembler.mal import load_microprogram
from mic1.bits import ADDRESS_MASK
from mic1.cache import CacheConfig, CacheSystem
from mic1.control import ControlStore, ControlUnit, DecodeMode
from mic1.datapath import Datapath, Reg
from mic1.isa import disassemble, encode
from mic1.memory import MainMemory, MemoryController
from mic1.ports import MemOp, MemoryPort

__all__ = ["CPU", "DEFAULT_SP", "HALT_CYCLE_LIMIT", "MAX_MACRO_CYCLES", "SUBCYCLE_NAMES"]

SUBCYCLE_NAMES = {1: "Δw", 2: "Δx", 3: "Δy", 4: "Δz"}
DEFAULT_SP = 0x1000  # pilha vazia: o primeiro PUSH grava no endereço 4095
MAX_MACRO_CYCLES = 10_000  # proteção contra microprogramas que nunca voltam à busca
HALT_CYCLE_LIMIT = 10_000  # proteção contra programas que nunca chegam ao HALT
_JUMP = encode("JUMP", 0)

Event = dict[str, object]


class CPU:
    """O processador MIC-1 completo, executado subciclo a subciclo.

    Por padrão a memória principal é acessada pelas caches L1 (I-Cache e D-Cache),
    configuradas por ``icache``/``dcache``. Com ``miss_penalty = 0`` (o padrão) a
    temporização é a do MIC-1 sem cache. Passar ``instruction_port``/``data_port``
    troca as caches por portas próprias (útil nos testes).
    """

    def __init__(
        self,
        mode: DecodeMode | str = DecodeMode.ORIGINAL,
        *,
        memory: MainMemory | None = None,
        control_store: ControlStore | None = None,
        icache: CacheConfig | None = None,
        dcache: CacheConfig | None = None,
        instruction_port: MemoryPort | None = None,
        data_port: MemoryPort | None = None,
    ) -> None:
        self.mode = DecodeMode(mode)
        self.memory = memory if memory is not None else MainMemory()
        self.datapath = Datapath()
        store = control_store if control_store is not None else load_microprogram(self.mode)
        self.control = ControlUnit(store, self.mode)

        self.caches: CacheSystem | None = None
        if instruction_port is None and data_port is None:
            self.caches = CacheSystem(self.memory, icache, dcache)
            instruction_port, data_port = self.caches.icache, self.caches.dcache
        elif icache is not None or dcache is not None:
            raise ValueError("use caches (icache/dcache) ou portas próprias, não os dois")
        self.memory_controller = MemoryController(
            self.memory, instruction_port=instruction_port, data_port=data_port
        )
        self.source_map: dict[int, int] = {}
        self.reset()

    def reset(self, *, pc: int = 0, sp: int = DEFAULT_SP) -> dict[str, object]:
        """Zera registradores, contadores, caches e o acesso à memória (a memória fica)."""
        self.datapath.reset()
        self.datapath.registers[Reg.PC] = pc
        self.datapath.registers[Reg.SP] = sp
        self.control.reset()
        self.memory_controller.reset()
        if self.caches is not None:
            self.caches.reset()
        self.subcycle = 0  # último subciclo executado (0 = nenhum desde o reset)
        self.cycles = 0  # ciclos (microinstruções) completos
        self.instructions = 0  # macroinstruções completas
        self.stalled = False  # o último passo foi um subciclo de espera?
        self.stall_subcycles = 0  # total de subciclos de espera (misses de cache)
        self.elapsed_subcycles = 0  # subciclos de relógio, incluindo as esperas
        self.instruction_address = pc & ADDRESS_MASK  # onde começou a macroinstrução atual
        return self.snapshot()

    def set_mode(self, mode: DecodeMode | str) -> dict[str, object]:
        """Troca o modo de decodificação (e o microprograma) e reinicia a CPU."""
        self.mode = DecodeMode(mode)
        self.control = ControlUnit(load_microprogram(self.mode), self.mode)
        return self.reset()

    def configure_caches(
        self, icache: CacheConfig | None = None, dcache: CacheConfig | None = None
    ) -> dict[str, object]:
        """Troca a configuração das caches (a omitida continua igual) e reinicia a CPU."""
        if self.caches is None:
            raise RuntimeError("esta CPU usa portas próprias, sem caches")
        self.caches = CacheSystem(
            self.memory,
            icache if icache is not None else self.caches.icache.config,
            dcache if dcache is not None else self.caches.dcache.config,
        )
        self.memory_controller.instruction_port = self.caches.icache
        self.memory_controller.data_port = self.caches.dcache
        return self.reset()

    def load_program(
        self, words: Iterable[int], start: int = 0, *, source_map: Mapping[int, int] | None = None
    ) -> None:
        """Copia o programa para a memória e invalida as caches (o conteúdo mudou)."""
        self.memory.load(words, start)
        self.source_map = dict(source_map or {})
        if self.caches is not None:
            self.caches.invalidate()

    def load_assembly(self, source: str) -> Program:
        """Monta o fonte MAC-1, carrega na memória a partir do endereço 0 e reinicia a CPU."""
        program = assemble_mac1(source)
        self.load_program(program.words, source_map=program.source_map)
        self.reset()
        return program

    @property
    def at_instruction_boundary(self) -> bool:
        """Entre duas macroinstruções: ciclo concluído e MPC no endereço de busca."""
        return self.subcycle in (0, 4) and self.control.mpc == self.mode.fetch_address

    @property
    def at_halt_loop(self) -> bool:
        """O PC chegou ao laço final: a próxima instrução é um ``JUMP`` para o próprio endereço.

        Vale entre duas macroinstruções ou no Δw da busca (que ainda não mexeu no PC).
        """
        if self.control.mpc != self.mode.fetch_address or self.subcycle not in (0, 1, 4):
            return False
        pc = self.datapath.registers[Reg.PC] & ADDRESS_MASK
        return self.memory.read(pc) == _JUMP | pc

    @property
    def halted(self) -> bool:
        """Programa terminado: no laço final e com a memória livre.

        A escrita de um ``STOD`` logo antes do laço final só termina no Δw seguinte (e, num
        miss, depois dos subciclos de espera); o HALT espera por ela para que o resultado
        apareça na memória.
        """
        return self.at_halt_loop and not self.stalled and not self.memory_controller.busy

    def step_subcycle(self) -> dict[str, object]:
        events: list[Event] = []
        self._advance(events)
        return self.snapshot(events)

    def step_micro(self) -> dict[str, object]:
        """Executa até o fim do ciclo atual (um ciclo inteiro, se estiver entre ciclos)."""
        events: list[Event] = []
        self._finish_cycle(events)
        return self.snapshot(events)

    def step_macro(self) -> dict[str, object]:
        """Executa até o MPC voltar ao endereço de busca: termina a macroinstrução atual."""
        events: list[Event] = []
        start = self.cycles
        while True:
            self._finish_cycle(events)
            if self.control.mpc == self.mode.fetch_address:
                return self.snapshot(events)
            if self.cycles - start >= MAX_MACRO_CYCLES:
                raise RuntimeError(
                    f"a macroinstrução não terminou em {MAX_MACRO_CYCLES} ciclos "
                    "(o microprograma não voltou ao endereço de busca)"
                )

    def run(self, max_cycles: int = HALT_CYCLE_LIMIT) -> dict[str, object]:
        """Executa até o HALT ou até completar ``max_cycles`` ciclos nesta chamada."""
        events: list[Event] = []
        start = self.cycles
        while not self.halted and self.cycles - start < max_cycles:
            self._advance(events)
        return self.snapshot(events)

    def _finish_cycle(self, events: list[Event]) -> None:
        start = self.cycles
        while self.cycles == start:
            self._advance(events)

    def _advance(self, events: list[Event]) -> None:
        """Executa o próximo subciclo, ou um subciclo de espera se a memória atrasou."""
        self.elapsed_subcycles += 1
        # Entre um ciclo e outro (subciclo 0 ou 4) é o ponto em que um miss segura a CPU:
        # o próximo Δw precisa do dado que ainda está vindo da memória principal.
        if self.subcycle in (0, 4) and self.memory_controller.needs_stall():
            self._stall(events)
            return
        self.stalled = False

        sub = 1 if self.subcycle in (0, 4) else self.subcycle + 1
        self.subcycle = sub
        cycle = self.cycles + 1
        dp = self.datapath

        def emit(event: Event) -> None:
            events.append({"cycle": cycle, "subcycle": sub, **event})

        # Cada subciclo faz só a sua parte; a memória anda junto em todos eles.
        if sub == 1:  # Δw
            self._tick_memory(emit)
            if self.control.mpc == self.mode.fetch_address:  # começo de uma macroinstrução
                self.instruction_address = dp.registers[Reg.PC] & ADDRESS_MASK
            self.control.load_mir()
        elif sub == 2:  # Δx
            self._tick_memory(emit)
            dp.load_latches(self.control.mir)
        elif sub == 3:  # Δy
            self._tick_memory(emit)
            mir = self.control.mir
            dp.compute(mir)
            if mir.mar:
                emit({"type": "mar_load", "value": dp.mar, "source": _label(dp.mar_source)})
        else:  # Δz
            # Ordem do Δz: grava o barramento C, avisa a memória (RD/WR com o MAR e o MBR
            # já estáveis) e por último o MMUX escolhe o próximo MPC com as flags deste ciclo.
            mir = self.control.mir
            dp.write_back(mir)
            self._tick_memory(emit)
            for event in self.memory_controller.signal(
                rd=bool(mir.rd), wr=bool(mir.wr), mar=dp.mar, mbr=dp.mbr, mar_source=dp.mar_source
            ):
                emit(event.to_dict())
            target = self.control.sequence(dp.n, dp.z, dp.registers[Reg.IR])
            if self.control.dispatched:
                emit(
                    {
                        "type": "case_dispatch",
                        "opcode": dp.registers[Reg.IR] >> 12,
                        "target": target,
                    }
                )
            self.cycles += 1
            if target == self.mode.fetch_address:
                self.instructions += 1

    def _stall(self, events: list[Event]) -> None:
        """Subciclo de espera: a CPU fica parada enquanto o bloco vem da memória principal."""
        self.stalled = True
        self.stall_subcycles += 1
        status = self.memory_controller.status()
        cycle = self.cycles + 1

        def emit(event: Event) -> None:
            events.append({"cycle": cycle, "subcycle": 0, **event})

        emit(
            {
                "type": "stall",
                "op": status["op"],
                "kind": status["kind"],
                "address": status["address"],
            }
        )
        self._tick_memory(emit)

    def _tick_memory(self, emit: Callable[[Event], None]) -> None:
        event = self.memory_controller.tick()
        if event is None:
            return
        if event.request.op is MemOp.READ:
            assert event.value is not None
            self.datapath.load_mbr_from_memory(event.value)
        emit(event.to_dict())

    def snapshot(self, events: list[Event] | None = None) -> dict[str, object]:
        """Estado completo da CPU, só com tipos JSON (int, str, bool, None, list, dict)."""
        dp = self.datapath
        step = self.control.current
        in_progress = self.subcycle in (1, 2, 3)
        ir = dp.registers[Reg.IR]
        line = self.source_map.get(self.instruction_address)
        return {
            "mode": self.mode.value,
            "halted": self.halted,
            "halt_loop": self.at_halt_loop,
            "clock": {
                "cycle": self.cycles + 1 if in_progress else self.cycles,
                "subcycle": self.subcycle,
                "subcycle_name": "espera" if self.stalled else SUBCYCLE_NAMES.get(self.subcycle),
                "stalled": self.stalled,
                "completed_cycles": self.cycles,
                "completed_instructions": self.instructions,
                "stall_subcycles": self.stall_subcycles,
                "elapsed_subcycles": self.elapsed_subcycles,
            },
            "mpc": self.control.mpc,
            "mir": None
            if step is None
            else {
                "address": step.address,
                "word": step.instruction.encode(),
                "bits": step.instruction.bit_groups(),
                "fields": step.instruction.fields(),
                "mal": step.source,
                "comment": step.comment,
            },
            "registers": dp.registers.snapshot(),
            "latches": {"A": dp.latch_a, "B": dp.latch_b},
            "buses": {
                "A": {"source": _label(dp.a_source), "value": dp.a_bus},
                "B": {"source": _label(dp.b_source), "value": dp.b_bus},
                "C": {"target": _label(dp.c_target), "value": dp.c_bus},
            },
            "amux": {"select": "MBR" if dp.amux_from_mbr else "Latch A", "value": dp.amux_out},
            "alu": {"op": dp.alu_op.symbol, "value": dp.alu_out},
            "shifter": {"op": dp.shift.symbol, "value": dp.shifter_out},
            "flags": {"N": dp.n, "Z": dp.z},
            "mar": {"value": dp.mar, "source": _label(dp.mar_source), "from_pc": dp.mar_from_pc},
            "mbr": dp.mbr,
            "memory": self.memory_controller.status(),
            "caches": None if self.caches is None else self.caches.snapshot(),
            "macro": {
                "address": self.instruction_address,
                "ir": ir,
                "text": disassemble(ir),
                "line": line,
                "at_boundary": self.at_instruction_boundary,
            },
            "program": {
                "source_map": {
                    str(address): src for address, src in sorted(self.source_map.items())
                },
                "line": line,
            },
            "active": self._active_components(),
            "events": list(events or []),
        }

    def _active_components(self) -> list[str]:
        """Componentes que trabalharam no último subciclo, para o diagrama acender."""
        step = self.control.current
        if self.subcycle == 0 or step is None:
            return []
        mir, dp = step.instruction, self.datapath
        active: list[str]
        if self.stalled:
            active = ["stall"]
        elif self.subcycle == 1:
            active = ["mpc", "control_store", "mir"]
        elif self.subcycle == 2:
            active = ["decoder_a", "decoder_b", "bus_a", "bus_b", "latch_a", "latch_b"]
            active += [f"reg:{_label(dp.a_source)}", f"reg:{_label(dp.b_source)}"]
        elif self.subcycle == 3:
            active = ["amux", "alu", "shifter", "flags"] + (["mbr"] if mir.amux else [])
            active += ["mar"] if mir.mar else []
        else:
            active = ["mmux", "mpc"]
            if mir.enc:
                active += ["decoder_c", "bus_c", f"reg:{_label(dp.c_target)}"]
            if mir.mbr:
                active += ["bus_c", "mbr"]
        status = self.memory_controller.status()
        if status["busy"]:
            active.append("memory")
            if self.caches is not None:
                active.append("icache" if status["kind"] == "instruction" else "dcache")
        return list(dict.fromkeys(active))


def _label(register: Reg | None) -> str | None:
    return None if register is None else register.label
