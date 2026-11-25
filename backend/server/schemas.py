"""Corpos das requisições da API (validação de entrada com Pydantic).

As regras do simulador (linhas em potência de 2, cache menor que a memória…) continuam no
núcleo, em :class:`mic1.cache.CacheConfig`; aqui só ficam os tipos e os limites de segurança
do servidor.
"""

from typing import Literal

from pydantic import BaseModel, Field

from mic1.cache import CacheConfig
from mic1.control import DecodeMode

_DEFAULT = CacheConfig()
MAX_SOURCE_CHARS = 200_000
MAX_MISS_PENALTY = 50  # evita pedidos que travariam o servidor em subciclos de espera

Granularity = Literal["subcycle", "micro", "macro", "halt"]


class CacheSettings(BaseModel):
    """Configuração de uma cache L1 (os mesmos campos de ``CacheConfig``)."""

    enabled: bool = _DEFAULT.enabled
    lines: int = Field(_DEFAULT.lines, ge=1, le=256)
    block_size: int = Field(_DEFAULT.block_size, ge=1, le=16)
    mapping: Literal["direct", "2way"] = "direct"
    miss_penalty: int = Field(_DEFAULT.miss_penalty, ge=0, le=MAX_MISS_PENALTY)

    def to_config(self) -> CacheConfig:
        return CacheConfig(**self.model_dump())


class MachineSettings(BaseModel):
    """Modo de decodificação e caches. ``cache`` vale para as duas, salvo ``icache``/``dcache``."""

    mode: DecodeMode = DecodeMode.ORIGINAL
    cache: CacheSettings = Field(default_factory=CacheSettings)
    icache: CacheSettings | None = None
    dcache: CacheSettings | None = None


class AssembleRequest(MachineSettings):
    source: str = Field(max_length=MAX_SOURCE_CHARS)


class StepRequest(BaseModel):
    granularity: Granularity = "micro"


class ResetRequest(BaseModel):
    """Campos omitidos mantêm a configuração atual."""

    mode: DecodeMode | None = None
    cache: CacheSettings | None = None
    icache: CacheSettings | None = None
    dcache: CacheSettings | None = None
