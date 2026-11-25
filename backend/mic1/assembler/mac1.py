"""Montador da ISA MAC-1, em duas passagens.

Sintaxe::

    ; comentário (ou // comentário)
    laco:   LODD n          ; rótulo + instrução
            JNEG fim
            LOCO &a         ; '&' opcional (notação do livro): endereço de a
            LODD a+2        ; rótulo ± deslocamento
    fim:    JUMP fim
    n:      5               ; dado: uma palavra
    a:      10, 20, 0x1E    ; dado: várias palavras seguidas
    neg:    -1              ; negativos viram complemento de dois (0xFFFF)

* Mnemônicos: as 23 instruções de :mod:`mic1.isa`, em maiúsculas ou minúsculas.
* Números decimais ou hexadecimais (``0x2A``). Dados aceitam de −32768 a 65535 e também
  podem ser rótulos (o endereço do rótulo).
* Rótulos não diferenciam maiúsculas de minúsculas, podem ser usados antes de definidos
  e não podem ter o nome de um mnemônico.
* O programa é montado a partir do endereço 0, na ordem do fonte (código e dados).

1ª passagem: atribui um endereço a cada instrução/dado e registra os rótulos.
2ª passagem: codifica as instruções e resolve os operandos. Todos os erros encontrados
são reportados de uma vez, cada um com o número da linha.
"""

import re
from dataclasses import dataclass

from mic1.bits import ADDRESS_BITS, WORD_MASK
from mic1.isa import BY_MNEMONIC, Operand

__all__ = ["AssemblyError", "Diagnostic", "ListingEntry", "Program", "assemble_mac1"]

MEMORY_WORDS = 1 << ADDRESS_BITS

_LABEL = re.compile(r"\s*([^\W\d]\w*)\s*:(.*)", re.DOTALL)
_IDENTIFIER = re.compile(r"[^\W\d]\w*")
_TERM = re.compile(r"\s*([+-]?)\s*(&\s*[^\W\d]\w*|[^\W\d]\w*|0[xX][0-9A-Fa-f]+|\d+)\s*")


@dataclass(frozen=True, slots=True)
class Diagnostic:
    line: int
    message: str

    def __str__(self) -> str:
        return f"linha {self.line}: {self.message}"


class AssemblyError(ValueError):
    """Um ou mais erros de montagem, ordenados pela linha."""

    def __init__(self, errors: list[Diagnostic]) -> None:
        self.errors = sorted(errors, key=lambda error: error.line)
        super().__init__("\n".join(str(error) for error in self.errors))

    @property
    def line(self) -> int:
        return self.errors[0].line


@dataclass(frozen=True, slots=True)
class ListingEntry:
    address: int
    word: int
    line: int
    kind: str  # "code" | "data"
    text: str


@dataclass(frozen=True, slots=True)
class Program:
    """Resultado da montagem: a imagem da memória e o mapa para o código-fonte."""

    words: tuple[int, ...]
    symbols: dict[str, int]  # rótulo (como escrito) → endereço
    source_map: dict[int, int]  # endereço → linha do fonte
    listing: tuple[ListingEntry, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "words": list(self.words),
            "symbols": dict(self.symbols),
            "source_map": {str(address): line for address, line in self.source_map.items()},
            "listing": [
                {
                    "address": entry.address,
                    "word": entry.word,
                    "line": entry.line,
                    "kind": entry.kind,
                    "text": entry.text,
                }
                for entry in self.listing
            ],
        }


@dataclass(slots=True)
class _Item:
    address: int
    line: int
    kind: str  # "code" | "data"
    text: str  # operando (código) ou valor (dado), como escrito
    mnemonic: str | None = None


class _LineError(Exception):
    """Erro da linha em processamento (vira um :class:`Diagnostic`)."""


