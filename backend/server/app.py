"""Aplicação FastAPI do Simulador MIC-1.

Execução local:   python -m uvicorn server.app:app --port 8000
Documentação:     http://localhost:8000/docs

Rotas da simulação (cada navegador tem a sua CPU, guardada pelo cookie ``mic1_sessao``):

* ``GET  /api/examples``: programas ``.mac1`` da pasta ``programs/``;
* ``GET  /api/microprogram``: o microprograma MAL de um modo de decodificação;
* ``POST /api/assemble``: monta o fonte, carrega na CPU e devolve o estado inicial;
* ``POST /api/step``: avança um subciclo, uma micro, uma macro ou até o HALT;
* ``POST /api/reset``: reinicia o último programa (podendo trocar a configuração);
* ``GET  /api/state``: estado atual da sessão (usado quando a página é recarregada).
"""

import mimetypes
import platform
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import mic1
from mic1.assembler.mac1 import AssemblyError
from mic1.assembler.mal import load_microprogram
from mic1.cache import CacheConfig
from mic1.control import DecodeMode
from server.schemas import (
    AssembleRequest,
    CacheSettings,
    MachineSettings,
    ResetRequest,
    StepRequest,
)
from server.simulation import SessionStore, Settings, Simulation

# O registro do Windows às vezes associa .js a text/plain, e o navegador recusa módulos assim.
mimetypes.add_type("text/javascript", ".js")

# backend/server/app.py -> a raiz do repositório fica dois níveis acima de server/.
ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "frontend"
EXAMPLES_DIR = ROOT / "programs"
SESSION_COOKIE = "mic1_sessao"

app = FastAPI(
    title="Simulador MIC-1",
    version=mic1.__version__,
    description="API do simulador educacional da microarquitetura MIC-1 (Tanenbaum).",
)
sessions = SessionStore()


class ApiError(Exception):
    """Erro com mensagem em português e, nos erros de montagem, a lista por linha."""

    def __init__(self, status: int, detail: str, errors: list[dict[str, object]] | None = None):
        super().__init__(detail)
        self.status, self.detail, self.errors = status, detail, errors


@app.exception_handler(ApiError)
async def _api_error(_: Request, error: ApiError) -> JSONResponse:
    content: dict[str, object] = {"detail": error.detail}
    if error.errors is not None:
        content["errors"] = error.errors
    return JSONResponse(content, status_code=error.status)


# Sistema


@app.get("/api/health", tags=["sistema"])
def health() -> dict[str, str]:
    """Indica se o servidor está no ar (usado pelo healthcheck do Docker e pela página)."""
    return {
        "status": "ok",
        "version": mic1.__version__,
        "python": platform.python_version(),
    }


# Recursos


@app.get("/api/examples", tags=["recursos"])
def examples() -> dict[str, object]:
    """Lista os programas de ``programs/`` com o conteúdo de cada um."""
    items = []
    for path in sorted(EXAMPLES_DIR.glob("*.mac1")):
        source = path.read_text(encoding="utf-8")
        items.append({"name": path.name, "title": _title(source), "source": source})
    return {"examples": items}


@app.get("/api/microprogram", tags=["recursos"])
def microprogram(mode: DecodeMode = DecodeMode.ORIGINAL) -> dict[str, object]:
    """As microinstruções do modo pedido, com o texto MAL e a palavra de 32 bits."""
    store = load_microprogram(mode)
    return {
        "mode": mode.value,
        "fetch_address": mode.fetch_address,
        "size": len(store),
        "used_bits": store.used_bits,
        "steps": [
            {
                "address": step.address,
                "mal": step.source,
                "comment": step.comment,
                "word": step.instruction.encode(),
            }
            for step in store
        ],
    }


# Simulação


@app.post("/api/assemble", tags=["simulação"])
def assemble(body: AssembleRequest, request: Request) -> JSONResponse:
    """Monta o fonte MAC-1, carrega na CPU e devolve o estado inicial (ou os erros por linha)."""
    try:
        simulation = Simulation(body.source, _settings(body))
    except AssemblyError as error:
        errors = [{"line": item.line, "message": item.message} for item in error.errors]
        raise ApiError(422, f"o programa tem {len(errors)} erro(s) de montagem", errors) from None

    session_id = request.cookies.get(SESSION_COOKIE)
    if sessions.get(session_id) is None:
        session_id = SessionStore.new_id()
    assert session_id is not None
    sessions.put(session_id, simulation)
    response = JSONResponse(simulation.state(include_program=True))
    response.set_cookie(SESSION_COOKIE, session_id, httponly=True, samesite="lax")
    return response


@app.post("/api/step", tags=["simulação"])
def step(request: Request, body: StepRequest | None = None) -> JSONResponse:
    """Avança a CPU: ``subcycle``, ``micro``, ``macro`` ou ``halt`` (até 10.000 ciclos)."""
    simulation = _session(request)
    granularity = (body or StepRequest()).granularity
    with simulation.lock:
        try:
            return JSONResponse(simulation.step(granularity))
        except RuntimeError as error:  # microprograma que não volta à busca
            raise ApiError(409, str(error)) from None


@app.post("/api/reset", tags=["simulação"])
def reset(request: Request, body: ResetRequest | None = None) -> JSONResponse:
    """Recarrega o último programa e zera a CPU; campos enviados trocam a configuração."""
    session_id = request.cookies.get(SESSION_COOKIE)
    current = _session(request)
    simulation = current.restarted(_merge(current.settings, body or ResetRequest()))
    assert session_id is not None
    sessions.put(session_id, simulation)
    return JSONResponse(simulation.state(include_program=True))


@app.get("/api/state", tags=["simulação"])
def state(request: Request) -> JSONResponse:
    """Estado atual da sessão, com o programa e o fonte (usado ao recarregar a página).

    Sem sessão, devolve ``{"loaded": false}``. Não é erro: a página só não montou nada ainda.
    """
    simulation = sessions.get(request.cookies.get(SESSION_COOKIE))
    if simulation is None:
        return JSONResponse({"loaded": False})
    return JSONResponse({"loaded": True, **simulation.state(include_program=True)})


# Auxiliares


def _session(request: Request) -> Simulation:
    simulation = sessions.get(request.cookies.get(SESSION_COOKIE))
    if simulation is None:
        raise ApiError(409, "nenhum programa carregado: use POST /api/assemble primeiro")
    return simulation


def _config(settings: CacheSettings) -> CacheConfig:
    try:
        return settings.to_config()
    except ValueError as error:  # regras do núcleo (potência de 2, tamanho máximo etc.)
        raise ApiError(422, f"configuração de cache inválida: {error}") from None


def _settings(body: MachineSettings) -> Settings:
    return Settings(
        mode=body.mode,
        icache=_config(body.icache or body.cache),
        dcache=_config(body.dcache or body.cache),
    )


def _merge(current: Settings, body: ResetRequest) -> Settings:
    icache, dcache = body.icache or body.cache, body.dcache or body.cache
    return Settings(
        mode=body.mode or current.mode,
        icache=current.icache if icache is None else _config(icache),
        dcache=current.dcache if dcache is None else _config(dcache),
    )


def _title(source: str) -> str:
    """Primeira linha de comentário com texto (o cabeçalho dos exemplos)."""
    for line in source.splitlines():
        text = line.strip()
        if not text:
            continue
        if not text.startswith((";", "//")):
            break
        text = text.lstrip(";/").strip().strip("=-").strip()
        if text:
            return text
    return ""


# Montado por último: atende tudo que não foi capturado pelas rotas /api e /docs acima.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
