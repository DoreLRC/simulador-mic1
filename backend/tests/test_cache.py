"""Caches L1: tag/índice/deslocamento, hits e misses, LRU, write-through e coerência I/D."""

import json

import pytest

from mic1.cache import CacheConfig, CacheSystem, DataCache, InstructionCache
from mic1.memory import MainMemory
from mic1.ports import MemOp, TimedPort


def memory_with(values: dict[int, int]) -> MainMemory:
    memory = MainMemory()
    for address, value in values.items():
        memory.write(address, value)
    return memory


# --- Configuração ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"lines": 6},
        {"lines": 512},
        {"block_size": 3},
        {"block_size": 32},
        {"lines": 1, "mapping": "2way"},
        {"miss_penalty": -1},
        {"mapping": "4way"},
    ],
)
def test_invalid_configurations(fields):
    with pytest.raises(ValueError):
        CacheConfig(**fields)


def test_two_way_splits_the_lines_into_sets():
    config = CacheConfig(lines=8, mapping="2way")
    assert (config.sets, config.mapping.ways) == (4, 2)


@pytest.mark.parametrize(
    ("config", "bits"),
    [
        (CacheConfig(lines=8, block_size=2), (8, 3, 1)),
        (CacheConfig(lines=16, block_size=4), (6, 4, 2)),
        (CacheConfig(lines=8, block_size=2, mapping="2way"), (9, 2, 1)),
        (CacheConfig(lines=4, block_size=1), (10, 2, 0)),
    ],
)
def test_tag_index_and_offset_add_up_to_12_bits(config, bits):
    cache = DataCache(MainMemory(), config)
    assert (cache.tag_bits, cache.index_bits, cache.offset_bits) == bits
    assert sum(bits) == 12


def test_split_address():
    cache = DataCache(MainMemory(), CacheConfig(lines=8, block_size=2))
    assert cache.split(0x0A5) == (0x0A, 2, 1)  # 0000 1010 | 010 | 1


# --- Leitura ---------------------------------------------------------------------------------


def test_cold_miss_then_hits_thanks_to_the_block():
    cache = DataCache(memory_with({10: 111, 11: 222}), CacheConfig(lines=8, block_size=2))
    assert cache.read(10) == 111  # miss: traz o bloco 10–11
    assert cache.read(11) == 222  # hit: mesmo bloco (localidade espacial)
    assert cache.read(10) == 111  # hit (localidade temporal)
    assert (cache.hits, cache.misses) == (2, 1)
    assert cache.hit_rate == pytest.approx(2 / 3)


def test_stall_is_zero_on_hit_and_the_penalty_on_miss():
    cache = InstructionCache(MainMemory(), CacheConfig(miss_penalty=5))
    assert isinstance(cache, TimedPort)
    assert cache.stall_for(MemOp.READ, 4) == 5
    cache.read(4)
    assert cache.stall_for(MemOp.READ, 5) == 0  # mesmo bloco


def test_asking_for_the_stall_does_not_touch_the_cache():
    cache = DataCache(MainMemory(), CacheConfig(miss_penalty=2))
    for _ in range(3):
        cache.stall_for(MemOp.READ, 7)
    assert (cache.hits, cache.misses) == (0, 0)
    assert cache.stall_for(MemOp.READ, 7) == 2


def test_last_access_reports_hit_or_miss_index_tag_and_address():
    cache = DataCache(memory_with({0xA5: 7}), CacheConfig(lines=8, block_size=2))
    cache.read(0xA5)
    assert cache.last_access is not None
    assert cache.last_access.to_dict() == {
        "result": "miss",
        "op": "rd",
        "address": 0xA5,
        "tag": 0x0A,
        "index": 2,
        "offset": 1,
        "way": 0,
        "evicted_tag": None,
        "bypass": False,
    }
    cache.read(0xA4)
    assert cache.last_access.to_dict()["result"] == "hit"


def test_direct_mapping_conflict_evicts_the_old_block():
    cache = DataCache(memory_with({0x010: 1, 0x110: 2}), CacheConfig(lines=8, block_size=2))
    assert cache.split(0x010)[1] == cache.split(0x110)[1] == 0  # mesmo índice, tags diferentes
    cache.read(0x010)
    cache.read(0x110)
    assert cache.last_access is not None and cache.last_access.evicted_tag == 0x01
    cache.read(0x010)  # o bloco antigo foi descartado: falta de novo
    assert (cache.hits, cache.misses) == (0, 3)


