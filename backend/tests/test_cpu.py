"""CPU completa: subciclos, memória, snapshots, as 23 instruções e o exemplo da Aula 14."""

import json
from dataclasses import dataclass, field

import pytest

from mic1.assembler.mal import assemble_mal
from mic1.control import ControlStore, DecodeMode
from mic1.cpu import CPU, DEFAULT_SP
from mic1.datapath import Reg
from mic1.isa import BY_MNEMONIC, encode
from mic1.memory import MainMemory

MODES = [DecodeMode.ORIGINAL, DecodeMode.CASE]


def reg(cpu: CPU, name: str | Reg) -> int:
    return cpu.datapath.registers[name]


def settle(cpu: CPU) -> None:
    """Deixa terminar um acesso em andamento (o ``wr`` do STOD termina no Δw seguinte)."""
    while cpu.memory_controller.busy:
        cpu.step_subcycle()


# --- Relógio e snapshots ----------------------------------------------------------------------


def test_subcycles_run_in_the_order_w_x_y_z():
    cpu = CPU()
    names = [cpu.step_subcycle()["clock"]["subcycle_name"] for _ in range(8)]
    assert names == ["Δw", "Δx", "Δy", "Δz"] * 2


def test_step_micro_finishes_the_current_cycle():
    cpu = CPU()
    cpu.step_subcycle()
    cpu.step_subcycle()
    snapshot = cpu.step_micro()
    assert (snapshot["clock"]["subcycle"], snapshot["clock"]["completed_cycles"]) == (4, 1)
    assert cpu.step_micro()["clock"]["completed_cycles"] == 2


def test_snapshot_is_json_serializable_at_every_subcycle():
    cpu = CPU()
    cpu.load_program([encode("LOCO", 3), encode("PUSH"), encode("POP")])
    for _ in range(4 * 40):
        json.dumps(cpu.step_subcycle())


def test_snapshot_uses_the_names_of_the_slides():
    cpu = CPU()
    snapshot = cpu.step_micro()  # microinstrução 0: mar := pc; rd;
    assert list(snapshot["registers"])[:8] == ["PC", "AC", "SP", "IR", "TIR", "0", "+1", "-1"]
    assert snapshot["mir"]["mal"] == "mar := pc; rd;"
    assert snapshot["mir"]["bits"] == "0 00 00 00 0 1 1 0 0 0000 0000 0000 00000001"
    assert snapshot["mar"] == {"value": 0, "source": "PC", "from_pc": True}
    assert snapshot["buses"]["B"] == {"source": "PC", "value": 0}
    assert snapshot["mpc"] == 1


def test_set_mode_swaps_the_microprogram():
    cpu = CPU()
    snapshot = cpu.set_mode("case")
    assert (snapshot["mode"], snapshot["mpc"]) == ("case", 252)


# --- Busca e temporização da memória ---------------------------------------------------------


def test_fetch_takes_six_subcycles_from_rd_to_mbr():
    """Slide "Busca na memória": RD em (1, Δz), dado no MBR no fim de (3, Δw)."""
    cpu = CPU()
    cpu.load_program([encode("LOCO", 0x123)])
    mbr_values, events = [], []
    for _ in range(9):
        snapshot = cpu.step_subcycle()
        mbr_values.append(snapshot["mbr"])
        events += [
            (event["cycle"], event["subcycle"], event["type"]) for event in snapshot["events"]
        ]

    assert (1, 4, "memory_start") in events
    assert (2, 4, "memory_continue") in events
    assert (3, 1, "memory_complete") in events
    assert mbr_values[:8] == [0] * 8  # subciclos globais 1–8
    assert mbr_values[8] == 0x7123  # subciclo global 9 = 6º subciclo desde o início (4)


def test_fetch_and_data_accesses_are_classified_for_the_l1_caches():
    cpu = CPU()
    cpu.load_program([encode("LODD", 9)])
    snapshot = cpu.step_macro()
    starts = [event for event in snapshot["events"] if event["type"] == "memory_start"]
    assert [(event["kind"], event["mar_source"]) for event in starts] == [
        ("instruction", "PC"),
        ("data", "IR"),
    ]


