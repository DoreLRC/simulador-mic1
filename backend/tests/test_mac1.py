"""Montador MAC-1: rótulos, dados, comentários, números, source_map e erros com a linha."""

import json
from pathlib import Path

import pytest

from mic1.assembler.mac1 import AssemblyError, assemble_mac1
from mic1.isa import INSTRUCTIONS, Operand, decode, encode

EXAMPLES = Path(__file__).resolve().parents[2] / "programs"


def test_assembles_the_se_i_example():
    source = (EXAMPLES / "se_i_menor_igual_n.mac1").read_text(encoding="utf-8")
    program = assemble_mac1(source)
    assert program.symbols == {"saida": 8, "n": 9, "i": 10, "m": 11, "a": 12}
    assert list(program.words[:9]) == [
        encode("LODD", 9),
        encode("SUBD", 10),
        encode("JNEG", 8),
        encode("LOCO", 12),
        encode("ADDD", 10),
        encode("PSHI"),
        encode("POP"),
        encode("STOD", 11),
        encode("JUMP", 8),
    ]
    assert list(program.words[9:]) == [5, 2, 0, 10, 20, 30, 40, 50]


def test_source_map_and_listing():
    program = assemble_mac1("; cabeçalho\n\nini: LOCO 1\n     JUMP ini\nx:   7, 8\n")
    assert program.source_map == {0: 3, 1: 4, 2: 5, 3: 5}
    assert [(entry.address, entry.kind, entry.text) for entry in program.listing] == [
        (0, "code", "LOCO 1"),
        (1, "code", "JUMP ini"),
        (2, "data", "7"),
        (3, "data", "8"),
    ]


@pytest.mark.parametrize("instruction", INSTRUCTIONS, ids=lambda instruction: instruction.mnemonic)
def test_every_instruction_of_the_isa(instruction):
    operand = {Operand.ADDRESS: 123, Operand.BYTE: 45, Operand.NONE: None}[instruction.operand]
    text = instruction.mnemonic if operand is None else f"{instruction.mnemonic.lower()} {operand}"
    (word,) = assemble_mac1(text).words
    assert decode(word) == (instruction, operand)


def test_forward_references_and_labels_on_their_own_line():
    program = assemble_mac1("      JUMP fim\nmeio:\nfim:  JUMP meio\n")
    assert program.symbols == {"meio": 1, "fim": 1}
    assert program.words == (encode("JUMP", 1), encode("JUMP", 1))


def test_data_in_decimal_hex_negative_and_label_values():
    program = assemble_mac1("v: 10, 0x1F, -1, 65535, -32768\nptr: v\n")
    assert program.words == (10, 0x1F, 0xFFFF, 0xFFFF, 0x8000, 0)


def test_operands_with_ampersand_offsets_and_hex():
    program = assemble_mac1("LODD &a\nLODD a+2\nLODD a - 1\nLOCO 0xFFF\na: 1, 2, 3\n")
    assert program.words[:4] == (
        encode("LODD", 4),
        encode("LODD", 6),
        encode("LODD", 3),
        encode("LOCO", 0xFFF),
    )


def test_comments_and_case_insensitivity():
    source = "Laco: lodd X ; comentário\n  // linha só de comentário\n  jump LACO // volta\nx: 5\n"
    program = assemble_mac1(source)
    assert program.words == (encode("LODD", 2), encode("JUMP", 0), 5)
    assert program.symbols == {"Laco": 0, "x": 2}


def test_labels_with_accents():
    assert assemble_mac1("saída: JUMP saída\n").words == (encode("JUMP", 0),)


@pytest.mark.parametrize(
    ("source", "line", "message"),
    [
        ("LODD 1\nLODX 5\n", 2, "instrução desconhecida"),
        ("JUMP nada\n", 1, "rótulo não definido"),
        ("a: 1\na: 2\n", 2, "já definido na linha 1"),
        ("LODD\n", 1, "precisa de um operando"),
        ("PUSH 3\n", 1, "não tem operando"),
        ("LODD 4096\n", 1, "fora de 0–4095"),
        ("LODD -1\n", 1, "fora de 0–4095"),
        ("INSP 256\n", 1, "fora de 0–255"),
        ("x: 70000\n", 1, "fora de 16 bits"),
        ("pop: 1\n", 1, "nome de uma instrução"),
        ("LODD 12abc\n", 1, "expressão inválida"),
        ("x: 1,,2\n", 1, "valor vazio"),
    ],
)
def test_errors_report_the_line(source, line, message):
    with pytest.raises(AssemblyError) as info:
        assemble_mac1(source)
    assert info.value.line == line
    assert message in str(info.value)


def test_all_errors_are_reported_at_once():
    with pytest.raises(AssemblyError) as info:
        assemble_mac1("JUMP a\nLODX 1\nLODD b\n")
    assert [error.line for error in info.value.errors] == [1, 2, 3]


def test_program_larger_than_the_memory():
    with pytest.raises(AssemblyError, match="4096"):
        assemble_mac1("x: " + ", ".join(["0"] * 4097))


def test_program_converts_to_json():
    program = assemble_mac1("LOCO 1\nx: 2\n")
    data = json.loads(json.dumps(program.to_dict()))
    assert data["source_map"] == {"0": 1, "1": 2}
    assert data["listing"][0] == {
        "address": 0,
        "word": encode("LOCO", 1),
        "line": 1,
        "kind": "code",
        "text": "LOCO 1",
    }
