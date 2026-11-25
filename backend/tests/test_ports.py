"""Ganchos de memória: classificação instrução/dado pela origem do MAR e roteamento das portas."""

import pytest

from mic1.datapath import Reg
from mic1.memory import MainMemory
from mic1.ports import (
    AccessKind,
    MemOp,
    MemoryPort,
    MemoryRequest,
    classify,
    dispatch,
    route,
    stall_for,
)


class RecordingPort:
    """Porta mínima que registra os acessos — faz o papel da futura cache."""

    def __init__(self) -> None:
        self.words: dict[int, int] = {}
        self.log: list[tuple[str, int]] = []

    def read(self, address: int) -> int:
        self.log.append(("read", address))
        return self.words.get(address, 0)

    def write(self, address: int, value: int) -> None:
        self.log.append(("write", address))
        self.words[address] = value


def test_read_with_mar_from_pc_is_an_instruction_fetch():
    assert classify(MemOp.READ, Reg.PC) is AccessKind.INSTRUCTION


@pytest.mark.parametrize("source", [Reg.IR, Reg.SP, Reg.AC, Reg.A, None])
def test_other_reads_are_data(source):
    assert classify(MemOp.READ, source) is AccessKind.DATA


def test_writes_are_always_data():
    assert classify(MemOp.WRITE, Reg.PC) is AccessKind.DATA


def test_request_from_signals_classifies_the_access():
    request = MemoryRequest.from_signals(MemOp.READ, mar=0x123, mbr=7, mar_source=Reg.PC)
    assert request == MemoryRequest(MemOp.READ, 0x123, AccessKind.INSTRUCTION, None, Reg.PC)


def test_write_request_carries_the_mbr():
    request = MemoryRequest.from_signals(MemOp.WRITE, mar=5, mbr=0xBEEF, mar_source=Reg.IR)
    assert (request.kind, request.data) == (AccessKind.DATA, 0xBEEF)


def test_request_validation():
    with pytest.raises(ValueError, match="12 bits"):
        MemoryRequest(MemOp.READ, 0x1000, AccessKind.DATA)
    with pytest.raises(ValueError):
        MemoryRequest(MemOp.WRITE, 1, AccessKind.DATA)  # WR sem dado
    with pytest.raises(ValueError):
        MemoryRequest(MemOp.READ, 1, AccessKind.DATA, data=3)  # RD com dado


def test_fetches_go_to_the_instruction_port():
    instructions, data = RecordingPort(), RecordingPort()
    instructions.words[4] = 0x7005
    request = MemoryRequest(MemOp.READ, 4, AccessKind.INSTRUCTION)

    assert dispatch(request, instruction_port=instructions, data_port=data) == 0x7005
    assert instructions.log == [("read", 4)]
    assert data.log == []


def test_data_accesses_go_to_the_data_port():
    instructions, data = RecordingPort(), RecordingPort()
    write = MemoryRequest(MemOp.WRITE, 9, AccessKind.DATA, data=0x1234)
    read = MemoryRequest(MemOp.READ, 9, AccessKind.DATA)

    dispatch(write, instruction_port=instructions, data_port=data)
    assert dispatch(read, instruction_port=instructions, data_port=data) == 0x1234
    assert data.log == [("write", 9), ("read", 9)]
    assert instructions.log == []


def test_route_and_stall_for():
    class TimedPort(RecordingPort):
        def stall_for(self, op, address) -> int:
            return 2

    instructions, data = TimedPort(), RecordingPort()
    fetch = MemoryRequest(MemOp.READ, 1, AccessKind.INSTRUCTION)
    load = MemoryRequest(MemOp.READ, 1, AccessKind.DATA)
    assert route(fetch, instruction_port=instructions, data_port=data) is instructions
    assert route(load, instruction_port=instructions, data_port=data) is data
    assert stall_for(instructions, fetch) == 2
    assert stall_for(data, load) == 0  # porta sem temporização: só a janela normal


def test_main_memory_and_caches_share_the_port_protocol():
    assert isinstance(MainMemory(), MemoryPort)
    assert isinstance(RecordingPort(), MemoryPort)
