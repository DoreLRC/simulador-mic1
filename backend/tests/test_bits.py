"""Utilitários de 16 bits: truncamento, leitura com sinal e endereços de 12 bits."""

import pytest

from mic1.bits import to_address, to_s16, to_u16


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-1, 0xFFFF), (1 << 16, 0), ((1 << 16) + 5, 5), (0x1234, 0x1234)],
)
def test_to_u16_discards_overflow(value, expected):
    assert to_u16(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0xFFFF, -1), (0x7FFF, 32767), (0x8000, -32768), (0, 0)],
)
def test_to_s16_reads_twos_complement(value, expected):
    assert to_s16(value) == expected


@pytest.mark.parametrize(("value", "expected"), [(0x8005, 0x005), (0xFFFF, 0xFFF), (0x0FFF, 0xFFF)])
def test_to_address_keeps_12_bits(value, expected):
    assert to_address(value) == expected
