"""Micro-montador MAL: o dialeto dos slides, a escolha dos lados A/B e as mensagens de erro."""

import pytest

from mic1.assembler.mal import MalError, assemble_mal, load_microprogram
from mic1.control import Cond, DecodeMode
from mic1.datapath import ALUOp, Reg

ORIGINAL = load_microprogram(DecodeMode.ORIGINAL)
CASE = load_microprogram(DecodeMode.CASE)


def test_original_microprogram_fills_addresses_0_to_78():
    assert [step.address for step in ORIGINAL] == list(range(79))


def test_case_microprogram_layout():
    addresses = {step.address for step in CASE}
    assert set(range(0, 64, 4)) <= addresses  # uma rotina por opcode, em opcode × 4
    assert {252, 253, 254} <= addresses  # busca
    assert 255 not in addresses  # endereço de despacho do case


def test_comments_are_kept_for_display():
    assert ORIGINAL[6].comment == "0000 = LODD"
    assert CASE[254].comment.startswith("busca")


# --- Escolha de AMUX / barramento A / barramento B ----------------------------------------


def test_mar_on_b_and_the_alu_operand_on_a():
    """9: ``mar := ir; mbr := ac; wr;``."""
    mi = ORIGINAL[9].instruction
    assert (mi.mar, mi.b, mi.a, mi.alu, mi.mbr, mi.wr, mi.enc) == (
        1,
        Reg.IR,
        Reg.AC,
        ALUOp.PASS_A,
        1,
        1,
        0,
    )


def test_operands_are_swapped_to_keep_sp_on_the_b_bus():
    """56: ``mar := sp; sp := sp + 1; rd;`` — o +1 vai pelo lado A."""
    mi = ORIGINAL[56].instruction
    assert (mi.mar, mi.b, mi.a, mi.alu, mi.enc, mi.c, mi.rd) == (
        1,
        Reg.SP,
        Reg.PLUS_ONE,
        ALUOp.ADD,
        1,
        Reg.SP,
        1,
    )


def test_mbr_goes_through_the_amux():
    """14: ``ac := mbr + ac; goto 0;``."""
    mi = ORIGINAL[14].instruction
    assert (mi.amux, mi.b, mi.alu, mi.enc, mi.c, mi.cond, mi.addr) == (
        1,
        Reg.AC,
        ALUOp.ADD,
        1,
        Reg.AC,
        Cond.ALWAYS,
        0,
    )


def test_band_and_inv():
    band = ORIGINAL[22].instruction  # pc := band(ir, amask); goto 0;
    inv = ORIGINAL[17].instruction  # a := inv(mbr);
    assert (band.a, band.b, band.alu, band.c) == (Reg.IR, Reg.AMASK, ALUOp.AND, Reg.PC)
    assert (inv.amux, inv.alu, inv.c) == (1, ALUOp.NOT_A, Reg.A)


def test_alu_assignment_only_sets_the_flags():
    """5: ``alu := tir; if n then goto 9;``."""
    mi = ORIGINAL[5].instruction
    assert (mi.enc, mi.alu, mi.a, mi.cond, mi.addr) == (0, ALUOp.PASS_A, Reg.TIR, Cond.IF_N, 9)


def test_constant_minus_one():
    """47: ``sp := sp + (-1);``."""
    mi = ORIGINAL[47].instruction
    assert (mi.a, mi.b, mi.alu) == (Reg.SP, Reg.MINUS_ONE, ALUOp.ADD)


def test_without_a_jump_addr_points_to_the_next_address():
    steps = assemble_mal("7: rd;\n8: goto 7;")
    assert (steps[0].instruction.cond, steps[0].instruction.addr) == (Cond.NONE, 8)


def test_brace_and_hash_comments():
    source = "{ cabeçalho\n  em duas linhas }\n0: rd;  # lê\n1: goto 0; {volta}\n"
    steps = assemble_mal(source)
    assert [(step.address, step.comment) for step in steps] == [(0, "lê"), (1, "volta")]


# --- Erros -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("0: 0 := ac;", "constante"),
        ("0: rd; wr;", "rd e wr"),
        ("0: mar := sp; ac := mbr + ir;", "incompatíveis"),
        ("0: goto 0; goto 0;", "mais de um desvio"),
        ("0: ac := ac + 1; tir := ac;", "expressões diferentes"),
        ("0: ac := 1; tir := 1;", "um só registrador"),
        ("256: rd;", "fora da memória"),
        ("0: rd;\n0: wr;", "já definido"),
        ("0: pula;", "desconhecida"),
        ("0: goto 7;", "vazio"),
        ("0: ac := xyz;", "operando desconhecido"),
        ("0: mar := mbr;", "MBR não pode"),
        ("0: rd; {sem fim", "sem o"),
        ("rd;", "esperado"),
    ],
)
def test_errors(source, message):
    with pytest.raises(MalError, match=message):
        assemble_mal(source)


def test_error_reports_the_source_line():
    with pytest.raises(MalError) as info:
        assemble_mal("0: rd;\n\n2: xyz;")
    assert info.value.line == 3
