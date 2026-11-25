"""Sessões de simulação: cada navegador tem a sua CPU, identificada por um cookie.

Esta camada só cola o núcleo à API: monta a CPU com o programa e a configuração pedidos,
chama os métodos de passo e empacota o snapshot junto com a imagem da memória.
"""

import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass

from mic1.cache import CacheConfig
from mic1.control import DecodeMode
from mic1.cpu import CPU, HALT_CYCLE_LIMIT

__all__ = ["MAX_EVENTS", "SessionStore", "Settings", "Simulation"]

MAX_EVENTS = 200  # eventos devolvidos por resposta (um "halt" pode gerar milhares)
MAX_SESSIONS = 64


@dataclass(frozen=True, slots=True)
class Settings:
    mode: DecodeMode
    icache: CacheConfig
    dcache: CacheConfig

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "icache": self.icache.to_dict(),
            "dcache": self.dcache.to_dict(),
        }


class Simulation:
    """Uma CPU com o programa montado. Levanta ``AssemblyError`` se o fonte tiver erros."""

    def __init__(self, source: str, settings: Settings) -> None:
        cpu = CPU(settings.mode, icache=settings.icache, dcache=settings.dcache)
        self.program = cpu.load_assembly(source)
        self.cpu = cpu
        self.source = source
        self.settings = settings
        self.lock = threading.Lock()  # um passo de cada vez na mesma CPU

    def restarted(self, settings: Settings | None = None) -> "Simulation":
        """Nova simulação do mesmo programa (memória recarregada), com outra config. se dada."""
        return Simulation(self.source, settings or self.settings)

    def step(self, granularity: str) -> dict[str, object]:
        cpu = self.cpu
        if granularity == "halt":
            snapshot = cpu.run(HALT_CYCLE_LIMIT)
            return self.state(snapshot, limit_reached=not snapshot["halted"])
        steps = {"subcycle": cpu.step_subcycle, "micro": cpu.step_micro, "macro": cpu.step_macro}
        return self.state(steps[granularity]())

    def state(
        self,
        snapshot: dict[str, object] | None = None,
        *,
        limit_reached: bool = False,
        include_program: bool = False,
    ) -> dict[str, object]:
        """O que a interface precisa para desenhar a tela."""
        if snapshot is None:
            snapshot = self.cpu.snapshot()
        events = snapshot["events"]
        assert isinstance(events, list)
        state: dict[str, object] = {
            "snapshot": {**snapshot, "events": events[-MAX_EVENTS:]},
            "memory": self.cpu.memory.dump(),
            "halted": snapshot["halted"],
            "limit_reached": limit_reached,
            "cycle_limit": HALT_CYCLE_LIMIT,
            "events_total": len(events),
            "settings": self.settings.to_dict(),
        }
        if include_program:
            state["program"] = self.program.to_dict()
            state["source"] = self.source
        return state


class SessionStore:
    """Simulações por id de sessão; as menos usadas saem quando passa do limite."""

    def __init__(self, limit: int = MAX_SESSIONS) -> None:
        self.limit = limit
        self._sessions: OrderedDict[str, Simulation] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def new_id() -> str:
        return secrets.token_urlsafe(16)

    def get(self, session_id: str | None) -> Simulation | None:
        if session_id is None:
            return None
        with self._lock:
            simulation = self._sessions.get(session_id)
            if simulation is not None:
                self._sessions.move_to_end(session_id)
            return simulation

    def put(self, session_id: str, simulation: Simulation) -> None:
        with self._lock:
            self._sessions[session_id] = simulation
            self._sessions.move_to_end(session_id)
            while len(self._sessions) > self.limit:
                self._sessions.popitem(last=False)

    def __len__(self) -> int:
        return len(self._sessions)