def test_separate_routes_receive_fetches_and_data():
    class Counting:
        def __init__(self, memory: MainMemory) -> None:
            self.memory, self.reads, self.writes = memory, [], []

        def read(self, address: int) -> int:
            self.reads.append(address)
            return self.memory.read(address)

        def write(self, address: int, value: int) -> None:
            self.writes.append((address, value))
            self.memory.write(address, value)

    memory = MainMemory()
    i_route, d_route = Counting(memory), Counting(memory)
    cpu = CPU(memory=memory, instruction_port=i_route, data_port=d_route)
    cpu.load_program([encode("LOCO", 7), encode("STOD", 20), encode("LODD", 20)])
    for _ in range(3):
        cpu.step_macro()
    settle(cpu)

    assert i_route.reads == [0, 1, 2] and i_route.writes == []
    assert d_route.writes == [(20, 7)] and d_route.reads == [20]


def test_rd_not_held_aborts_the_read():
    store = ControlStore(assemble_mal("0: mar := pc; rd;\n1: goto 1;"))
    cpu = CPU(control_store=store)
    cpu.load_program([0x1234])
    events = [event["type"] for _ in range(3) for event in cpu.step_micro()["events"]]
    assert "memory_abort" in events
    assert cpu.datapath.mbr == 0


# --- Exemplo da Aula 14: Se (i <= n) então m := a[i] -------------------------------------------

# Endereços das variáveis n, i, m e do vetor a; SAIDA é o rótulo "saída" do slide.
ADDR_N, ADDR_I, ADDR_M, ADDR_A, SAIDA = 20, 21, 22, 30, 8
EXAMPLE = [
    encode("LODD", ADDR_N),  # 0: ac := n
    encode("SUBD", ADDR_I),  # 1: ac := n - i
    encode("JNEG", SAIDA),  # 2: se n - i < 0, sai
    encode("LOCO", ADDR_A),  # 3: ac := &a
    encode("ADDD", ADDR_I),  # 4: ac := &a + i
    encode("PSHI"),  # 5: empilha a[i]
    encode("POP"),  # 6: ac := a[i]
    encode("STOD", ADDR_M),  # 7: m := a[i]
    encode("JUMP", SAIDA),  # 8: saída (laço)
]
EXAMPLE_TEXT = ["LODD 20", "SUBD 21", "JNEG 8", "LOCO 30", "ADDD 21", "PSHI", "POP", "STOD 22"]


def run_example(mode: DecodeMode, *, i: int, n: int) -> tuple[CPU, list[tuple[str, int]]]:
    cpu = CPU(mode)
    cpu.load_program(EXAMPLE)
    cpu.memory.load([n, i, 0], ADDR_N)
    cpu.memory.load([10, 20, 30, 40, 50], ADDR_A)
    costs = []
    while not (reg(cpu, Reg.PC) == SAIDA and cpu.at_instruction_boundary):
        before = cpu.cycles
        text = cpu.step_macro()["macro"]["text"]
        costs.append((text, cpu.cycles - before))
    return cpu, costs


@pytest.mark.parametrize(
    ("mode", "exit_cycles", "full_cycles"),
    [(DecodeMode.ORIGINAL, 27, 76), (DecodeMode.CASE, 18, 56)],
)
def test_example_cycle_counts(mode, exit_cycles, full_cycles):
    cpu, _ = run_example(mode, i=7, n=5)  # i > n: LODD, SUBD, JNEG
    assert (cpu.cycles, cpu.instructions) == (exit_cycles, 3)
    cpu, _ = run_example(mode, i=2, n=5)  # i <= n: as 8 instruções
    assert (cpu.cycles, cpu.instructions) == (full_cycles, 8)


@pytest.mark.parametrize(
    ("mode", "costs"),
    [
        (DecodeMode.ORIGINAL, [9, 10, 8, 7, 9, 13, 12, 8]),
        (DecodeMode.CASE, [6, 7, 5, 4, 6, 12, 11, 5]),
    ],
)
def test_example_cost_of_each_instruction(mode, costs):
    _, measured = run_example(mode, i=2, n=5)
    assert [text for text, _ in measured] == EXAMPLE_TEXT
    assert [cycles for _, cycles in measured] == costs


@pytest.mark.parametrize("mode", MODES)
def test_example_computes_m_equals_a_of_i(mode):
    cpu, _ = run_example(mode, i=2, n=5)
    assert cpu.memory_controller.busy  # o wr do STOD termina no Δw seguinte
    settle(cpu)
    assert cpu.memory.read(ADDR_M) == 30
    assert (reg(cpu, Reg.AC), reg(cpu, Reg.SP)) == (30, DEFAULT_SP)


@pytest.mark.parametrize("mode", MODES)
def test_example_skips_when_i_is_greater_than_n(mode):
    cpu, _ = run_example(mode, i=7, n=5)
    settle(cpu)
    assert cpu.memory.read(ADDR_M) == 0
    assert reg(cpu, Reg.AC) == 0xFFFE  # 5 - 7


