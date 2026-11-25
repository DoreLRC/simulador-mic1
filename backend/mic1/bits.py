"""Aritmética de bits do MIC-1 clássico: palavras de 16 bits em complemento de dois.

Os registradores guardam sempre valores *sem sinal* (0 … 0xFFFF). A leitura com sinal só é
usada quando o hardware precisa dela (flag N) ou para exibição. Endereços têm 12 bits
(4096 palavras de memória principal).
"""

WORD_BITS = 16
WORD_MASK = 0xFFFF
SIGN_BIT = 0x8000
ADDRESS_BITS = 12
ADDRESS_MASK = 0x0FFF


def to_u16(value: int) -> int:
    """Reduz um inteiro a 16 bits sem sinal, descartando o estouro como o hardware faz."""
    return value & WORD_MASK


def to_s16(value: int) -> int:
    """Interpreta os 16 bits menos significativos como inteiro com sinal."""
    value &= WORD_MASK
    return value - (1 << WORD_BITS) if value & SIGN_BIT else value


def to_address(value: int) -> int:
    """Os 12 bits menos significativos, que é o que cabe no MAR."""
    return value & ADDRESS_MASK
