"""Ligação da CPU com a memória e a divisão dos acessos entre as caches L1.

O MIC-1 tem **uma única porta de memória** (MAR/MBR, sinais RD e WR), como toda máquina
Von Neumann. As duas caches L1 (o que dá uma Harvard modificada) ficam entre essa porta e
a memória principal, e cada acesso é classificado pela origem do endereço:

* RD com o MAR carregado a partir do PC (``mar := pc``) → busca de instrução → **I-Cache**;
* qualquer outro RD, e todo WR → acesso a dado → **D-Cache**.

O controlador de memória (:mod:`mic1.memory`) só conversa com objetos que seguem o
protocolo :class:`MemoryPort`. Sem caches, as duas rotas levam à memória principal; com
caches (:mod:`mic1.cache`), cada rota recebe a sua, que repassa os misses à memória
(padrão *decorator*).

Uma porta pode também seguir :class:`TimedPort`: no início do acesso o controlador pergunta
quantos subciclos de espera (*stall*) ele vai custar, 0 num acerto de cache ou a penalidade
de miss caso contrário.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from mic1.bits import ADDRESS_MASK, WORD_MASK
from mic1.datapath import Reg

__all__ = [
    "AccessKind",
    "MemOp",
    "MemoryPort",
    "MemoryRequest",
    "TimedPort",
    "classify",
    "dispatch",
    "route",
    "stall_for",
]


class MemOp(StrEnum):
    """Operações de memória, com a grafia usada em MAL."""

    READ = "rd"
    WRITE = "wr"


class AccessKind(StrEnum):
    """Tipo do acesso: é ele que decide qual cache L1 atende."""

    INSTRUCTION = "instruction"
    DATA = "data"


@runtime_checkable
class MemoryPort(Protocol):
    """Porta de palavras de 16 bits com endereço de 12 bits (memória principal ou cache)."""

    def read(self, address: int) -> int:
        """Devolve a palavra guardada em ``address``."""
        ...

    def write(self, address: int, value: int) -> None:
        """Grava ``value`` em ``address``."""
        ...


@runtime_checkable
class TimedPort(Protocol):
    """Porta que informa, no início do acesso, quantos subciclos de espera ele vai custar."""

    def stall_for(self, op: "MemOp", address: int) -> int:
        """Subciclos de *stall* além da janela normal de 6 (0 num cache hit)."""
        ...


def classify(op: MemOp, mar_source: Reg | None) -> AccessKind:
    """RD com MAR vindo do PC é busca de instrução; todo o resto é acesso a dado."""
    if op is MemOp.READ and mar_source == Reg.PC:
        return AccessKind.INSTRUCTION
    return AccessKind.DATA


@dataclass(frozen=True, slots=True)
class MemoryRequest:
    """Um acesso iniciado pelo controlador de memória."""

    op: MemOp
    address: int  # 12 bits (valor do MAR no início do acesso)
    kind: AccessKind
    data: int | None = None  # WR: palavra a gravar (valor do MBR no início do acesso)
    mar_source: Reg | None = None  # registrador que carregou o MAR

    def __post_init__(self) -> None:
        if not 0 <= self.address <= ADDRESS_MASK:
            raise ValueError(f"endereço fora de 12 bits: {self.address!r}")
        if (self.op is MemOp.WRITE) != (self.data is not None):
            raise ValueError("só o WR carrega dado; o RD não carrega")

    @classmethod
    def from_signals(
        cls, op: MemOp, *, mar: int, mbr: int, mar_source: Reg | None
    ) -> "MemoryRequest":
        """Monta o pedido a partir do MAR/MBR atuais, já classificado para a cache certa."""
        return cls(
            op=op,
            address=mar & ADDRESS_MASK,
            kind=classify(op, mar_source),
            data=(mbr & WORD_MASK) if op is MemOp.WRITE else None,
            mar_source=mar_source,
        )


def route(
    request: MemoryRequest, *, instruction_port: MemoryPort, data_port: MemoryPort
) -> MemoryPort:
    """A porta que atende o pedido: instruções → ``instruction_port``; dados → ``data_port``."""
    return instruction_port if request.kind is AccessKind.INSTRUCTION else data_port


def stall_for(port: MemoryPort, request: MemoryRequest) -> int:
    """Subciclos de espera que ``port`` cobra pelo pedido (0 numa porta sem temporização)."""
    if isinstance(port, TimedPort):
        return max(0, int(port.stall_for(request.op, request.address)))
    return 0


def dispatch(
    request: MemoryRequest, *, instruction_port: MemoryPort, data_port: MemoryPort
) -> int | None:
    """Executa o acesso na rota certa: instruções → ``instruction_port``; dados → ``data_port``.

    Devolve a palavra lida (RD) ou ``None`` (WR).
    """
    port = route(request, instruction_port=instruction_port, data_port=data_port)
    if request.op is MemOp.READ:
        return port.read(request.address) & WORD_MASK
    assert request.data is not None  # garantido por MemoryRequest.__post_init__
    port.write(request.address, request.data)
    return None
