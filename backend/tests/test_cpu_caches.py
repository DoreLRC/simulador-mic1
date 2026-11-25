"""Integração CPU + caches + montador: stalls, cache desligada, exemplos e snapshot."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from mic1.cache import CacheConfig
from mic1.control import DecodeMode
from mic1.cpu import CPU
from mic1.datapath import Reg
from mic1.isa import encode
from mic1.memory import MainMemory

EXAMPLES = Path(__file__).resolve().parents[2] / "programs"
DEMO_ICACHE = CacheConfig(lines=16, block_size=2, miss_penalty=4)
DEMO_DCACHE = CacheConfig(lines=8, block_size=2, miss_penalty=4)


def run_until(cpu: CPU, address: int) -> None:
    """Executa macroinstruções até o PC chegar a ``address`` e deixa a memória terminar."""
    while not (cpu.datapath.registers[Reg.PC] == address and cpu.at_instruction_boundary):
        cpu.step_macro()
    while cpu.memory_controller.busy:
        cpu.step_subcycle()


def example(name: str) -> str:
    return (EXAMPLES / name).read_text(encoding="utf-8")


# --- Temporização com caches ----------------------------------------------------------------


def test_default_caches_have_no_penalty():
    """Penalidade 0 = temporização dos slides (todos os testes da CPU continuam valendo)."""
    cpu = CPU()
    assert cpu.caches is not None
    assert cpu.caches.icache.config.miss_penalty == cpu.caches.dcache.config.miss_penalty == 0


def test_miss_stalls_right_before_the_delta_w_that_receives_the_word():
    penalty = 4
    cpu = CPU(icache=CacheConfig(miss_penalty=penalty))
    cpu.load_program([encode("LOCO", 0x123), encode("LOCO", 0x45)])
    names, mbr = [], []
    for _ in range(9 + penalty):
        snapshot = cpu.step_subcycle()
        names.append(snapshot["clock"]["subcycle_name"])
        mbr.append(snapshot["mbr"])

    assert names[:8] == ["Δw", "Δx", "Δy", "Δz"] * 2
    assert names[8:12] == ["espera"] * penalty  # o bloco vem da memória principal
    assert names[12] == "Δw"
    assert mbr[11] == 0 and mbr[12] == 0x7123  # dado entregue após 6 + 4 subciclos
    assert cpu.stall_subcycles == penalty


def test_hit_needs_no_stall():
    cpu = CPU(icache=CacheConfig(miss_penalty=4, block_size=2))
    cpu.load_program([encode("LOCO", 1), encode("LOCO", 2)])
    cpu.step_macro()
    assert cpu.stall_subcycles == 4  # 1ª busca: miss
    cpu.step_macro()
    assert cpu.stall_subcycles == 4  # 2ª busca: mesmo bloco, hit
    assert cpu.caches is not None
    assert (cpu.caches.icache.hits, cpu.caches.icache.misses) == (1, 1)


def test_disabled_caches_pay_the_penalty_on_every_access():
    config = CacheConfig(enabled=False, miss_penalty=3)
    cpu = CPU(icache=config, dcache=config)
    cpu.load_program([encode("LOCO", 5), encode("STOD", 10), encode("LODD", 10)])
    for _ in range(3):
        cpu.step_macro()
    while cpu.memory_controller.busy:
        cpu.step_subcycle()
    assert cpu.stall_subcycles == 3 * 5  # 3 buscas + 1 escrita + 1 leitura
    assert cpu.datapath.registers[Reg.AC] == 5


def test_stalls_do_not_change_the_number_of_cycles():
    def run(penalty: int) -> CPU:
        config = CacheConfig(miss_penalty=penalty)
        cpu = CPU(icache=config, dcache=config)
        cpu.load_program([encode("LOCO", 5), encode("STOD", 10), encode("LODD", 10)])
        for _ in range(3):
            cpu.step_macro()
        return cpu

    fast, slow = run(0), run(6)
    assert fast.cycles == slow.cycles
    assert slow.stall_subcycles > 0
    assert slow.elapsed_subcycles - fast.elapsed_subcycles == slow.stall_subcycles


# --- Snapshot e configuração -------------------------------------------------------------------


def test_snapshot_exports_both_caches():
    cpu = CPU()
    cpu.load_program([encode("LODD", 9)])
    snapshot = json.loads(json.dumps(cpu.step_macro()))
    icache, dcache = snapshot["caches"]["instruction"], snapshot["caches"]["data"]

    assert icache["last_access"] == {
        "result": "miss",
        "op": "rd",
        "address": 0,
        "tag": 0,
        "index": 0,
        "offset": 0,
        "way": 0,
        "evicted_tag": None,
        "bypass": False,
    }
    assert (icache["hits"], icache["misses"], icache["hit_rate"]) == (0, 1, 0.0)
    assert dcache["last_access"]["address"] == 9
    assert (dcache["last_access"]["index"], dcache["last_access"]["offset"]) == (4, 1)
    assert any(line["valid"] for line in icache["lines"])


def test_custom_ports_replace_the_cache_system():
    memory = MainMemory()
    cpu = CPU(memory=memory, instruction_port=memory, data_port=memory)
    assert cpu.caches is None and cpu.snapshot()["caches"] is None
    with pytest.raises(ValueError):
        CPU(instruction_port=memory, icache=CacheConfig())


def test_configure_caches_rebuilds_and_resets():
    cpu = CPU()
    snapshot = cpu.configure_caches(dcache=CacheConfig(lines=4, mapping="2way"))
    assert snapshot["caches"]["data"]["config"]["mapping"] == "2way"
    assert snapshot["caches"]["instruction"]["config"]["lines"] == 8  # mantida


# --- Demonstração das caches (examples/cache_laco_soma.mac1) ------------------------------------


def run_demo(*, enabled: bool = True) -> tuple[CPU, dict[str, int]]:
    cpu = CPU(
        icache=replace(DEMO_ICACHE, enabled=enabled), dcache=replace(DEMO_DCACHE, enabled=enabled)
    )
    program = cpu.load_assembly(example("cache_laco_soma.mac1"))
    run_until(cpu, program.symbols["fim"])
    return cpu, program.symbols


def test_cache_demo_has_only_cold_misses():
    cpu, symbols = run_demo()
    assert cpu.caches is not None
    icache, dcache = cpu.caches.icache, cpu.caches.dcache

    assert cpu.memory.read(symbols["soma"]) == 30
    # 18 palavras de código = 9 blocos de 2 palavras, cada um buscado uma única vez.
    assert icache.misses == 9
    assert icache.hits == cpu.instructions - 9
    # 6 blocos de dados lidos pela 1ª vez (k/soma, um/tam, voltas/v0, v1/v2, v3, pilha)
    # + 2 escritas que chegam antes da 1ª leitura do bloco (k e pilha): no-write-allocate.
    assert dcache.misses == 8
    assert dcache.hit_rate > 0.9
    assert cpu.stall_subcycles == 4 * (9 + 8)


def test_cache_demo_misses_only_on_the_first_pass():
    cpu = CPU(icache=DEMO_ICACHE, dcache=DEMO_DCACHE)
    program = cpu.load_assembly(example("cache_laco_soma.mac1"))
    assert cpu.caches is not None
    cpu.step_macro()
    while not (
        cpu.datapath.registers[Reg.PC] == program.symbols["inicio"] and cpu.at_instruction_boundary
    ):
        cpu.step_macro()  # fim da 1ª varredura
    after_first_pass = (cpu.caches.icache.misses, cpu.caches.dcache.misses)
    hits_after_first_pass = cpu.caches.icache.hits

    run_until(cpu, program.symbols["fim"])
    assert after_first_pass == (9, 8)
    assert (cpu.caches.icache.misses, cpu.caches.dcache.misses) == after_first_pass
    assert cpu.caches.icache.hits > hits_after_first_pass


def test_cache_demo_with_the_caches_turned_off():
    cpu, symbols = run_demo(enabled=False)
    assert cpu.caches is not None
    icache, dcache = cpu.caches.icache, cpu.caches.dcache
    assert cpu.memory.read(symbols["soma"]) == 30
    assert (icache.hits, icache.misses) == (0, cpu.instructions)
    assert dcache.hits == 0
    assert cpu.stall_subcycles == 4 * (icache.misses + dcache.misses)


def test_caches_make_the_demo_faster_without_changing_the_microprogram():
    with_caches, _ = run_demo()
    without, _ = run_demo(enabled=False)
    assert with_caches.cycles == without.cycles
    assert with_caches.elapsed_subcycles < without.elapsed_subcycles


# --- Exemplo "se i <= n" montado a partir do arquivo ------------------------------------------


@pytest.mark.parametrize(("mode", "cycles"), [(DecodeMode.ORIGINAL, 76), (DecodeMode.CASE, 56)])
def test_se_i_example_file(mode, cycles):
    cpu = CPU(mode)
    program = cpu.load_assembly(example("se_i_menor_igual_n.mac1"))
    run_until(cpu, program.symbols["saida"])
    assert cpu.cycles == cycles
    assert cpu.memory.read(program.symbols["m"]) == 30


def test_snapshot_maps_the_current_instruction_to_its_source_line():
    source = example("se_i_menor_igual_n.mac1")
    lines = source.splitlines()
    cpu = CPU()
    cpu.load_assembly(source)

    snapshot = cpu.step_micro()  # primeira microinstrução da busca de LODD n
    line = snapshot["macro"]["line"]
    assert lines[line - 1].strip().startswith("LODD n")
    assert snapshot["program"]["source_map"]["0"] == line == snapshot["program"]["line"]

    cpu.step_macro()  # termina LODD
    snapshot = cpu.step_micro()  # começa SUBD
    assert lines[snapshot["macro"]["line"] - 1].strip().startswith("SUBD i")


# --- HALT e run() ------------------------------------------------------------------------------


def test_run_stops_at_the_final_loop_once_the_last_write_lands():
    cpu = CPU()
    program = cpu.load_assembly(example("se_i_menor_igual_n.mac1"))
    snapshot = cpu.run()
    assert cpu.halted and snapshot["halted"] and snapshot["halt_loop"]
    assert cpu.cycles == 76
    assert cpu.memory.read(program.symbols["m"]) == 30
    # A escrita do STOD termina no Δw da busca do JUMP final: a CPU para ali.
    assert snapshot["clock"]["subcycle_name"] == "Δw"
    assert snapshot["macro"]["address"] == program.symbols["saida"]


def test_reaching_the_final_loop_with_a_pending_write_is_not_halted_yet():
    cpu = CPU()
    program = cpu.load_assembly(example("se_i_menor_igual_n.mac1"))
    while not (
        cpu.datapath.registers[Reg.PC] == program.symbols["saida"] and cpu.at_instruction_boundary
    ):
        cpu.step_macro()
    assert cpu.at_halt_loop and cpu.memory_controller.busy
    assert not cpu.halted
    assert cpu.memory.read(program.symbols["m"]) == 0  # o STOD ainda não terminou


def test_run_respects_the_cycle_limit():
    cpu = CPU()
    cpu.load_assembly("laco: LOCO 1\n      JUMP laco\n")
    snapshot = cpu.run(max_cycles=300)
    assert cpu.cycles == 300
    assert not snapshot["halted"] and not cpu.halted


def test_a_program_that_starts_halted_does_not_run():
    cpu = CPU()
    cpu.load_assembly("fim: JUMP fim")
    assert cpu.halted
    snapshot = cpu.run()
    assert snapshot["clock"]["elapsed_subcycles"] == 0


@pytest.mark.parametrize(("enabled", "stall"), [(True, 68), (False, 1128)])
def test_run_gives_the_cache_demo_numbers_with_16_line_caches(enabled, stall):
    config = CacheConfig(enabled=enabled, lines=16, block_size=2, miss_penalty=4)
    cpu = CPU(icache=config, dcache=config)
    program = cpu.load_assembly(example("cache_laco_soma.mac1"))
    cpu.run()
    assert cpu.halted
    assert (cpu.cycles, cpu.instructions, cpu.stall_subcycles) == (1370, 150, stall)
    assert cpu.memory.read(program.symbols["soma"]) == 30
