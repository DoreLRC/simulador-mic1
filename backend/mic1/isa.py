"""ISA MAC-1: as 23 macroinstruções de 16 bits.

Formatos:

* opcode de 4 bits + operando ``x`` de 12 bits: LODD até CALL (direto, imediato, local);
* opcode de 7 bits, sem operando: PSHI, POPI, PUSH, POP, RETN, SWAP;
* opcode de 7 bits + constante ``y`` de 8 bits: INSP, DESP.

O montador usa ``encode`` e a interface usa ``disassemble`` para mostrar o que está no IR.
"""

from dataclasses import dataclass
from enum import StrEnum

from mic1.bits import ADDRESS_MASK, to_u16

__all__ = [
    "BY_MNEMONIC",
    "INSTRUCTIONS",
    "Instruction",
    "Operand",
    "decode",
    "disassemble",
    "encode",
]


class Operand(StrEnum):
    ADDRESS = "x"  # 12 bits: endereço, constante (LOCO) ou deslocamento (LODL etc.)
    NONE = ""
    BYTE = "y"  # 8 bits (INSP, DESP)

    @property
    def max_value(self) -> int:
        return {Operand.ADDRESS: ADDRESS_MASK, Operand.BYTE: 0xFF, Operand.NONE: 0}[self]


@dataclass(frozen=True, slots=True)
class Instruction:
    mnemonic: str
    opcode: int  # bits do opcode já na posição final (ex.: 0x2000, 0xF400)
    operand: Operand
    meaning: str


INSTRUCTIONS: tuple[Instruction, ...] = (
    Instruction("LODD", 0x0000, Operand.ADDRESS, "ac := m[x]"),
    Instruction("STOD", 0x1000, Operand.ADDRESS, "m[x] := ac"),
    Instruction("ADDD", 0x2000, Operand.ADDRESS, "ac := ac + m[x]"),
    Instruction("SUBD", 0x3000, Operand.ADDRESS, "ac := ac - m[x]"),
    Instruction("JPOS", 0x4000, Operand.ADDRESS, "if ac >= 0 then pc := x"),
    Instruction("JZER", 0x5000, Operand.ADDRESS, "if ac = 0 then pc := x"),
    Instruction("JUMP", 0x6000, Operand.ADDRESS, "pc := x"),
    Instruction("LOCO", 0x7000, Operand.ADDRESS, "ac := x (0 <= x <= 4095)"),
    Instruction("LODL", 0x8000, Operand.ADDRESS, "ac := m[sp + x]"),
    Instruction("STOL", 0x9000, Operand.ADDRESS, "m[x + sp] := ac"),
    Instruction("ADDL", 0xA000, Operand.ADDRESS, "ac := ac + m[sp + x]"),
    Instruction("SUBL", 0xB000, Operand.ADDRESS, "ac := ac - m[sp + x]"),
    Instruction("JNEG", 0xC000, Operand.ADDRESS, "if ac < 0 then pc := x"),
    Instruction("JNZE", 0xD000, Operand.ADDRESS, "if ac != 0 then pc := x"),
    Instruction("CALL", 0xE000, Operand.ADDRESS, "sp := sp - 1; m[sp] := pc; pc := x"),
    Instruction("PSHI", 0xF000, Operand.NONE, "sp := sp - 1; m[sp] := m[ac]"),
    Instruction("POPI", 0xF200, Operand.NONE, "m[ac] := m[sp]; sp := sp + 1"),
    Instruction("PUSH", 0xF400, Operand.NONE, "sp := sp - 1; m[sp] := ac"),
    Instruction("POP", 0xF600, Operand.NONE, "ac := m[sp]; sp := sp + 1"),
    Instruction("RETN", 0xF800, Operand.NONE, "pc := m[sp]; sp := sp + 1"),
    Instruction("SWAP", 0xFA00, Operand.NONE, "tmp := ac; ac := sp; sp := tmp"),
    Instruction("INSP", 0xFC00, Operand.BYTE, "sp := sp + y (0 <= y <= 255)"),
    Instruction("DESP", 0xFE00, Operand.BYTE, "sp := sp - y (0 <= y <= 255)"),
)

BY_MNEMONIC: dict[str, Instruction] = {
    instruction.mnemonic: instruction for instruction in INSTRUCTIONS
}


def encode(mnemonic: str, operand: int | None = None) -> int:
    """Palavra de 16 bits da instrução, ex.: ``encode("LODD", 5) == 0x0005``."""
    try:
        instruction = BY_MNEMONIC[mnemonic.upper()]
    except KeyError:
        raise ValueError(f"instrução MAC-1 desconhecida: {mnemonic!r}") from None
    if instruction.operand is Operand.NONE:
        if operand not in (None, 0):
            raise ValueError(f"{instruction.mnemonic} não tem operando")
        return instruction.opcode
    if operand is None:
        raise ValueError(f"{instruction.mnemonic} precisa de um operando")
    limit = instruction.operand.max_value
    if not 0 <= operand <= limit:
        raise ValueError(f"operando de {instruction.mnemonic} fora de 0–{limit}: {operand}")
    return instruction.opcode | operand


def decode(word: int) -> tuple[Instruction, int | None]:
    """Instrução e operando de uma palavra, do mesmo jeito que o microprograma interpreta."""
    word = to_u16(word)
    # Os 4 bits de cima escolhem a instrução, menos o 1111: nesse grupo os 3 bits
    # seguintes (bits 11 a 9) dizem qual das 8 instruções sem endereço é a da vez.
    if word >> 12 != 0xF:
        return INSTRUCTIONS[word >> 12], word & ADDRESS_MASK
    instruction = INSTRUCTIONS[15 + ((word >> 9) & 0b111)]
    if instruction.operand is Operand.BYTE:
        return instruction, word & 0xFF
    return instruction, None


def disassemble(word: int) -> str:
    """Texto da instrução, ex.: ``"LODD 5"``, ``"PSHI"``, ``"INSP 3"``."""
    instruction, operand = decode(word)
    return instruction.mnemonic if operand is None else f"{instruction.mnemonic} {operand}"
