"""Unidade de controle: formato da MIR (Aula 13), MMUX/COND e o desvio múltiplo do modo case."""

import pytest

from mic1.assembler.mal import load_microprogram
from mic1.control import (
    FIELDS,
    Cond,
    ControlStore,
    ControlUnit,
    DecodeMode,
    MicroInstruction,
    MicroStep,
    case_target,
    next_address,
)
from mic1.datapath import ALUOp, Reg, Shift


def test_microinstruction_has_the_13_fields_of_the_slide():
    assert FIELDS == (
        ("amux", 1),
        ("cond", 2),
        ("alu", 2),
        ("sh", 2),
        ("mbr", 1),
        ("mar", 1),
        ("rd", 1),
        ("wr", 1),
        ("enc", 1),
        ("c", 4),
        ("b", 4),
        ("a", 4),
        ("addr", 8),
    )
    assert sum(width for _, width in FIELDS) == 32


# --- Codificações binárias mostradas nos slides da Aula 13 ------------------------------


def test_microinstruction_0_matches_the_slide_bit_by_bit():
    """Slide "Busca na memória de MIC-1"."""
    step = load_microprogram(DecodeMode.ORIGINAL)[0]
    assert step.source == "mar := pc; rd;"
    assert step.instruction.bit_groups() == "0 00 00 00 0 1 1 0 0 0000 0000 0000 00000001"
    assert step.instruction.encode() == 0x00C0_0001


def test_microinstruction_3_matches_the_slide_bit_by_bit():
    """Slide "Identificando a macroinstrução"."""
    step = load_microprogram(DecodeMode.ORIGINAL)[3]
    assert step.source == "tir := lshift(ir + ir); if n then goto 19;"
    assert step.instruction.bit_groups() == "0 01 00 10 0 0 0 0 1 0100 0011 0011 00010011"
    assert step.instruction.encode() == 0x2414_3313


def test_decoding_the_slide_word_gives_back_every_field():
    mi = MicroInstruction.decode(0x2414_3313)
    assert (mi.cond, mi.alu, mi.sh) == (Cond.IF_N, ALUOp.ADD, Shift.LEFT)
    assert (mi.enc, mi.c, mi.b, mi.a, mi.addr) == (1, Reg.TIR, Reg.IR, Reg.IR, 19)


@pytest.mark.parametrize("mode", list(DecodeMode))
def test_decode_is_the_inverse_of_encode(mode):
    for step in load_microprogram(mode):
        assert MicroInstruction.decode(step.instruction.encode()) == step.instruction


@pytest.mark.parametrize(
    "fields",
    [{"c": 16}, {"addr": 256}, {"cond": 4}, {"sh": 3}, {"rd": 1, "wr": 1}],
)
def test_fields_are_validated(fields):
    with pytest.raises(ValueError):
        MicroInstruction(**fields)


# --- MMUX ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cond", "n", "z", "expected"),
    [
        (Cond.NONE, True, True, 11),
        (Cond.IF_N, True, False, 99),
        (Cond.IF_N, False, True, 11),
        (Cond.IF_Z, False, True, 99),
        (Cond.IF_Z, True, False, 11),
        (Cond.ALWAYS, False, False, 99),
    ],
)
def test_mmux_chooses_addr_or_mpc_plus_one(cond, n, z, expected):
    assert next_address(10, MicroInstruction(cond=cond, addr=99), n, z) == expected


def test_mpc_plus_one_wraps_around():
    assert next_address(255, MicroInstruction(), False, False) == 0


# --- Modo case (Aula 15) ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ir", "target"),
    [(0x0005, 0), (0x1005, 4), (0x3000, 12), (0xC123, 48), (0xF600, 60)],
)
def test_case_target_is_opcode_times_four(ir, target):
    assert case_target(ir) == target


def _unit(mode: DecodeMode) -> ControlUnit:
    store = ControlStore([MicroStep(254, MicroInstruction()), MicroStep(12, MicroInstruction())])
    unit = ControlUnit(store, mode)
    unit.mpc = 254
    unit.load_mir()
    return unit


def test_case_mode_dispatches_when_the_next_address_would_be_255():
    unit = _unit(DecodeMode.CASE)
    assert unit.sequence(False, False, ir=0x3005) == 12
    assert unit.dispatched


def test_original_mode_has_no_dispatch_hardware():
    unit = _unit(DecodeMode.ORIGINAL)
    assert unit.sequence(False, False, ir=0x3005) == 255
    assert not unit.dispatched


def test_fetch_address_of_each_mode():
    assert DecodeMode.ORIGINAL.fetch_address == 0
    assert DecodeMode.CASE.fetch_address == 252


# --- Memória de controle -------------------------------------------------------------------


def test_control_store_usage_matches_the_slide():
    """Aula 14: 79 microinstruções × 32 bits = 2528 bits, numa memória de 256 × 32."""
    original = load_microprogram(DecodeMode.ORIGINAL)
    assert (len(original), original.used_bits) == (79, 2528)
    assert len(load_microprogram(DecodeMode.CASE)) == 67


def test_empty_slot_is_an_error():
    with pytest.raises(LookupError, match="endereço 5"):
        ControlStore()[5]


def test_address_cannot_be_filled_twice():
    with pytest.raises(ValueError):
        ControlStore([MicroStep(1, MicroInstruction()), MicroStep(1, MicroInstruction())])
