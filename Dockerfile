# Imagem do site para o Cloud Run: app Flask + catálogo SQLite somente leitura.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

COPY requirements-web.txt .
# libgomp1: dependência do LightGBM (ordenação aprendida).
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements-web.txt \
    && useradd --system --create-home web

# --chown evita uma segunda cópia do banco numa camada extra (chown -R duplicaria ~80 MB).
COPY --chown=web app.py catalog.py scoring.py recommender.py ./
COPY --chown=web templates/ templates/
COPY --chown=web static/ static/
COPY --chown=web data/catalog.db data/reco_vectors.npz data/reco_config.json data/reco_features.npz data/ranker.txt data/
USER web

# 1 processo com várias threads: o SQLite é aberto em modo imutável por requisição, sem disputa de escrita.
CMD ["sh", "-c", "exec gunicorn --bind :${PORT} --workers 1 --threads 8 --timeout 30 app:app"]
