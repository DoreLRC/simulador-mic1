"""ISA MAC-1: as 23 macroinstruções da tabela "Nossa ISA" (Aula 13)."""

import pytest

from mic1.isa import BY_MNEMONIC, INSTRUCTIONS, Operand, decode, disassemble, encode

SLIDE_ORDER = (
    "LODD STOD ADDD SUBD JPOS JZER JUMP LOCO LODL STOL ADDL SUBL JNEG JNZE CALL "
    "PSHI POPI PUSH POP RETN SWAP INSP DESP"
).split()


def test_mac1_has_23_instructions_in_the_slide_order():
    assert [instruction.mnemonic for instruction in INSTRUCTIONS] == SLIDE_ORDER


def test_opcodes_have_4_or_7_bits():
    for instruction in INSTRUCTIONS[:15]:
        assert instruction.opcode & 0x0FFF == 0  # 4 bits + operando de 12
    for instruction in INSTRUCTIONS[15:]:
        assert instruction.opcode >> 12 == 0xF and instruction.opcode & 0x1FF == 0  # 7 bits


@pytest.mark.parametrize(
    ("mnemonic", "operand", "word"),
    [
        ("LODD", 5, 0x0005),
        ("STOD", 0xFFF, 0x1FFF),
        ("LOCO", 4095, 0x7FFF),
        ("CALL", 0x10, 0xE010),
        ("PSHI", None, 0xF000),
        ("POPI", None, 0xF200),
        ("PUSH", None, 0xF400),
        ("POP", None, 0xF600),
        ("RETN", None, 0xF800),
        ("SWAP", None, 0xFA00),
        ("INSP", 3, 0xFC03),
        ("DESP", 255, 0xFEFF),
    ],
)
def test_encoding_matches_the_slide_table(mnemonic, operand, word):
    assert encode(mnemonic, operand) == word


@pytest.mark.parametrize("instruction", INSTRUCTIONS, ids=lambda instruction: instruction.mnemonic)
def test_decode_is_the_inverse_of_encode(instruction):
    operand = {Operand.ADDRESS: 0xABC, Operand.BYTE: 0x5A, Operand.NONE: None}[instruction.operand]
    assert decode(encode(instruction.mnemonic, operand)) == (instruction, operand)


def test_disassemble():
    assert [disassemble(word) for word in (0x0005, 0xF000, 0xFC03, 0x7FFF)] == [
        "LODD 5",
        "PSHI",
        "INSP 3",
        "LOCO 4095",
    ]


@pytest.mark.parametrize(
    ("mnemonic", "operand"),
    [("LODD", 4096), ("INSP", 256), ("PSHI", 1), ("LODD", None), ("HALT", 0)],
)
def test_invalid_encodings(mnemonic, operand):
    with pytest.raises(ValueError):
        encode(mnemonic, operand)


def test_lookup_by_mnemonic():
    assert BY_MNEMONIC["SUBL"].meaning == "ac := ac - m[sp + x]"
