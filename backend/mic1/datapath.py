"""Via de dados do MIC-1 (Tanenbaum, 3ª ed., cap. 4).

Componentes:

* **16 registradores de 16 bits**: PC, AC, SP, IR, TIR, 0, +1, −1, AMASK, SMASK e A a F,
  numerados de 0 a 15 do mesmo jeito que nos campos A, B e C da microinstrução. Os
  registradores 5 a 9 são constantes e só podem ser lidos.
* **Barramentos A e B**: cada um recebe o registrador escolhido por um decodificador 4→16.
* **Latches A e B**: guardam o valor dos barramentos. Sem eles, em ``pc := pc + 1`` o PC
  mudaria enquanto a própria ULA ainda estivesse lendo o PC.
* **AMUX**: escolhe a entrada esquerda da ULA, Latch A (0) ou MBR (1).
* **ULA**: A + B, A AND B, A ou NOT A, com as saídas N (negativo) e Z (zero). A soma é
  de 16 bits e o estouro simplesmente se perde.
* **Deslocador**: não desloca, desloca 1 bit à direita ou 1 bit à esquerda.
* **MAR** (12 bits) recebe o Latch B; **MBR** (16 bits) recebe a saída do deslocador ou a
  palavra que veio da memória.
* **Barramento C**: leva a saída do deslocador ao registrador do campo C, só se ENC = 1.

O ciclo tem 4 subciclos. O primeiro (Δw, carga do MIR) fica na unidade de controle; os
outros três estão nesta classe:

* Δx: :meth:`Datapath.load_latches`, barramentos A e B para os latches;
* Δy: :meth:`Datapath.compute`, AMUX, ULA, deslocador e carga do MAR;
* Δz: :meth:`Datapath.write_back`, barramento C para o registrador e/ou o MBR.

Também guardamos **qual registrador carregou o MAR** (``mar_source``): uma leitura com o
MAR vindo do PC é busca de instrução, e é isso que :mod:`mic1.ports` usa para escolher a
cache certa.
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Protocol

from mic1.bits import ADDRESS_MASK, SIGN_BIT, to_u16

__all__ = [
    "CONSTANTS",
    "ALUOp",
    "ALUResult",
    "Datapath",
    "DatapathControl",
    "DatapathSignals",
    "Reg",
    "RegisterFile",
    "Shift",
    "alu",
    "shifter",
]


# Registradores


class Reg(IntEnum):
    """Os 16 registradores, com o número usado nos campos A, B e C da microinstrução."""

    PC = 0
    AC = 1
    SP = 2
    IR = 3
    TIR = 4
    ZERO = 5
    PLUS_ONE = 6
    MINUS_ONE = 7
    AMASK = 8
    SMASK = 9
    A = 10
    B = 11
    C = 12
    D = 13
    E = 14
    F = 15

    @property
    def label(self) -> str:
        """Nome curto que aparece na interface: "PC", "0", "+1", "-1", "AMASK"..."""
        return _LABELS.get(self, self.name)

    @property
    def is_constant(self) -> bool:
        return self in CONSTANTS

    @classmethod
    def parse(cls, value: "Reg | int | str") -> "Reg":
        """Aceita o número (0 a 15), o nome ("PLUS_ONE") ou o rótulo curto ("+1", "pc")."""
        if isinstance(value, int):
            return cls(value)
        key = value.strip().upper()
        for register in cls:
            if key in (register.name, register.label):
                return register
        raise ValueError(f"registrador desconhecido: {value!r}")


_LABELS = {Reg.ZERO: "0", Reg.PLUS_ONE: "+1", Reg.MINUS_ONE: "-1"}

CONSTANTS: dict[Reg, int] = {
    Reg.ZERO: 0x0000,
    Reg.PLUS_ONE: 0x0001,
    Reg.MINUS_ONE: 0xFFFF,
    Reg.AMASK: 0x0FFF,
    Reg.SMASK: 0x00FF,
}


class RegisterFile:
    """Os 16 registradores. Escritas são truncadas a 16 bits; as constantes não aceitam escrita."""

    __slots__ = ("_values",)

    def __init__(self, **initial: int) -> None:
        """Cria o banco com as constantes carregadas; ex.: ``RegisterFile(sp=0x100, ac=5)``."""
        self._values = [0] * len(Reg)
        self.reset()
        for name, value in initial.items():
            self[Reg.parse(name)] = value

    def __getitem__(self, register: Reg | int | str) -> int:
        return self._values[Reg.parse(register)]

    def __setitem__(self, register: Reg | int | str, value: int) -> None:
        register = Reg.parse(register)
        if register.is_constant:
            raise ValueError(f"o registrador {register.label} é uma constante (somente leitura)")
        self._values[register] = to_u16(value)

    def reset(self) -> None:
        for register in Reg:
            self._values[register] = CONSTANTS.get(register, 0)

    def snapshot(self) -> dict[str, int]:
        """Valores atuais por rótulo, já no formato que vai para o JSON."""
        return {register.label: self._values[register] for register in Reg}

    def __repr__(self) -> str:
        body = ", ".join(f"{name}=0x{value:04X}" for name, value in self.snapshot().items())
        return f"RegisterFile({body})"


# ULA e deslocador


class ALUOp(IntEnum):
    """Campo ULA (2 bits) da microinstrução."""

    ADD = 0  # A + B
    AND = 1  # A AND B
    PASS_A = 2  # A
    NOT_A = 3  # NOT A

    @property
    def symbol(self) -> str:
        return ("A + B", "A AND B", "A", "NOT A")[self]


@dataclass(frozen=True, slots=True)
class ALUResult:
    value: int  # 16 bits sem sinal
    n: bool  # bit 15 da saída (resultado negativo)
    z: bool  # saída igual a zero


def alu(a: int, b: int, op: ALUOp | int) -> ALUResult:
    """ULA de 16 bits. O vai-um do bit 15 é descartado (o MIC-1 não trata overflow)."""
    op = ALUOp(op)
    if op is ALUOp.ADD:
        out = a + b
    elif op is ALUOp.AND:
        out = a & b
    elif op is ALUOp.PASS_A:
        out = a
    else:
        out = ~a
    out = to_u16(out)
    # As flags olham a saída da ULA, antes do deslocador: N é o bit de sinal (bit 15) e Z
    # indica resultado zero. É com elas que o microprograma faz "if n" e "if z".
    return ALUResult(value=out, n=bool(out & SIGN_BIT), z=out == 0)


class Shift(IntEnum):
    """Campo DESL (2 bits). O código 3 (11) não é usado."""

    NONE = 0
    RIGHT = 1  # rshift: 1 bit à direita, entra 0
    LEFT = 2  # lshift: 1 bit à esquerda, entra 0

    @property
    def symbol(self) -> str:
        return ("—", "rshift", "lshift")[self]

    @classmethod
    def parse(cls, value: "Shift | int") -> "Shift":
        try:
            return cls(value)
        except ValueError:
            raise ValueError(
                f"código de deslocamento inválido: {value!r} (11 não é usado)"
            ) from None


def shifter(value: int, shift: Shift | int) -> int:
    """Desloca 1 bit à esquerda (lshift) ou à direita (rshift), preenchendo com zero."""
    shift = Shift.parse(shift)
    if shift is Shift.LEFT:
        return to_u16(value << 1)
    if shift is Shift.RIGHT:
        return to_u16(value) >> 1
    return to_u16(value)


# Sinais de controle


class DatapathSignals(Protocol):
    """Campos da microinstrução que a via de dados lê (a MIR os fornece a cada ciclo)."""

    @property
    def amux(self) -> int: ...
    @property
    def alu(self) -> int: ...
    @property
    def sh(self) -> int: ...
    @property
    def mbr(self) -> int: ...
    @property
    def mar(self) -> int: ...
    @property
    def enc(self) -> int: ...
    @property
    def c(self) -> int: ...
    @property
    def b(self) -> int: ...
    @property
    def a(self) -> int: ...


@dataclass(frozen=True, slots=True)
class DatapathControl:
    """Sinais da via de dados montados à mão (para testes); na CPU, quem os fornece é a MIR."""

    amux: int = 0  # 0 = Latch A, 1 = MBR
    alu: ALUOp = ALUOp.ADD
    sh: Shift = Shift.NONE
    mbr: int = 0  # MBR ← deslocador (Δz)
    mar: int = 0  # MAR ← Latch B (Δy)
    enc: int = 0  # habilita a gravação do barramento C (Δz)
    c: int = 0
    b: int = 0
    a: int = 0

    def __post_init__(self) -> None:
        for name in ("c", "b", "a"):
            if not 0 <= getattr(self, name) <= 0xF:
                raise ValueError(
                    f"o campo {name.upper()} tem 4 bits; recebido {getattr(self, name)!r}"
                )
        object.__setattr__(self, "alu", ALUOp(self.alu))
        object.__setattr__(self, "sh", Shift.parse(self.sh))


# Via de dados


class Datapath:
    """Estado da via de dados e as três etapas que ela executa em cada ciclo (Δx, Δy, Δz)."""

    def __init__(self, registers: RegisterFile | None = None) -> None:
        self.registers = registers if registers is not None else RegisterFile()
        self._clear_internal_state()

    def _clear_internal_state(self) -> None:
        self.a_source: Reg | None = None
        self.b_source: Reg | None = None
        self.a_bus = self.b_bus = 0
        self.latch_a = self.latch_b = 0
        self.amux_from_mbr = False
        self.amux_out = 0
        self.alu_op = ALUOp.ADD
        self.alu_out = 0
        self.n = self.z = False
        self.shift = Shift.NONE
        self.shifter_out = 0
        self.c_bus = 0
        self.c_target: Reg | None = None
        self.mar = 0
        self.mar_source: Reg | None = None
        self.mbr = 0

    def reset(self) -> None:
        self.registers.reset()
        self._clear_internal_state()

    @property
    def mar_from_pc(self) -> bool:
        """``True`` quando o valor atual do MAR foi carregado a partir do PC (``mar := pc``)."""
        return self.mar_source is Reg.PC

    def load_latches(self, signals: DatapathSignals) -> None:
        """Δx: dois registradores vão aos barramentos A e B, e os latches os capturam."""
        self.a_source, self.b_source = Reg(signals.a), Reg(signals.b)
        self.a_bus = self.registers[self.a_source]
        self.b_bus = self.registers[self.b_source]
        self.latch_a, self.latch_b = self.a_bus, self.b_bus

    def compute(self, signals: DatapathSignals) -> None:
        """Δy: AMUX, ULA (com N e Z) e deslocador; se MAR = 1, o MAR recebe o Latch B."""
        self.amux_from_mbr = bool(signals.amux)
        self.amux_out = self.mbr if self.amux_from_mbr else self.latch_a
        self.alu_op = ALUOp(signals.alu)
        result = alu(self.amux_out, self.latch_b, self.alu_op)
        self.alu_out, self.n, self.z = result.value, result.n, result.z
        self.shift = Shift.parse(signals.sh)
        self.shifter_out = shifter(result.value, self.shift)
        if signals.mar:
            # O MAR só tem 12 bits; guardar a origem permite saber se é busca (veio do PC).
            self.mar = self.latch_b & ADDRESS_MASK
            self.mar_source = self.b_source

    def write_back(self, signals: DatapathSignals) -> None:
        """Δz: o barramento C grava a saída do deslocador no registrador C (se ENC) e/ou no MBR."""
        self.c_bus = self.shifter_out
        self.c_target = Reg(signals.c) if signals.enc else None
        if self.c_target is not None:
            self.registers[self.c_target] = self.c_bus
        if signals.mbr:
            self.mbr = self.c_bus

    def execute(self, signals: DatapathSignals) -> None:
        """Executa Δx, Δy e Δz em sequência (um ciclo completo da via de dados)."""
        self.load_latches(signals)
        self.compute(signals)
        self.write_back(signals)

    def load_mbr_from_memory(self, word: int) -> None:
        """Carrega no MBR a palavra entregue por uma leitura (RD) concluída."""
        self.mbr = to_u16(word)
