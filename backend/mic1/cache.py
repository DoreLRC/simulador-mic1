"""Caches L1 separadas: I-Cache (instruções) e D-Cache (dados) sobre a memória principal.

As duas caches ficam entre a porta única do MIC-1 (MAR/MBR) e a memória principal, que é
compartilhada (Harvard modificada). Quem decide a rota é :mod:`mic1.ports`:

* **I-Cache**: leituras com o MAR vindo do PC, ou seja, buscas de instrução. Só leitura.
* **D-Cache**: as outras leituras e todas as escritas. A política é **write-through** sem
  alocação na escrita (*no-write-allocate*): toda escrita vai para a memória principal e,
  se o bloco estiver na cache, a linha também é atualizada. A escrita atualiza ainda a
  cópia que estiver na I-Cache, para as duas não ficarem com valores diferentes.

Parâmetros (:class:`CacheConfig`): ``enabled``, ``lines`` (total de linhas), ``block_size``
(palavras de 16 bits por bloco), ``mapping`` (``direct`` ou ``2way`` com substituição LRU)
e ``miss_penalty`` (subciclos de espera). O endereço de 12 bits se divide em
``tag | índice | deslocamento``.

Temporização: um acerto (hit) cabe nos 6 subciclos normais do MIC-1; uma falta (miss)
soma ``miss_penalty`` subciclos de *stall*, o tempo de trazer o bloco da memória
principal. Com a cache desligada, todo acesso vai à memória e paga a penalidade. O
controlador de memória consulta :meth:`Cache.stall_for` no início do acesso e chama
``read``/``write`` quando o acesso termina.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from mic1.bits import ADDRESS_BITS, WORD_MASK
from mic1.ports import MemOp, MemoryPort

__all__ = [
    "Cache",
    "CacheAccess",
    "CacheConfig",
    "CacheLine",
    "CacheSystem",
    "DataCache",
    "InstructionCache",
    "Mapping",
]


class Mapping(StrEnum):
    """Política de mapeamento."""

    DIRECT = "direct"  # mapeamento direto: 1 via por conjunto
    TWO_WAY = "2way"  # associativa por conjunto de 2 vias, substituição LRU

    @property
    def ways(self) -> int:
        return 1 if self is Mapping.DIRECT else 2


def _is_power_of_two(value: int) -> bool:
    return value > 0 and value & (value - 1) == 0


@dataclass(frozen=True, slots=True)
class CacheConfig:
    """Configuração de uma cache. Com ``miss_penalty = 0`` a temporização é a do MIC-1 sem cache."""

    enabled: bool = True
    lines: int = 8  # total de linhas (no 2way: lines / 2 conjuntos)
    block_size: int = 2  # palavras de 16 bits por bloco
    mapping: Mapping = Mapping.DIRECT
    miss_penalty: int = 0  # subciclos de stall por miss

    def __post_init__(self) -> None:
        object.__setattr__(self, "mapping", Mapping(self.mapping))
        if not _is_power_of_two(self.lines) or self.lines > 256:
            raise ValueError(f"lines deve ser potência de 2 entre 1 e 256; recebido {self.lines}")
        if not _is_power_of_two(self.block_size) or self.block_size > 16:
            raise ValueError(
                f"block_size deve ser potência de 2 entre 1 e 16; recebido {self.block_size}"
            )
        if self.lines < self.mapping.ways:
            raise ValueError("o mapeamento 2way precisa de pelo menos 2 linhas")
        if self.lines * self.block_size > 1 << ADDRESS_BITS:
            raise ValueError("a cache não pode ser maior que a memória principal (4096 palavras)")
        if self.miss_penalty < 0:
            raise ValueError(f"miss_penalty não pode ser negativo; recebido {self.miss_penalty}")

    @property
    def sets(self) -> int:
        return self.lines // self.mapping.ways

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "lines": self.lines,
            "sets": self.sets,
            "ways": self.mapping.ways,
            "block_size": self.block_size,
            "mapping": self.mapping.value,
            "miss_penalty": self.miss_penalty,
        }


@dataclass(slots=True)
class CacheLine:
    valid: bool = False
    tag: int = 0
    data: list[int] = field(default_factory=list)
    last_used: int = 0  # carimbo de uso, para o LRU


@dataclass(frozen=True, slots=True)
class CacheAccess:
    """O último acesso feito à cache (é o que a interface destaca)."""

    op: MemOp
    address: int
    tag: int
    index: int
    offset: int
    hit: bool
    way: int | None  # via usada (None com a cache desligada ou num write miss)
    evicted_tag: int | None = None  # tag do bloco válido descartado num miss
    enabled: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "result": "hit" if self.hit else "miss",
            "op": self.op.value,
            "address": self.address,
            "tag": self.tag,
            "index": self.index,
            "offset": self.offset,
            "way": self.way,
            "evicted_tag": self.evicted_tag,
            "bypass": not self.enabled,
        }


class Cache:
    """Cache genérica de leitura; as subclasses definem o tratamento das escritas."""

    name = "Cache"

    def __init__(self, backing: MemoryPort, config: CacheConfig | None = None) -> None:
        self.backing = backing
        self.config = config if config is not None else CacheConfig()
        self.ways = self.config.mapping.ways
        self.sets = self.config.sets
        self.offset_bits = self.config.block_size.bit_length() - 1
        self.index_bits = self.sets.bit_length() - 1
        self.tag_bits = ADDRESS_BITS - self.offset_bits - self.index_bits
        self.reset()

    def reset(self) -> None:
        """Esvazia a cache e zera as estatísticas."""
        block = self.config.block_size
        self._sets = [
            [CacheLine(data=[0] * block) for _ in range(self.ways)] for _ in range(self.sets)
        ]
        self._clock = 0
        self.hits = 0
        self.misses = 0
        self.last_access: CacheAccess | None = None

    def invalidate(self) -> None:
        """Marca todas as linhas como inválidas (as estatísticas continuam)."""
        for lines in self._sets:
            for line in lines:
                line.valid = False

    @property
    def accesses(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        return self.hits / self.accesses if self.accesses else 0.0

    def split(self, address: int) -> tuple[int, int, int]:
        """Divide o endereço de 12 bits em (tag, índice, deslocamento)."""
        # Ex.: 8 conjuntos e blocos de 2 palavras dão 1 bit de offset, 3 de índice e os 8
        # restantes de tag. O índice escolhe o conjunto e a tag confirma se o bloco é o certo.
        offset = address & (self.config.block_size - 1)
        index = (address >> self.offset_bits) & (self.sets - 1)
        tag = address >> (self.offset_bits + self.index_bits)
        return tag, index, offset

    def stall_for(self, op: MemOp, address: int) -> int:
        """Subciclos de espera do acesso: 0 num hit; a penalidade num miss ou sem cache."""
        # Só consulta: não mexe nas linhas nem nas estatísticas (isso fica para o read/write).
        if not self.config.enabled:
            return self.config.miss_penalty
        tag, index, _ = self.split(address)
        return 0 if self._find(index, tag) is not None else self.config.miss_penalty

    def read(self, address: int) -> int:
        if not self.config.enabled:
            self._record(MemOp.READ, address, hit=False, way=None)
            return self.backing.read(address) & WORD_MASK
        tag, index, offset = self.split(address)
        way = self._find(index, tag)
        hit, evicted = way is not None, None
        if way is None:  # miss: traz o bloco inteiro, aproveitando a localidade espacial
            way, evicted = self._fill(index, tag, address)
        self._touch(index, way)
        self._record(MemOp.READ, address, hit=hit, way=way, evicted=evicted)
        return self._sets[index][way].data[offset]

    def write(self, address: int, value: int) -> None:
        raise NotImplementedError

    def snoop(self, address: int, value: int) -> None:
        """Atualiza a cópia do endereço, se ela estiver na cache (sem contar como acesso)."""
        if not self.config.enabled:
            return
        tag, index, offset = self.split(address)
        way = self._find(index, tag)
        if way is not None:
            self._sets[index][way].data[offset] = value & WORD_MASK

    def snapshot(self) -> dict[str, object]:
        lines = []
        for index, ways in enumerate(self._sets):
            victim = self._victim(index) if self.ways > 1 else None
            for way, line in enumerate(ways):
                lines.append(
                    {
                        "set": index,
                        "way": way,
                        "valid": line.valid,
                        "tag": line.tag,
                        "data": list(line.data),
                        "lru": None if victim is None else victim == way,
                    }
                )
        return {
            "name": self.name,
            "config": self.config.to_dict(),
            "address_bits": {
                "tag": self.tag_bits,
                "index": self.index_bits,
                "offset": self.offset_bits,
            },
            "hits": self.hits,
            "misses": self.misses,
            "accesses": self.accesses,
            "hit_rate": round(self.hit_rate, 4),
            "last_access": None if self.last_access is None else self.last_access.to_dict(),
            "lines": lines,
        }

    def _find(self, index: int, tag: int) -> int | None:
        for way, line in enumerate(self._sets[index]):
            if line.valid and line.tag == tag:
                return way
        return None

    def _victim(self, index: int) -> int:
        """Via a substituir: a primeira inválida; senão, a usada há mais tempo (LRU)."""
        lines = self._sets[index]
        for way, line in enumerate(lines):
            if not line.valid:
                return way
        return min(range(len(lines)), key=lambda way: lines[way].last_used)

    def _fill(self, index: int, tag: int, address: int) -> tuple[int, int | None]:
        """Traz da memória principal o bloco que contém ``address``."""
        way = self._victim(index)
        line = self._sets[index][way]
        evicted = line.tag if line.valid else None
        base = address - (address & (self.config.block_size - 1))
        line.data = [self.backing.read(base + i) & WORD_MASK for i in range(self.config.block_size)]
        line.valid, line.tag = True, tag
        return way, evicted

    def _touch(self, index: int, way: int) -> None:
        self._clock += 1
        self._sets[index][way].last_used = self._clock

    def _record(
        self, op: MemOp, address: int, *, hit: bool, way: int | None, evicted: int | None = None
    ) -> None:
        if hit:
            self.hits += 1
        else:
            self.misses += 1
        tag, index, offset = self.split(address)
        self.last_access = CacheAccess(
            op, address, tag, index, offset, hit, way, evicted, self.config.enabled
        )


class InstructionCache(Cache):
    """I-Cache: atende as buscas de instrução (RD com MAR vindo do PC). Só leitura."""

    name = "I-Cache"

    def write(self, address: int, value: int) -> None:
        raise TypeError("a I-Cache é só de leitura: escritas vão pela D-Cache")


class DataCache(Cache):
    """D-Cache: demais leituras e todas as escritas (write-through, no-write-allocate)."""

    name = "D-Cache"

    def __init__(
        self,
        backing: MemoryPort,
        config: CacheConfig | None = None,
        *,
        on_write: Callable[[int, int], None] | None = None,
    ) -> None:
        super().__init__(backing, config)
        self._on_write = on_write

    def write(self, address: int, value: int) -> None:
        value &= WORD_MASK
        if not self.config.enabled:
            self._record(MemOp.WRITE, address, hit=False, way=None)
        else:
            tag, index, offset = self.split(address)
            way = self._find(index, tag)
            if way is not None:  # write hit: atualiza a linha
                self._sets[index][way].data[offset] = value
                self._touch(index, way)
            self._record(MemOp.WRITE, address, hit=way is not None, way=way)
        self.backing.write(address, value)  # write-through: a memória sempre recebe
        if self._on_write is not None:
            self._on_write(address, value)


class CacheSystem:
    """As duas caches L1 sobre a mesma memória principal."""

    def __init__(
        self,
        memory: MemoryPort,
        icache: CacheConfig | None = None,
        dcache: CacheConfig | None = None,
    ) -> None:
        self.memory = memory
        self.icache = InstructionCache(memory, icache)
        self.dcache = DataCache(memory, dcache, on_write=self.icache.snoop)

    def reset(self) -> None:
        self.icache.reset()
        self.dcache.reset()

    def invalidate(self) -> None:
        self.icache.invalidate()
        self.dcache.invalidate()

    def snapshot(self) -> dict[str, object]:
        return {"instruction": self.icache.snapshot(), "data": self.dcache.snapshot()}
