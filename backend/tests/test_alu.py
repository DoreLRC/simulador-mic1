"""ULA de 2 bits do MIC-1 clássico: as 4 operações, flags N/Z e 16 bits sem trap de overflow."""

import pytest

from mic1.bits import to_s16
from mic1.datapath import ALUOp, alu


def test_field_codes_match_the_slides():
    """Aula 10: F = 00 → A + B, 01 → A AND B, 10 → A, 11 → INV(A)."""
    assert [(op.value, op.symbol) for op in ALUOp] == [
        (0, "A + B"),
        (1, "A AND B"),
        (2, "A"),
        (3, "NOT A"),
    ]


@pytest.mark.parametrize(
    ("op", "a", "b", "expected"),
    [
        (ALUOp.ADD, 3, 4, 7),
        (ALUOp.AND, 0b1100, 0b1010, 0b1000),
        (ALUOp.PASS_A, 0x1234, 0xFFFF, 0x1234),  # B é ignorado
        (ALUOp.NOT_A, 0x00FF, 0x1234, 0xFF00),
    ],
)
def test_operations(op, a, b, expected):
    assert alu(a, b, op).value == expected


def test_accepts_the_raw_field_value():
    assert alu(2, 3, 0).value == 5


def test_z_when_result_is_zero():
    result = alu(5, 0xFFFB, ALUOp.ADD)  # 5 + (-5)
    assert result.value == 0
    assert result.z and not result.n


def test_n_is_bit_15():
    result = alu(0x8000, 0, ALUOp.PASS_A)
    assert result.n and not result.z


def test_signed_overflow_wraps_without_trap():
    result = alu(0x7FFF, 1, ALUOp.ADD)  # 32767 + 1
    assert result.value == 0x8000  # vira -32768: o estouro não é tratado
    assert result.n


def test_carry_out_is_discarded():
    result = alu(0xFFFF, 1, ALUOp.ADD)
    assert result.value == 0
    assert result.z


def test_subtraction_as_done_by_the_microprogram():
    """Microinstruções 16–18 (SUBD): x - y = x + 1 + inv(y)."""
    x, y = 5, 7
    x_plus_1 = alu(x, 1, ALUOp.ADD).value
    inv_y = alu(y, 0, ALUOp.NOT_A).value
    assert to_s16(alu(x_plus_1, inv_y, ALUOp.ADD).value) == -2