def test_two_way_lru_keeps_the_recently_used_block():
    cache = DataCache(
        memory_with({0x000: 1, 0x008: 2, 0x010: 3}),
        CacheConfig(lines=8, block_size=2, mapping="2way"),  # 4 conjuntos de 2 vias
    )
    cache.read(0x000)
    cache.read(0x008)  # conjunto 0 cheio
    cache.read(0x000)  # hit: o bloco 0x008 passa a ser o LRU
    cache.read(0x010)  # miss: substitui o LRU (0x008), não o 0x000
    assert cache.last_access is not None
    assert cache.last_access.evicted_tag == cache.split(0x008)[0]
    assert cache.read(0x000) == 1  # continua na cache
    cache.read(0x008)  # foi descartado
    assert (cache.hits, cache.misses) == (2, 4)


def test_snapshot_marks_the_lru_way_of_each_set():
    cache = DataCache(MainMemory(), CacheConfig(lines=4, block_size=1, mapping="2way"))
    cache.read(0)
    cache.read(2)  # mesmo conjunto (0), outra via
    cache.read(0)
    set_0 = [line for line in cache.snapshot()["lines"] if line["set"] == 0]
    assert [(line["tag"], line["lru"]) for line in set_0] == [(0, False), (1, True)]


def test_disabled_cache_always_misses_and_reads_main_memory():
    cache = DataCache(memory_with({3: 9}), CacheConfig(enabled=False, miss_penalty=4))
    assert cache.read(3) == 9 and cache.read(3) == 9
    assert (cache.hits, cache.misses) == (0, 2)
    assert cache.stall_for(MemOp.READ, 3) == 4
    assert cache.last_access is not None and cache.last_access.to_dict()["bypass"] is True
    assert not any(line["valid"] for line in cache.snapshot()["lines"])


# --- Escrita (D-Cache, write-through) ---------------------------------------------------------


def test_write_hit_updates_the_line_and_main_memory():
    memory = memory_with({20: 1})
    cache = DataCache(memory, CacheConfig())
    cache.read(20)
    cache.write(20, 99)
    assert memory.read(20) == 99  # write-through
    assert cache.read(20) == 99
    assert (cache.hits, cache.misses) == (2, 1)


def test_write_miss_goes_to_memory_without_allocating_the_block():
    memory = MainMemory()
    cache = DataCache(memory, CacheConfig(miss_penalty=3))
    assert cache.stall_for(MemOp.WRITE, 30) == 3
    cache.write(30, 5)
    assert memory.read(30) == 5
    assert cache.last_access is not None and not cache.last_access.hit
    assert cache.stall_for(MemOp.READ, 30) == 3  # no-write-allocate: o bloco não veio


def test_instruction_cache_is_read_only():
    with pytest.raises(TypeError):
        InstructionCache(MainMemory()).write(0, 1)


# --- Sistema de caches ----------------------------------------------------------------------


def test_data_writes_keep_the_instruction_cache_coherent():
    system = CacheSystem(memory_with({0: 0x7001}))
    system.icache.read(0)  # a instrução está na I-Cache
    system.dcache.write(0, 0x7002)  # o programa reescreve essa palavra
    assert system.icache.read(0) == 0x7002
    assert (system.icache.hits, system.icache.misses) == (1, 1)


def test_invalidate_keeps_statistics_and_reset_clears_them():
    cache = DataCache(MainMemory(), CacheConfig(miss_penalty=2))
    cache.read(1)
    assert cache.stall_for(MemOp.READ, 1) == 0
    cache.invalidate()
    assert cache.misses == 1  # estatísticas preservadas
    assert cache.stall_for(MemOp.READ, 1) == 2  # a linha ficou inválida
    cache.reset()
    assert (cache.hits, cache.misses, cache.last_access) == (0, 0, None)


def test_snapshot_is_json_and_complete():
    system = CacheSystem(
        MainMemory(),
        CacheConfig(lines=4, block_size=2),
        CacheConfig(lines=4, block_size=1, mapping="2way"),
    )
    system.icache.read(1)
    data = json.loads(json.dumps(system.snapshot()))
    icache = data["instruction"]
    assert icache["name"] == "I-Cache"
    assert {
        "hits",
        "misses",
        "accesses",
        "hit_rate",
        "last_access",
        "lines",
        "address_bits",
    } <= set(icache)
    assert len(icache["lines"]) == 4
    assert icache["lines"][0] == {
        "set": 0,
        "way": 0,
        "valid": True,
        "tag": 0,
        "data": [0, 0],
        "lru": None,
    }
    assert data["data"]["config"] == {
        "enabled": True,
        "lines": 4,
        "sets": 2,
        "ways": 2,
        "block_size": 1,
        "mapping": "2way",
        "miss_penalty": 0,
    }
