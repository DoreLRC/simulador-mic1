"""Unidade de controle do MIC-1: MIR de 32 bits, memória de controle, MPC e MMUX.

Formato da microinstrução, do bit 31 ao bit 0::

    AMUX(1) COND(2) ULA(2) DESL(2) MBR(1) MAR(1) RD(1) WR(1) ENC(1) C(4) B(4) A(4) ADDR(8)

No fim de cada ciclo (Δz) o MMUX escolhe o próximo MPC entre ``MPC + 1`` e ``ADDR``,
olhando o campo COND e as flags N e Z que a ULA acabou de calcular::

    COND 00 → MPC + 1    01 → ADDR se N    10 → ADDR se Z    11 → ADDR sempre

Implementamos dois jeitos de descobrir qual macroinstrução foi buscada:

* **original**: microprograma de 79 microinstruções, com a busca no endereço 0. O opcode
  é testado bit a bit numa árvore de desvios (``if n then goto``).
* **case**: o hardware liga IR[15:12] às entradas 5 a 2 do MMUX. A busca fica em 252 a
  254 e, quando o próximo endereço seria 255, o MPC recebe ``IR[15:12] × 4``. Isso pula
  direto para a rotina do opcode e elimina a árvore de testes.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import IntEnum, StrEnum

from mic1.datapath import ALUOp, Shift

__all__ = [
    "CASE_DISPATCH_ADDRESS",
    "CONTROL_STORE_SIZE",
    "FIELDS",
    "Cond",
    "ControlStore",
    "ControlUnit",
    "DecodeMode",
    "MicroInstruction",
    "MicroStep",
    "case_target",
    "next_address",
]

CONTROL_STORE_SIZE = 256
CASE_DISPATCH_ADDRESS = 255

# Campos da microinstrução, do bit mais significativo para o menos significativo.
FIELDS: tuple[tuple[str, int], ...] = (
    ("amux", 1),
    ("cond", 2),
    ("alu", 2),
    ("sh", 2),
    ("mbr", 1),
    ("mar", 1),
    ("rd", 1),
    ("wr", 1),
    ("enc", 1),
    ("c", 4),
    ("b", 4),
    ("a", 4),
    ("addr", 8),
)


class Cond(IntEnum):
    """Campo COND: quando o MMUX escolhe ADDR em vez de MPC + 1."""

    NONE = 0
    IF_N = 1
    IF_Z = 2
    ALWAYS = 3


class DecodeMode(StrEnum):
    """Como o microprograma identifica a macroinstrução buscada."""

    ORIGINAL = "original"  # árvore de desvios testando o opcode bit a bit
    CASE = "case"  # desvio múltiplo feito pelo hardware

    @property
    def fetch_address(self) -> int:
        """Endereço da primeira microinstrução da busca (início de cada macroinstrução)."""
        return 0 if self is DecodeMode.ORIGINAL else 252

    @property
    def microcode_file(self) -> str:
        return "mic1.mal" if self is DecodeMode.ORIGINAL else "mic1_case.mal"


@dataclass(frozen=True, slots=True)
class MicroInstruction:
    """Conteúdo de uma palavra de 32 bits da memória de controle (e da MIR)."""

    amux: int = 0
    cond: Cond = Cond.NONE
    alu: ALUOp = ALUOp.ADD
    sh: Shift = Shift.NONE
    mbr: int = 0
    mar: int = 0
    rd: int = 0
    wr: int = 0
    enc: int = 0
    c: int = 0
    b: int = 0
    a: int = 0
    addr: int = 0

    def __post_init__(self) -> None:
        for name, width in FIELDS:
            value = int(getattr(self, name))
            if not 0 <= value < (1 << width):
                raise ValueError(f"o campo {name.upper()} tem {width} bit(s); recebido {value!r}")
        if self.rd and self.wr:
            raise ValueError("RD e WR não podem ser ativados na mesma microinstrução")
        object.__setattr__(self, "cond", Cond(self.cond))
        object.__setattr__(self, "alu", ALUOp(self.alu))
        object.__setattr__(self, "sh", Shift.parse(self.sh))

    def encode(self) -> int:
        """Palavra de 32 bits, com AMUX no bit 31 e ADDR nos bits 7–0."""
        word = 0
        for name, width in FIELDS:
            word = (word << width) | int(getattr(self, name))
        return word

    @classmethod
    def decode(cls, word: int) -> "MicroInstruction":
        if not 0 <= word <= 0xFFFF_FFFF:
            raise ValueError(f"a microinstrução tem 32 bits; recebido {word!r}")
        values: dict[str, int] = {}
        position = 32
        for name, width in FIELDS:
            position -= width
            values[name] = (word >> position) & ((1 << width) - 1)
        return cls(**values)  # type: ignore[arg-type]

    def bit_groups(self) -> str:
        """Bits separados por campo, ex.: ``0 00 00 00 0 1 1 0 0 0000 0000 0000 00000001``."""
        return " ".join(format(int(getattr(self, name)), f"0{width}b") for name, width in FIELDS)

    def fields(self) -> dict[str, int]:
        """Valor numérico de cada campo, no formato que vai para o JSON."""
        return {name: int(getattr(self, name)) for name, _ in FIELDS}


@dataclass(frozen=True, slots=True)
class MicroStep:
    """Uma posição ocupada da memória de controle: a microinstrução e o seu texto MAL."""

    address: int
    instruction: MicroInstruction
    source: str = ""  # ex.: "mar := pc; rd;"
    comment: str = ""  # ex.: "0000 = LODD"


class ControlStore:
    """Memória de controle: 256 palavras de 32 bits (8 Kbits)."""

    def __init__(self, steps: Iterable[MicroStep] = ()) -> None:
        slots: list[MicroStep | None] = [None] * CONTROL_STORE_SIZE
        for step in steps:
            if not 0 <= step.address < CONTROL_STORE_SIZE:
                raise ValueError(f"endereço de controle fora de 0–255: {step.address}")
            if slots[step.address] is not None:
                raise ValueError(f"endereço de controle {step.address} ocupado duas vezes")
            slots[step.address] = step
        self._slots = tuple(slots)

    def __getitem__(self, address: int) -> MicroStep:
        step = self._slots[address]
        if step is None:
            raise LookupError(f"não há microinstrução no endereço {address} da memória de controle")
        return step

    def get(self, address: int) -> MicroStep | None:
        return self._slots[address]

    def __len__(self) -> int:
        return sum(1 for step in self._slots if step is not None)

    def __iter__(self) -> Iterator[MicroStep]:
        return (step for step in self._slots if step is not None)

    @property
    def used_bits(self) -> int:
        """Bits ocupados pelo microprograma (ex.: 79 × 32 = 2528)."""
        return len(self) * 32


def next_address(mpc: int, mir: MicroInstruction, n: bool, z: bool) -> int:
    """Saída do MMUX: ADDR quando a condição de COND é satisfeita; senão MPC + 1."""
    # A "lógica de microssequenciamento" do diagrama é só esta expressão: COND diz qual
    # flag olhar, e N/Z vêm da ULA no Δy do mesmo ciclo.
    jump = mir.cond is Cond.ALWAYS or (mir.cond is Cond.IF_N and n) or (mir.cond is Cond.IF_Z and z)
    return mir.addr if jump else (mpc + 1) % CONTROL_STORE_SIZE


def case_target(ir: int) -> int:
    """Desvio do modo case: IR[15:12] ligado às entradas 5 a 2 do MMUX (opcode × 4)."""
    # Ligar os 4 bits do opcode deslocados de 2 posições dá 4 microinstruções por opcode:
    # LODD (0000) cai em 0, STOD (0001) em 4, ADDD (0010) em 8, e assim por diante.
    return ((ir >> 12) & 0xF) << 2


class ControlUnit:
    """MPC, MIR, memória de controle e a lógica de sequenciamento (MMUX)."""

    def __init__(self, store: ControlStore, mode: DecodeMode = DecodeMode.ORIGINAL) -> None:
        self.store = store
        self.mode = DecodeMode(mode)
        self.reset()

    def reset(self) -> None:
        self.mpc = self.mode.fetch_address
        self.current: MicroStep | None = None
        self.dispatched = False  # o último sequenciamento usou o desvio do case?

    @property
    def mir(self) -> MicroInstruction:
        if self.current is None:
            raise RuntimeError("a MIR ainda não foi carregada (execute o subciclo Δw)")
        return self.current.instruction

    def load_mir(self) -> MicroStep:
        """Δw: a MIR recebe a microinstrução apontada pelo MPC."""
        self.current = self.store[self.mpc]
        return self.current

    def sequence(self, n: bool, z: bool, ir: int) -> int:
        """Δz: calcula o próximo MPC (MMUX), incluindo o desvio múltiplo do modo case."""
        target = next_address(self.mpc, self.mir, n, z)
        # No modo case, o 255 funciona como gatilho: em vez de ir para 255, o MPC vai para
        # a rotina do opcode que está no IR.
        self.dispatched = self.mode is DecodeMode.CASE and target == CASE_DISPATCH_ADDRESS
        if self.dispatched:
            target = case_target(ir)
        self.mpc = target
        return target
