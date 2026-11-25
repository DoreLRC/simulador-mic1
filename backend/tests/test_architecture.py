"""Regra de arquitetura: o motor (mic1/) e o microcódigo (microcode/) não dependem da web."""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_PACKAGES = ("fastapi", "starlette", "uvicorn", "pydantic")

# Importa todos os módulos de mic1 num processo limpo e lista os pacotes web carregados.
PROBE = f"""
import importlib, pkgutil, sys
import mic1
for module in pkgutil.walk_packages(mic1.__path__, "mic1."):
    importlib.import_module(module.name)
print(",".join(sorted(n for n in sys.modules if n.split(".")[0] in {WEB_PACKAGES!r})))
"""


def test_engine_does_not_import_web_libraries():
    result = subprocess.run(
        [sys.executable, "-c", PROBE], capture_output=True, text=True, check=True, cwd=PROJECT_ROOT
    )
    assert result.stdout.strip() == ""


def test_microcode_folder_has_no_web_imports():
    files = [path for path in (PROJECT_ROOT / "microcode").rglob("*") if path.is_file()]
    assert files, "a pasta microcode/ deveria ter os microprogramas"
    for path in files:
        text = path.read_text(encoding="utf-8").lower()
        for package in WEB_PACKAGES:
            assert f"import {package}" not in text, path
            assert f"from {package}" not in text, path
