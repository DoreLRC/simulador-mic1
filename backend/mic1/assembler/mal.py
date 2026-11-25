"""Montador do microprograma: lê MAL (a notação do Tanenbaum, 3ª ed.) e gera a memória de controle.

Cada linha tem um endereço explícito e micro-operações separadas por ``;``::

    0: mar := pc; rd;                              {busca}
    3: tir := lshift(ir + ir); if n then goto 19;  {identifica}

Micro-operações:

==================  ===============================================================
``reg := expr``     grava pelo barramento C (ENC = 1, C = reg)
``mbr := expr``     carrega o MBR com a saída do deslocador
``alu := expr``     só calcula: atualiza N e Z sem gravar nada
``mar := reg``      carrega o MAR com o registrador colocado no barramento B
``rd`` / ``wr``     sinais de leitura e escrita na memória principal
``goto n``          desvio incondicional (COND = 11)
``if n then goto``  desvio se N (COND = 01); ``if z then goto`` desvia se Z (COND = 10)
==================  ===============================================================

Expressões: ``lshift(…)`` ou ``rshift(…)`` envolvendo ``x + y``, ``band(x, y)``,
``inv(x)`` ou ``x``. Os operandos são registradores (pc, ac, sp, ir, tir, a–f, amask,
smask), as constantes ``0``, ``1`` e ``(-1)``, ou ``mbr``, que só entra pela AMUX, no
lado A da ULA. Comentários: ``{...}`` (podem ocupar várias linhas) ou ``#`` até o fim da linha.

Sem desvio, o campo ADDR recebe o endereço seguinte, como na codificação do livro para a
microinstrução 0; o hardware ignora esse campo porque COND = 00.
"""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from mic1.control import (
    CONTROL_STORE_SIZE,
    Cond,
    ControlStore,
    DecodeMode,
    MicroInstruction,
    MicroStep,
)
from mic1.datapath import ALUOp, Reg, Shift

__all__ = ["MICROCODE_DIR", "MalError", "assemble_mal", "load_microprogram"]

MICROCODE_DIR = Path(__file__).resolve().parents[2] / "microcode"

OPERANDS: dict[str, Reg] = {
    "pc": Reg.PC,
    "ac": Reg.AC,
    "sp": Reg.SP,
    "ir": Reg.IR,
    "tir": Reg.TIR,
    "0": Reg.ZERO,
    "1": Reg.PLUS_ONE,
    "+1": Reg.PLUS_ONE,
    "-1": Reg.MINUS_ONE,
    "(-1)": Reg.MINUS_ONE,
    "amask": Reg.AMASK,
    "smask": Reg.SMASK,
    "a": Reg.A,
    "b": Reg.B,
    "c": Reg.C,
    "d": Reg.D,
    "e": Reg.E,
    "f": Reg.F,
}
MBR = "mbr"

Operand = Reg | str  # um registrador ou "mbr"


class MalError(ValueError):
    """Erro de montagem, com o número da linha do fonte MAL."""

    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"linha {line}: {message}")
        self.line = line
        self.message = message


@dataclass(frozen=True, slots=True)
class _Expression:
    op: ALUOp
    operands: tuple[Operand, ...]
    shift: Shift


_LINE = re.compile(r"^\s*(\d+)\s*:(.*)$")
_JUMP = re.compile(r"(?:if ([nz]) then )?goto (\d+)")
_SHIFT_CALL = re.compile(r"(lshift|rshift)\((.+)\)")
_BAND = re.compile(r"band\(([^,()]+),([^,()]+)\)")
_INV = re.compile(r"inv\((.+)\)")
_ADD = re.compile(r"(.+?)\+(.+)")


def assemble_mal(source: str) -> list[MicroStep]:
    """Monta o fonte MAL e devolve as microinstruções em ordem de endereço."""
    steps: dict[int, MicroStep] = {}
    defined_on: dict[int, int] = {}
    jumps: list[tuple[int, int]] = []  # (linha, destino)

    for number, raw in enumerate(_join_brace_comments(source).splitlines(), start=1):
        comments = [text.strip() for text in re.findall(r"\{([^}]*)\}", raw)]
        code = re.sub(r"\{[^}]*\}", " ", raw)
        if "#" in code:
            code, trailing = code.split("#", 1)
            comments.append(trailing.strip())
        if not code.strip():
            continue

        match = _LINE.match(code)
        if not match:
            raise MalError(number, "esperado '<endereço>: <micro-operações>'")
        address, body = int(match.group(1)), " ".join(match.group(2).split())
        if not 0 <= address < CONTROL_STORE_SIZE:
            raise MalError(number, f"endereço {address} fora da memória de controle (0–255)")
        if address in defined_on:
            raise MalError(number, f"endereço {address} já definido na linha {defined_on[address]}")
        if not body:
            continue  # posição reservada, sem microinstrução
        defined_on[address] = number

        instruction = _assemble_line(body, address, number)
        if instruction.cond is not Cond.NONE:
            jumps.append((number, instruction.addr))
        steps[address] = MicroStep(
            address, instruction, body, " · ".join(text for text in comments if text)
        )

    for number, target in jumps:
        if target not in steps:
            raise MalError(number, f"desvio para o endereço {target}, que está vazio")
    return [steps[address] for address in sorted(steps)]


