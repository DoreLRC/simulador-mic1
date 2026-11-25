"""Banco de registradores: os 16 registradores dos slides, constantes e largura de 16 bits."""

import pytest

from mic1.datapath import Reg, RegisterFile

SLIDE_LABELS = ["PC", "AC", "SP", "IR", "TIR", "0", "+1", "-1", "AMASK", "SMASK"]
SLIDE_LABELS += ["A", "B", "C", "D", "E", "F"]


def test_sixteen_registers_numbered_like_the_slides():
    assert [register.label for register in Reg] == SLIDE_LABELS
    assert [register.value for register in Reg] == list(range(16))


def test_constants_are_preloaded_and_the_rest_starts_at_zero():
    snapshot = RegisterFile().snapshot()
    constants = {"0": 0x0000, "+1": 0x0001, "-1": 0xFFFF, "AMASK": 0x0FFF, "SMASK": 0x00FF}
    assert {label: snapshot[label] for label in constants} == constants
    assert all(snapshot[label] == 0 for label in SLIDE_LABELS if label not in constants)


@pytest.mark.parametrize("register", [Reg.ZERO, Reg.PLUS_ONE, Reg.MINUS_ONE, Reg.AMASK, Reg.SMASK])
def test_constants_are_read_only(register):
    with pytest.raises(ValueError, match="constante"):
        RegisterFile()[register] = 5


def test_writes_wrap_to_16_bits():
    regs = RegisterFile()
    regs[Reg.AC] = -1
    regs[Reg.TIR] = 0x1_0005
    assert regs[Reg.AC] == 0xFFFF
    assert regs[Reg.TIR] == 0x0005


def test_lookup_by_number_name_or_slide_label():
    regs = RegisterFile(sp=0x100)
    assert regs[Reg.SP] == regs[2] == regs["SP"] == regs["sp"] == 0x100
    assert regs["+1"] == regs["PLUS_ONE"] == 1


def test_unknown_register_name():
    with pytest.raises(ValueError, match="desconhecido"):
        Reg.parse("XYZ")


def test_reset_restores_constants_and_zeroes_the_rest():
    regs = RegisterFile(ac=7, pc=9)
    regs.reset()
    assert regs.snapshot() == RegisterFile().snapshot()
