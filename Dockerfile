# Imagem do Simulador MIC-1: API FastAPI (backend/) + interface estática (frontend/).
# A porta vem da variável PORT (padrão 8000). Localmente fica na 8000; serviços como
# Render, Railway e Koyeb definem PORT sozinhos e o uvicorn passa a ouvir nela.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Instala as dependências antes de copiar o código: assim, mudar só o código
# reaproveita a camada do pip no próximo build.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

# Só o que a aplicação usa em execução (os testes e a documentação ficam de fora).
COPY backend/mic1 backend/mic1
COPY backend/microcode backend/microcode
COPY backend/server backend/server
COPY frontend frontend
COPY programs programs

RUN useradd --create-home --uid 1000 appuser
USER appuser

# O uvicorn roda de dentro de backend/ para os imports "mic1" e "server" funcionarem.
WORKDIR /app/backend
EXPOSE 8000
# Forma shell para o ${PORT:-8000} ser expandido; o exec deixa o uvicorn como processo
# principal, recebendo direto o sinal de parada do container.
CMD ["sh", "-c", "exec uvicorn server.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
