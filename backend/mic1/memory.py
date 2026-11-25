"""Memória principal do MIC-1 (4096 palavras de 16 bits) e a temporização dos acessos.

A memória leva **6 subciclos** para responder. O acesso começa no Δz da microinstrução que
liga RD (ou WR), quando o MAR (carregado no Δy) e o MBR (carregado no Δz) já estão
estáveis. O sinal tem de continuar ligado na microinstrução seguinte, e o acesso termina no
fim do Δw da microinstrução k+2::

    ciclo k    Δz             → 1º subciclo (início: endereço = MAR, dado = MBR)
    ciclo k+1  Δw Δx Δy Δz    → 2º ao 5º subciclos (RD/WR mantido)
    ciclo k+2  Δw             → 6º subciclo: a palavra chega ao MBR (ou é gravada)

Por isso o microprograma usa pares como ``mar := pc; rd;`` e ``pc := pc + 1; rd;`` e só
lê o MBR na microinstrução seguinte (``ir := mbr``). Se o sinal não for mantido no ciclo
k+1, o acesso é abortado e nada é lido nem gravado.

Com as caches (:mod:`mic1.cache`), a rota diz no início do acesso quantos subciclos de
espera ele vai custar: 0 num hit, a penalidade num miss. Quando há espera, a CPU fica
parada (*stall*) logo antes do Δw de k+2 até o acesso acabar; o microprograma é o mesmo.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from mic1.bits import to_u16
from mic1.datapath import Reg
from mic1.ports import MemOp, MemoryPort, MemoryRequest, dispatch, route, stall_for

__all__ = ["LATENCY_SUBCYCLES", "MEMORY_WORDS", "MainMemory", "MemoryController", "MemoryEvent"]

MEMORY_WORDS = 4096
LATENCY_SUBCYCLES = 6


class MainMemory:
    """4096 palavras de 16 bits, endereço de 12 bits. Implementa :class:`~mic1.ports.MemoryPort`."""

    def __init__(self, size: int = MEMORY_WORDS) -> None:
        self._words = [0] * size

    def __len__(self) -> int:
        return len(self._words)

    def read(self, address: int) -> int:
        return self._words[self._check(address)]

    def write(self, address: int, value: int) -> None:
        self._words[self._check(address)] = to_u16(value)

    def load(self, words: Iterable[int], start: int = 0) -> None:
        """Copia ``words`` para a memória a partir de ``start``."""
        for offset, word in enumerate(words):
            self.write(start + offset, word)

    def dump(self, start: int = 0, count: int | None = None) -> list[int]:
        end = len(self._words) if count is None else start + count
        self._check(start)
        self._check(end - 1)
        return self._words[start:end]

    def clear(self) -> None:
        self._words = [0] * len(self._words)

    def _check(self, address: int) -> int:
        if not 0 <= address < len(self._words):
            raise IndexError(f"endereço de memória fora de 0–{len(self._words) - 1}: {address}")
        return address


@dataclass(frozen=True, slots=True)
class MemoryEvent:
    """Algo que aconteceu com um acesso: início, continuação, conclusão ou aborto."""

    kind: str  # "start" | "continue" | "complete" | "abort"
    request: MemoryRequest
    value: int | None = None  # palavra lida, quando um RD é concluído
    stall: int = 0  # subciclos de espera deste acesso (miss de cache)

    def to_dict(self) -> dict[str, object]:
        source = self.request.mar_source
        return {
            "type": f"memory_{self.kind}",
            "op": self.request.op.value,
            "address": self.request.address,
            "kind": self.request.kind.value,
            "data": self.request.data,
            "value": self.value,
            "mar_source": None if source is None else source.label,
            "stall": self.stall,
        }


@dataclass(slots=True)
class _Pending:
    request: MemoryRequest
    stall: int = 0  # subciclos de espera além dos 6 normais
    elapsed: int = 1  # subciclos já decorridos (o de início conta como o 1º)
    confirmed: bool = False  # o sinal foi mantido na microinstrução seguinte?

    @property
    def duration(self) -> int:
        return LATENCY_SUBCYCLES + self.stall


class MemoryController:
    """Controla os acessos à memória: latência de 6 subciclos, stalls e rotas I/D."""

    def __init__(
        self,
        memory: MainMemory,
        *,
        instruction_port: MemoryPort | None = None,
        data_port: MemoryPort | None = None,
    ) -> None:
        self.memory = memory
        self.instruction_port: MemoryPort = instruction_port if instruction_port else memory
        self.data_port: MemoryPort = data_port if data_port else memory
        self._pending: _Pending | None = None

    @property
    def busy(self) -> bool:
        return self._pending is not None

    def reset(self) -> None:
        self._pending = None

    def needs_stall(self) -> bool:
        """A CPU deve esperar antes do próximo Δw: o acesso já passou da janela normal."""
        # Num hit a duração é exatamente 6 e isto nunca é verdade. Num miss, o 6º subciclo
        # (que seria o Δw de k+2) só pode acontecer depois dos subciclos de penalidade.
        pending = self._pending
        return (
            pending is not None
            and pending.elapsed + 1 >= LATENCY_SUBCYCLES
            and pending.elapsed + 1 < pending.duration
        )

    def tick(self) -> MemoryEvent | None:
        """Avança um subciclo o acesso em andamento; ele termina após 6 + stall subciclos."""
        pending = self._pending
        if pending is None:
            return None
        pending.elapsed += 1
        if pending.elapsed < pending.duration:
            return None
        self._pending = None
        value = dispatch(
            pending.request, instruction_port=self.instruction_port, data_port=self.data_port
        )
        return MemoryEvent("complete", pending.request, value, pending.stall)

    def signal(
        self, *, rd: bool, wr: bool, mar: int, mbr: int, mar_source: Reg | None
    ) -> list[MemoryEvent]:
        """Recebe os sinais RD/WR da MIR no Δz: inicia, mantém ou aborta um acesso."""
        if rd and wr:
            raise ValueError("RD e WR não podem ser ativados na mesma microinstrução")
        op = MemOp.READ if rd else MemOp.WRITE if wr else None
        events: list[MemoryEvent] = []

        pending = self._pending
        if pending is not None:
            if op is pending.request.op and not pending.confirmed:
                pending.confirmed = True
                return [MemoryEvent("continue", pending.request, stall=pending.stall)]
            events.append(MemoryEvent("abort", pending.request, stall=pending.stall))
            self._pending = None

        if op is not None:
            # A penalidade é decidida aqui, no início: a cache só diz se o bloco está lá.
            # Os dados (e as estatísticas de hit/miss) só mudam quando o acesso termina.
            request = MemoryRequest.from_signals(op, mar=mar, mbr=mbr, mar_source=mar_source)
            port = route(request, instruction_port=self.instruction_port, data_port=self.data_port)
            self._pending = _Pending(request, stall=stall_for(port, request))
            events.append(MemoryEvent("start", request, stall=self._pending.stall))
        return events

    def status(self) -> dict[str, object]:
        """Estado do acesso em andamento, no formato que vai para o JSON."""
        pending = self._pending
        if pending is None:
            return {
                "busy": False,
                "op": None,
                "address": None,
                "kind": None,
                "data": None,
                "elapsed": 0,
                "latency": LATENCY_SUBCYCLES,
                "stall": 0,
                "waiting": False,
            }
        return {
            "busy": True,
            "op": pending.request.op.value,
            "address": pending.request.address,
            "kind": pending.request.kind.value,
            "data": pending.request.data,
            "elapsed": pending.elapsed,
            "latency": LATENCY_SUBCYCLES,
            "stall": pending.stall,
            "waiting": pending.elapsed >= LATENCY_SUBCYCLES,
        }