def assemble_mac1(source: str) -> Program:
    errors: list[Diagnostic] = []
    symbols: dict[str, tuple[str, int, int]] = {}  # NOME → (como escrito, endereço, linha)
    items: list[_Item] = []
    address = 0
    overflow_reported = False

    # 1ª passagem: dá um endereço para cada instrução e dado e anota onde cada rótulo foi
    # definido. Ainda não dá para codificar, porque um JUMP pode citar um rótulo que só
    # aparece mais para baixo no fonte.
    for number, raw in enumerate(source.splitlines(), start=1):
        text = _strip_comment(raw).strip()
        while match := _LABEL.fullmatch(text):  # uma linha pode ter mais de um rótulo
            name, text = match.group(1), match.group(2).strip()
            try:
                _define(symbols, name, address, number)
            except _LineError as error:
                errors.append(Diagnostic(number, str(error)))
        if not text:
            continue

        parts = text.split(None, 1)
        head, rest = parts[0], (parts[1].strip() if len(parts) > 1 else "")
        if head.upper() in BY_MNEMONIC:
            items.append(_Item(address, number, "code", rest, head.upper()))
            address += 1
        elif _IDENTIFIER.fullmatch(head) and rest and rest[0] not in "+-,":
            errors.append(Diagnostic(number, f"instrução desconhecida: {head!r}"))
            address += 1
        else:
            values = [value.strip() for value in text.split(",")]
            if not all(values):
                errors.append(Diagnostic(number, "valor vazio na lista de dados"))
                continue
            for value in values:
                items.append(_Item(address, number, "data", value))
                address += 1

        if address > MEMORY_WORDS and not overflow_reported:
            errors.append(Diagnostic(number, f"o programa passa de {MEMORY_WORDS} palavras"))
            overflow_reported = True

    # 2ª passagem: com todos os rótulos conhecidos, cada operando vira número (rótulo,
    # &rótulo, rótulo+n, decimal ou hex) e cada linha vira uma palavra de 16 bits.
    addresses = {key: entry[1] for key, entry in symbols.items()}
    words: list[int] = []
    source_map: dict[int, int] = {}
    listing: list[ListingEntry] = []
    for item in items:
        try:
            word = _encode(item, addresses)
        except _LineError as error:
            errors.append(Diagnostic(item.line, str(error)))
            word = 0
        if item.address >= MEMORY_WORDS:
            continue
        words.append(word)
        source_map[item.address] = item.line
        text = f"{item.mnemonic} {item.text}".strip() if item.kind == "code" else item.text
        listing.append(ListingEntry(item.address, word, item.line, item.kind, text))

    if errors:
        raise AssemblyError(errors)
    return Program(
        tuple(words),
        {name: value for name, value, _ in symbols.values()},
        source_map,
        tuple(listing),
    )


# Detalhes


def _strip_comment(line: str) -> str:
    cut = len(line)
    for marker in (";", "//"):
        position = line.find(marker)
        if position != -1:
            cut = min(cut, position)
    return line[:cut]


def _define(symbols: dict[str, tuple[str, int, int]], name: str, address: int, line: int) -> None:
    key = name.upper()
    if key in BY_MNEMONIC:
        raise _LineError(f"o rótulo {name!r} tem o nome de uma instrução")
    if key in symbols:
        raise _LineError(f"rótulo {name!r} já definido na linha {symbols[key][2]}")
    symbols[key] = (name, address, line)


def _encode(item: _Item, addresses: dict[str, int]) -> int:
    if item.kind == "data":
        value = _evaluate(item.text, addresses)
        if not -0x8000 <= value <= 0xFFFF:
            raise _LineError(f"dado fora de 16 bits: {item.text!r} = {value}")
        return value & WORD_MASK

    assert item.mnemonic is not None
    instruction = BY_MNEMONIC[item.mnemonic]
    if instruction.operand is Operand.NONE:
        if item.text:
            raise _LineError(f"{item.mnemonic} não tem operando")
        return instruction.opcode
    if not item.text:
        kind = "um endereço de 12 bits" if instruction.operand is Operand.ADDRESS else "um byte"
        raise _LineError(f"{item.mnemonic} precisa de um operando ({kind})")
    value = _evaluate(item.text, addresses)
    limit = instruction.operand.max_value
    if not 0 <= value <= limit:
        raise _LineError(f"operando de {item.mnemonic} fora de 0–{limit}: {item.text!r} = {value}")
    return instruction.opcode | value


def _evaluate(text: str, addresses: dict[str, int]) -> int:
    """Valor de ``termo (± termo)*``, onde termo é número, rótulo ou &rótulo."""
    position, total, terms = 0, 0, 0
    while position < len(text):
        match = _TERM.match(text, position)
        if match is None or match.end() == position or (terms and not match.group(1)):
            raise _LineError(f"expressão inválida: {text!r}")
        token = match.group(2).replace(" ", "")
        if token[0].isdigit():
            value = int(token, 16) if token[:2].lower() == "0x" else int(token)
        else:
            name = token.lstrip("&")
            try:
                value = addresses[name.upper()]
            except KeyError:
                raise _LineError(f"rótulo não definido: {name!r}") from None
        total += -value if match.group(1) == "-" else value
        terms += 1
        position = match.end()
    if not terms:
        raise _LineError("valor vazio")
    return total