# --- As 23 instruções MAC-1, nos dois modos -----------------------------------------------------


@dataclass
class Case:
    name: str
    program: list[int]
    expect: dict[str | int, int]  # registradores pelo rótulo ("AC") ou endereços de memória (int)
    memory: dict[int, int] = field(default_factory=dict)
    sp: int = 200


NEGATIVE = 0xFFFF
CASES = [
    Case("LODD", [encode("LODD", 100)], {"AC": 1234}, {100: 1234}),
    Case("STOD", [encode("LOCO", 77), encode("STOD", 100)], {100: 77}),
    Case("ADDD", [encode("LOCO", 5), encode("ADDD", 100)], {"AC": 12}, {100: 7}),
    Case("SUBD", [encode("LOCO", 5), encode("SUBD", 100)], {"AC": 0xFFFE}, {100: 7}),
    Case("JPOS desvia", [encode("LOCO", 0), encode("JPOS", 40)], {"PC": 40}),
    Case("JPOS segue", [encode("LODD", 100), encode("JPOS", 40)], {"PC": 2}, {100: NEGATIVE}),
    Case("JZER desvia", [encode("LOCO", 0), encode("JZER", 40)], {"PC": 40}),
    Case("JZER segue", [encode("LOCO", 3), encode("JZER", 40)], {"PC": 2}),
    Case("JUMP", [encode("JUMP", 42)], {"PC": 42}),
    Case("LOCO", [encode("LOCO", 4095)], {"AC": 4095}),
    Case("LODL", [encode("LODL", 3)], {"AC": 99}, {203: 99}),
    Case("STOL", [encode("LOCO", 55), encode("STOL", 2)], {202: 55}),
    Case("ADDL", [encode("LOCO", 5), encode("ADDL", 1)], {"AC": 15}, {201: 10}),
    Case("SUBL", [encode("LOCO", 5), encode("SUBL", 1)], {"AC": 0xFFFB}, {201: 10}),
    Case("JNEG desvia", [encode("LODD", 100), encode("JNEG", 40)], {"PC": 40}, {100: NEGATIVE}),
    Case("JNEG segue", [encode("LOCO", 1), encode("JNEG", 40)], {"PC": 2}),
    Case("JNZE desvia", [encode("LOCO", 1), encode("JNZE", 40)], {"PC": 40}),
    Case("JNZE segue", [encode("LOCO", 0), encode("JNZE", 40)], {"PC": 2}),
    Case("CALL", [encode("CALL", 50)], {"PC": 50, "SP": 199, 199: 1}),
    Case("PSHI", [encode("LOCO", 100), encode("PSHI")], {"SP": 199, 199: 321}, {100: 321}),
    Case("POPI", [encode("LOCO", 100), encode("POPI")], {"SP": 200, 100: 777}, {199: 777}, 199),
    Case("PUSH", [encode("LOCO", 9), encode("PUSH")], {"SP": 199, 199: 9}),
    Case("POP", [encode("POP")], {"AC": 4321, "SP": 200}, {199: 4321}, 199),
    Case("RETN", [encode("RETN")], {"PC": 60, "SP": 200}, {199: 60}, 199),
    Case("SWAP", [encode("LOCO", 7), encode("SWAP")], {"AC": 200, "SP": 7}),
    Case("INSP", [encode("INSP", 10)], {"SP": 210}),
    Case("DESP", [encode("DESP", 10)], {"SP": 190}),
]


def run_case(case: Case, mode: DecodeMode) -> CPU:
    cpu = CPU(mode)
    cpu.reset(sp=case.sp)
    cpu.load_program(case.program)
    for address, value in case.memory.items():
        cpu.memory.write(address, value)
    for _ in case.program:
        cpu.step_macro()
    return cpu


def test_cases_cover_all_23_instructions():
    assert {case.name.split()[0] for case in CASES} == set(BY_MNEMONIC)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_every_mac1_instruction(case, mode):
    cpu = run_case(case, mode)
    cycles = cpu.cycles
    settle(cpu)
    assert cpu.instructions == len(case.program)
    for key, expected in case.expect.items():
        actual = cpu.memory.read(key) if isinstance(key, int) else reg(cpu, key)
        assert actual == expected, f"{key}: {actual:#06x} != {expected:#06x}"
    assert cycles > 0


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_case_mode_is_never_slower(case):
    original = run_case(case, DecodeMode.ORIGINAL).cycles
    with_case = run_case(case, DecodeMode.CASE).cycles
    assert with_case < original