@cache
def load_microprogram(mode: DecodeMode = DecodeMode.ORIGINAL) -> ControlStore:
    """Memória de controle carregada com o microprograma do modo (arquivos em ``microcode/``)."""
    mode = DecodeMode(mode)
    source = (MICROCODE_DIR / mode.microcode_file).read_text(encoding="utf-8")
    return ControlStore(assemble_mal(source))


# Detalhes da montagem


def _join_brace_comments(source: str) -> str:
    """Põe cada comentário ``{…}`` numa linha só, preservando a numeração das linhas."""
    if source.count("{") != source.count("}"):
        raise MalError(1, "comentário '{' sem o '}' correspondente")

    def flatten(match: re.Match[str]) -> str:
        text = match.group(0)
        return text.replace("\n", " ") + "\n" * text.count("\n")

    return re.sub(r"\{[^}]*\}", flatten, source)


def _assemble_line(body: str, address: int, line: int) -> MicroInstruction:
    fields: dict[str, int] = {"addr": (address + 1) % CONTROL_STORE_SIZE}
    expression: _Expression | None = None
    mar_source: Reg | None = None
    has_jump = False

    for statement in body.split(";"):
        text = " ".join(statement.lower().split())
        if not text:
            continue
        if text in ("rd", "wr"):
            if fields.get(text):
                raise MalError(line, f"'{text}' repetido")
            fields[text] = 1
        elif match := _JUMP.fullmatch(text):
            if has_jump:
                raise MalError(line, "mais de um desvio na mesma microinstrução")
            has_jump = True
            flag, target = match.group(1), int(match.group(2))
            if not 0 <= target < CONTROL_STORE_SIZE:
                raise MalError(line, f"destino {target} fora da memória de controle (0–255)")
            fields["cond"] = {None: Cond.ALWAYS, "n": Cond.IF_N, "z": Cond.IF_Z}[flag]
            fields["addr"] = target
        elif ":=" in text:
            destination, _, right = text.partition(":=")
            destination, right = destination.strip(), right.replace(" ", "")
            if destination == "mar":
                if mar_source is not None:
                    raise MalError(line, "'mar :=' repetido")
                mar_source = _register(right, line, "o MAR recebe o barramento B")
                fields["mar"] = 1
                continue
            parsed = _parse_expression(right, line)
            if expression is not None and parsed != expression:
                raise MalError(
                    line, "atribuições com expressões diferentes (há uma só ULA por ciclo)"
                )
            expression = parsed
            if destination == "mbr":
                fields["mbr"] = 1
            elif destination != "alu":
                target_register = _register(destination, line, "destino do barramento C")
                if target_register.is_constant:
                    raise MalError(line, f"{target_register.label} é constante (somente leitura)")
                if fields.get("enc"):
                    raise MalError(line, "o barramento C grava um só registrador por ciclo")
                fields["enc"], fields["c"] = 1, target_register
        else:
            raise MalError(line, f"micro-operação desconhecida: {statement.strip()!r}")

    if fields.get("rd") and fields.get("wr"):
        raise MalError(line, "rd e wr na mesma microinstrução")

    fields["b"] = mar_source if mar_source is not None else 0
    if expression is not None:
        fields["alu"], fields["sh"] = expression.op, expression.shift
        a_side, b_side = _place_operands(expression, mar_source, line)
        if a_side == MBR:
            fields["amux"] = 1
        else:
            fields["a"] = a_side  # type: ignore[assignment]
        if b_side is not None:
            fields["b"] = b_side
    return MicroInstruction(**fields)  # type: ignore[arg-type]


def _parse_expression(text: str, line: int) -> _Expression:
    shift = Shift.NONE
    if match := _SHIFT_CALL.fullmatch(text):
        shift = Shift.LEFT if match.group(1) == "lshift" else Shift.RIGHT
        text = match.group(2)

    if match := _BAND.fullmatch(text):
        op, names = ALUOp.AND, (match.group(1), match.group(2))
    elif match := _INV.fullmatch(text):
        op, names = ALUOp.NOT_A, (match.group(1),)
    elif match := _ADD.fullmatch(text):
        op, names = ALUOp.ADD, (match.group(1), match.group(2))
    else:
        op, names = ALUOp.PASS_A, (text,)
    return _Expression(op, tuple(_operand(name, line) for name in names), shift)


def _operand(name: str, line: int) -> Operand:
    if name == MBR:
        return MBR
    try:
        return OPERANDS[name]
    except KeyError:
        raise MalError(line, f"operando desconhecido: {name!r}") from None


def _register(name: str, line: int, role: str) -> Reg:
    if name == MBR:
        raise MalError(line, f"o MBR não pode ser usado aqui ({role})")
    operand = _operand(name, line)
    assert isinstance(operand, Reg)
    return operand


def _place_operands(
    expression: _Expression, b_fixed: Reg | None, line: int
) -> tuple[Operand, Reg | None]:
    """Escolhe qual operando vai pelo lado A (Latch A ou MBR via AMUX) e qual pelo B."""
    if len(expression.operands) == 1:
        return expression.operands[0], b_fixed
    first, second = expression.operands
    for a_side, b_side in ((first, second), (second, first)):
        if b_side == MBR or (b_fixed is not None and b_side != b_fixed):
            continue
        assert isinstance(b_side, Reg)
        return a_side, b_side
    raise MalError(
        line,
        "operandos incompatíveis: o MBR só entra pelo lado A (AMUX) e o barramento B "
        "já está ocupado por 'mar :='",
    )
