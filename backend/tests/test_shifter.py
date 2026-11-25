"""Deslocador: sem deslocamento, rshift e lshift de 1 bit (preenchendo com zero)."""

import pytest

from mic1.datapath import Shift, shifter


def test_field_codes_match_the_slides():
    """Aula 13: a microinstrução 3 (``tir := lshift(ir + ir)``) usa DESL = 10."""
    assert (Shift.NONE, Shift.RIGHT, Shift.LEFT) == (0, 1, 2)
    assert [shift.symbol for shift in Shift] == ["—", "rshift", "lshift"]


@pytest.mark.parametrize(
    ("value", "shift", "expected"),
    [
        (0x4001, Shift.LEFT, 0x8002),
        (0x8000, Shift.LEFT, 0x0000),  # o bit 15 sai
        (0x8000, Shift.RIGHT, 0x4000),  # entra zero (não replica o sinal)
        (0x0001, Shift.RIGHT, 0x0000),
        (0xBEEF, Shift.NONE, 0xBEEF),
    ],
)
def test_shifts(value, shift, expected):
    assert shifter(value, shift) == expected


def test_code_3_is_not_used():
    with pytest.raises(ValueError, match="11"):
        shifter(1, 3)
