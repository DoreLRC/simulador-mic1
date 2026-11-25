"""API FastAPI: exemplos, microprograma, montagem, passos, HALT, reset, sessões e erros."""

import pytest
from fastapi.testclient import TestClient

from mic1.isa import encode
from server.app import EXAMPLES_DIR, app
from server.schemas import MAX_MISS_PENALTY
from server.simulation import MAX_EVENTS, SessionStore

# Configuração padrão da interface: as duas caches com 16 linhas, blocos de 2 e miss de 4.
UI_CACHE = {"enabled": True, "lines": 16, "block_size": 2, "mapping": "direct", "miss_penalty": 4}
SE_I = (EXAMPLES_DIR / "se_i_menor_igual_n.mac1").read_text(encoding="utf-8")
DEMO = (EXAMPLES_DIR / "cache_laco_soma.mac1").read_text(encoding="utf-8")
FOREVER = "laco: LOCO 1\n      JUMP laco\n"


@pytest.fixture
def client():
    with TestClient(app) as test_client:  # cada teste com o seu cookie de sessão
        yield test_client


def assemble(client: TestClient, source: str, **settings: object) -> dict:
    response = client.post("/api/assemble", json={"source": source, **settings})
    assert response.status_code == 200, response.text
    return response.json()


def step(client: TestClient, granularity: str) -> dict:
    response = client.post("/api/step", json={"granularity": granularity})
    assert response.status_code == 200, response.text
    return response.json()


def clock(state: dict) -> dict:
    return state["snapshot"]["clock"]


# --- Recursos ---------------------------------------------------------------------------------


def test_examples_lists_the_mac1_files_with_their_content(client):
    examples = client.get("/api/examples").json()["examples"]
    assert [item["name"] for item in examples] == [
        "cache_laco_soma.mac1",
        "se_i_menor_igual_n.mac1",
    ]
    assert examples[0]["source"] == DEMO
    assert examples[1]["source"] == SE_I
    assert examples[1]["title"].startswith("Desvio condicional")
    assert all(item["title"] for item in examples)


@pytest.mark.parametrize(("mode", "size", "fetch"), [("original", 79, 0), ("case", 67, 252)])
def test_microprogram_endpoint(client, mode, size, fetch):
    data = client.get("/api/microprogram", params={"mode": mode}).json()
    assert (data["mode"], data["size"], data["fetch_address"]) == (mode, size, fetch)
    assert data["used_bits"] == size * 32
    assert fetch in {step["address"] for step in data["steps"]}
    assert all({"address", "mal", "comment", "word"} <= set(step) for step in data["steps"])


def test_static_interface_is_served(client):
    page = client.get("/")
    assert page.status_code == 200
    for text in ("Montar e Carregar", "Executar até HALT", 'id="diagram"', "Memória principal"):
        assert text in page.text
    script = client.get("/js/main.js")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]  # módulos ES exigem esse tipo


# --- Montagem -----------------------------------------------------------------------------------


def test_assemble_returns_the_initial_state_and_sets_the_session_cookie(client):
    response = client.post("/api/assemble", json={"source": SE_I, "cache": UI_CACHE})
    assert response.status_code == 200
    assert "mic1_sessao" in response.cookies
    state = response.json()

    snapshot = state["snapshot"]
    assert clock(state)["elapsed_subcycles"] == 0
    assert snapshot["registers"]["PC"] == 0 and snapshot["registers"]["SP"] == 4096
    assert state["memory"][0] == encode("LODD", state["program"]["symbols"]["n"])
    assert len(state["memory"]) == 4096
    assert state["source"] == SE_I
    assert state["settings"]["mode"] == "original"
    assert state["settings"]["icache"] == state["settings"]["dcache"]
    assert state["settings"]["icache"]["miss_penalty"] == 4
    assert snapshot["caches"]["instruction"]["config"]["lines"] == 16
    assert (state["halted"], state["limit_reached"]) == (False, False)


def test_assembly_errors_come_back_with_line_numbers(client):
    response = client.post("/api/assemble", json={"source": "LODD x\nFOO 3\n  LOCO 5000\n"})
    assert response.status_code == 422
    body = response.json()
    assert "erro" in body["detail"]
    assert [error["line"] for error in body["errors"]] == [1, 2, 3]
    assert "rótulo não definido" in body["errors"][0]["message"]
    assert "instrução desconhecida" in body["errors"][1]["message"]


def test_a_failed_assembly_keeps_the_program_already_loaded(client):
    assemble(client, SE_I)
    step(client, "micro")
    assert client.post("/api/assemble", json={"source": "FOO"}).status_code == 422
    assert clock(step(client, "micro"))["completed_cycles"] == 2


@pytest.mark.parametrize(
    "settings",
    [
        {"mode": "ijvm"},
        {"cache": {"lines": 6}},
        {"cache": {"mapping": "4way"}},
        {"cache": {"block_size": 0}},
        {"cache": {"miss_penalty": MAX_MISS_PENALTY + 1}},
        {"cache": {"block_size": 3}},  # regra do núcleo: potência de 2
    ],
)
def test_invalid_settings_are_rejected(client, settings):
    response = client.post("/api/assemble", json={"source": SE_I, **settings})
    assert response.status_code == 422


def test_instruction_and_data_caches_can_have_their_own_settings(client):
    state = assemble(
        client,
        SE_I,
        cache=UI_CACHE,
        icache={**UI_CACHE, "lines": 4},
        dcache={**UI_CACHE, "mapping": "2way"},
    )
    assert state["settings"]["icache"]["lines"] == 4
    assert state["settings"]["dcache"]["mapping"] == "2way"
    assert state["settings"]["dcache"]["lines"] == 16


