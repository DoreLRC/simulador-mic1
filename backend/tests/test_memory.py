"""Memória principal (4096 × 16 bits) e a temporização de 6 subciclos do controlador."""

import pytest

from mic1.datapath import Reg
from mic1.memory import LATENCY_SUBCYCLES, MEMORY_WORDS, MainMemory, MemoryController


class RecordingPort:
    def __init__(self, memory: MainMemory) -> None:
        self.memory = memory
        self.log: list[tuple[str, int]] = []

    def read(self, address: int) -> int:
        self.log.append(("read", address))
        return self.memory.read(address)

    def write(self, address: int, value: int) -> None:
        self.log.append(("write", address))
        self.memory.write(address, value)


def _signal(controller: MemoryController, *, rd=False, wr=False, mar=0, mbr=0, source=Reg.IR):
    return controller.signal(rd=rd, wr=wr, mar=mar, mbr=mbr, mar_source=source)


# --- Memória principal ----------------------------------------------------------------------


def test_main_memory_has_4096_words_of_16_bits():
    memory = MainMemory()
    memory.write(4095, 0x1_2345)
    assert len(memory) == MEMORY_WORDS == 4096
    assert memory.read(4095) == 0x2345


@pytest.mark.parametrize("address", [-1, 4096])
def test_addresses_have_12_bits(address):
    with pytest.raises(IndexError):
        MainMemory().read(address)


def test_load_and_dump():
    memory = MainMemory()
    memory.load([1, 2, 3], start=10)
    assert memory.dump(10, 3) == [1, 2, 3]


# --- Temporização (slide "Busca na memória de MIC-1") ------------------------------------


def test_read_completes_six_subcycles_after_it_starts():
    memory = MainMemory()
    memory.write(7, 0xCAFE)
    controller = MemoryController(memory)

    (start,) = _signal(controller, rd=True, mar=7)  # Δz do ciclo k → 1º subciclo
    assert start.kind == "start"
    assert [controller.tick() for _ in range(4)] == [None] * 4  # Δw Δx Δy Δz de k+1 → 2º–5º
    (held,) = _signal(controller, rd=True, mar=7)  # RD mantido em k+1
    assert held.kind == "continue"

    done = controller.tick()  # Δw de k+2 → 6º subciclo
    assert done is not None and (done.kind, done.value) == ("complete", 0xCAFE)
    assert not controller.busy
    assert LATENCY_SUBCYCLES == 6


def test_access_is_aborted_when_the_signal_is_not_held():
    controller = MemoryController(MainMemory())
    _signal(controller, rd=True, mar=7)
    for _ in range(4):
        controller.tick()
    (abort,) = _signal(controller)  # RD = 0 em k+1
    assert abort.kind == "abort"
    assert not controller.busy
    assert controller.tick() is None


def test_write_lands_on_the_sixth_subcycle_with_the_mbr_of_the_start():
    memory = MainMemory()
    controller = MemoryController(memory)
    _signal(controller, wr=True, mar=3, mbr=0x1111)
    for _ in range(4):
        controller.tick()
    _signal(controller, wr=True, mar=3, mbr=0x2222)  # continuação: endereço e dado já fixados
    assert memory.read(3) == 0  # ainda não gravou

    controller.tick()
    assert memory.read(3) == 0x1111


def test_rd_and_wr_together_are_rejected():
    with pytest.raises(ValueError):
        _signal(MemoryController(MainMemory()), rd=True, wr=True)


def test_status_reports_the_access_in_progress():
    controller = MemoryController(MainMemory())
    assert controller.status()["busy"] is False
    _signal(controller, rd=True, mar=5, source=Reg.PC)
    controller.tick()
    status = controller.status()
    assert (status["busy"], status["op"], status["address"]) == (True, "rd", 5)
    assert (status["kind"], status["elapsed"], status["latency"]) == ("instruction", 2, 6)


# --- Stall (miss de cache) ------------------------------------------------------------------


class SlowPort(RecordingPort):
    """Porta temporizada: todo acesso custa 3 subciclos de espera (como um miss)."""

    def stall_for(self, op, address) -> int:
        return 3


def test_timed_port_delays_completion_and_asks_the_cpu_to_wait():
    memory = MainMemory()
    memory.write(7, 0xBEEF)
    controller = MemoryController(memory, data_port=SlowPort(memory))

    (start,) = _signal(controller, rd=True, mar=7)
    assert start.stall == 3
    for _ in range(4):
        controller.tick()  # 2º–5º subciclos
    _signal(controller, rd=True, mar=7)
    assert controller.needs_stall()  # o 6º subciclo não basta: a CPU espera

    assert [controller.tick() for _ in range(3)] == [None] * 3  # 3 subciclos de espera
    assert controller.status()["waiting"] is True
    assert not controller.needs_stall()
    done = controller.tick()  # 6 + 3 = 9º subciclo
    assert done is not None and (done.value, done.stall) == (0xBEEF, 3)


# --- Rotas I-Cache / D-Cache ----------------------------------------------------------------


def _complete(controller: MemoryController, **signals) -> None:
    _signal(controller, **signals)
    for _ in range(4):
        controller.tick()
    _signal(controller, **signals)
    controller.tick()


def test_reads_with_mar_from_pc_use_the_instruction_route():
    memory = MainMemory()
    instructions, data = RecordingPort(memory), RecordingPort(memory)
    controller = MemoryController(memory, instruction_port=instructions, data_port=data)

    _complete(controller, rd=True, mar=5, source=Reg.PC)  # mar := pc; rd
    _complete(controller, rd=True, mar=9, source=Reg.SP)  # mar := sp; rd
    _complete(controller, wr=True, mar=9, source=Reg.SP)  # mar := sp; wr

    assert instructions.log == [("read", 5)]
    assert data.log == [("read", 9), ("write", 9)]