# --- Passos -------------------------------------------------------------------------------------


def test_each_granularity_advances_the_right_amount(client):
    assemble(client, SE_I)
    state = step(client, "subcycle")
    assert (clock(state)["subcycle_name"], clock(state)["completed_cycles"]) == ("Δw", 0)
    state = step(client, "micro")
    assert (clock(state)["subcycle"], clock(state)["completed_cycles"]) == (4, 1)
    state = step(client, "macro")
    assert clock(state)["completed_instructions"] == 1
    assert state["snapshot"]["macro"]["at_boundary"]


def test_step_defaults_to_one_microinstruction(client):
    assemble(client, SE_I)
    response = client.post("/api/step")
    assert response.status_code == 200
    assert clock(response.json())["completed_cycles"] == 1


def test_unknown_granularity_is_rejected(client):
    assemble(client, SE_I)
    assert client.post("/api/step", json={"granularity": "ciclo"}).status_code == 422


@pytest.mark.parametrize(("mode", "cycles"), [("original", 76), ("case", 56)])
def test_halt_reproduces_the_se_i_cycle_counts(client, mode, cycles):
    program = assemble(client, SE_I, mode=mode)["program"]
    state = step(client, "halt")
    assert state["halted"] and state["snapshot"]["halted"]
    assert not state["limit_reached"]
    assert clock(state)["completed_cycles"] == cycles
    assert state["memory"][program["symbols"]["m"]] == 30  # a escrita do STOD já terminou
    assert state["snapshot"]["macro"]["address"] == program["symbols"]["saida"]


def test_halt_shows_the_cache_demo_numbers_with_the_interface_defaults(client):
    program = assemble(client, DEMO, cache=UI_CACHE)["program"]
    with_caches = step(client, "halt")
    caches = with_caches["snapshot"]["caches"]
    assert clock(with_caches)["stall_subcycles"] == 68
    assert (caches["instruction"]["misses"], caches["data"]["misses"]) == (9, 8)
    assert with_caches["memory"][program["symbols"]["soma"]] == 30

    without = client.post("/api/reset", json={"cache": {**UI_CACHE, "enabled": False}}).json()
    assert without["settings"]["icache"]["enabled"] is False
    without = step(client, "halt")
    assert clock(without)["stall_subcycles"] == 1128
    assert without["snapshot"]["caches"]["instruction"]["hits"] == 0
    for state in (with_caches, without):
        assert (clock(state)["completed_cycles"], clock(state)["completed_instructions"]) == (
            1370,
            150,
        )


def test_halt_again_changes_nothing(client):
    assemble(client, SE_I)
    first = step(client, "halt")
    second = step(client, "halt")
    assert clock(second) == clock(first)
    assert second["events_total"] == 0


def test_halt_stops_at_the_cycle_limit_on_programs_without_an_end(client):
    assemble(client, FOREVER)
    state = step(client, "halt")
    assert state["limit_reached"] and not state["halted"]
    assert clock(state)["completed_cycles"] == state["cycle_limit"] == 10_000
    assert len(state["snapshot"]["events"]) == MAX_EVENTS < state["events_total"]


# --- Reset, estado e sessões ---------------------------------------------------------------------


def test_reset_reloads_the_program_and_keeps_the_settings(client):
    program = assemble(client, SE_I, mode="case", cache=UI_CACHE)["program"]
    step(client, "halt")
    state = client.post("/api/reset").json()
    assert state["memory"][program["symbols"]["m"]] == 0  # memória recarregada
    assert clock(state)["completed_cycles"] == 0
    assert state["snapshot"]["caches"]["instruction"]["misses"] == 0
    assert state["settings"]["mode"] == "case"
    assert state["settings"]["icache"]["miss_penalty"] == 4


def test_reset_can_apply_new_settings(client):
    assemble(client, SE_I)
    state = client.post("/api/reset", json={"mode": "case", "cache": UI_CACHE}).json()
    assert state["settings"]["mode"] == "case"
    assert state["settings"]["dcache"]["lines"] == 16
    assert clock(step(client, "halt"))["completed_cycles"] == 56


def test_without_a_program_step_and_reset_answer_409(client):
    assert client.get("/api/state").json() == {"loaded": False}
    for url in ("/api/step", "/api/reset"):
        response = client.post(url, json={})
        assert response.status_code == 409
        assert "nenhum programa" in response.json()["detail"]


def test_state_restores_the_session(client):
    assemble(client, SE_I)
    stepped = step(client, "macro")
    state = client.get("/api/state").json()
    assert state["loaded"]
    assert state["source"] == SE_I
    assert clock(state) == clock(stepped)
    assert state["program"]["symbols"]["saida"] == 8


def test_each_browser_has_its_own_simulation(client):
    with TestClient(app) as other:
        assemble(client, SE_I)
        assemble(other, DEMO)
        step(client, "halt")
        assert clock(step(other, "micro"))["completed_cycles"] == 1
        assert client.get("/api/state").json()["source"] == SE_I


def test_session_store_drops_the_least_recently_used():
    store = SessionStore(limit=2)
    store.put("a", object())  # type: ignore[arg-type]
    store.put("b", object())  # type: ignore[arg-type]
    store.get("a")
    store.put("c", object())  # type: ignore[arg-type]
    assert store.get("b") is None
    assert store.get("a") is not None and store.get("c") is not None
    assert len(store) == 2
