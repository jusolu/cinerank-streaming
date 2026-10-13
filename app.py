import gzip
import json
import os
import re

from flask import Flask, jsonify, request, render_template

import catalog
import recommender

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(BASE_DIR, 'static'), template_folder=os.path.join(BASE_DIR, 'templates'))

IMDB_ID = re.compile(r'^tt\d{7,10}$')


@app.after_request
def add_header(response):
    # O catálogo só muda a cada deploy: respostas da API podem ser cacheadas por alguns minutos.
    if request.path.startswith('/api/recommend'):
        response.headers['Cache-Control'] = 'no-store'
    elif request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'public, max-age=300'
    elif request.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'public, max-age=86400'
    # Compressão das respostas JSON (a fila do Descobrir com atributos passa de 1 MB por página).
    if (response.status_code == 200 and response.mimetype == 'application/json'
            and 'gzip' in request.headers.get('Accept-Encoding', '') and not response.direct_passthrough):
        data = response.get_data()
        if len(data) > 1024:
            response.set_data(gzip.compress(data, compresslevel=6))
            response.headers['Content-Encoding'] = 'gzip'
            response.headers['Vary'] = 'Accept-Encoding'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response


def int_arg(name, default, lo, hi):
    value = request.args.get(name, default, type=int)
    return default if value is None else max(lo, min(hi, value))


@app.route('/')
@app.route('/api/index')
@app.route('/api/index/')
def index():
    return render_template('index.html')


@app.route('/api/movies', methods=['GET'])
def get_movies():
    page = int_arg('page', 1, 1, 10000)
    page_size = int_arg('page_size', 50, 1, 500)
    items, total = catalog.list_movies(
        year=request.args.get('year', type=int),
        decade=request.args.get('decade', type=int),
        genre=request.args.get('genre') or None,
        search=request.args.get('search', '').strip()[:100] or None,
        sort=request.args.get('sort', 'cinescore'),
        min_votes=request.args.get('min_votes', type=int),
        page=page,
        page_size=page_size,
        with_facets=request.args.get('facets') == '1',
    )
    return jsonify({"success": True, "total": total, "page": page, "page_size": page_size, "movies": items})


@app.route('/api/movies/<movie_id>', methods=['GET'])
def get_movie_detail(movie_id):
    movie = catalog.get_movie(movie_id) if IMDB_ID.match(movie_id) else None
    if not movie:
        return jsonify({"success": False, "error": "Filme não encontrado"}), 404
    return jsonify({"success": True, "movie": movie})


def _graph(center_id):
    graph = catalog.build_graph(
        center_id,
        max_neighbors=int_arg('limit', 30, 1, 80),
        min_similarity=int_arg('min_sim', 20, 0, 100),
    )
    if not graph:
        return jsonify({"success": False, "error": "Filme não encontrado"}), 404
    return jsonify({"success": True, "graph": graph})


@app.route('/api/movies/<movie_id>/graph', methods=['GET'])
def get_movie_graph(movie_id):
    if not IMDB_ID.match(movie_id):
        return jsonify({"success": False, "error": "Filme não encontrado"}), 404
    return _graph(movie_id)


@app.route('/api/graph/default', methods=['GET'])
def get_default_graph():
    return _graph(None)


@app.route('/api/recommend', methods=['POST'])
def recommend():
    """Recomendação híbrida. Corpo: {answers: [{id, action}], n, genre}. Sem estado no servidor."""
    if not recommender.available():
        return jsonify({"success": False, "error": "Vetores de recomendação não gerados neste ambiente."}), 503
    body = request.get_json(silent=True) or {}
    answers = body.get('answers') or []
    if not isinstance(answers, list) or len(answers) > 2000:
        return jsonify({"success": False, "error": "Lista de respostas inválida."}), 400
    answers = [a for a in answers if isinstance(a, dict) and IMDB_ID.match(str(a.get('id', '')))]
    n = max(1, min(30, int(body.get('n') or 10)))
    genre = body.get('genre') or None
    variant = body.get('variant') if body.get('variant') in recommender.VARIANTS else 'A'
    movies = recommender.recommend(answers, n=n, genre=genre, variant=variant)
    return jsonify({"success": True, "movies": movies, "variant": variant})


PERSONAS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'personas.json')
_personas = None


@app.route('/api/personas', methods=['GET'])
def get_personas():
    """Personas de teste (ingest/personas.py): nome, traços, filmes-assinatura e curtidas-semente."""
    global _personas
    if _personas is None:
        if not os.path.exists(PERSONAS):
            return jsonify({"success": False, "error": "Personas não geradas neste ambiente."}), 404
        with open(PERSONAS, encoding='utf-8') as f:
            _personas = [{k: p[k] for k in ('id', 'name', 'traits', 'moods', 'signature', 'seed_likes')}
                         for p in json.load(f)['personas']]
    return jsonify({"success": True, "personas": _personas})


EVENTS = {'impression', 'answer', 'open', 'session'}


@app.route('/api/event', methods=['POST'])
def log_event():
    """Eventos do teste A/B, gravados como JSON nos logs do Cloud Run (lidos por ingest/ab_report.py).

    Anônimo: só um ID aleatório gerado no navegador, a variante e o que aconteceu com qual filme."""
    body = request.get_json(silent=True) or {}
    events = body.get('events') if isinstance(body.get('events'), list) else [body]
    sid = str(body.get('sid', ''))[:36]
    variant = body.get('variant') if body.get('variant') in recommender.VARIANTS else None
    if not re.fullmatch(r'[0-9a-f-]{8,36}', sid) or not variant:
        return jsonify({"success": False}), 400
    for e in events[:50]:
        if not isinstance(e, dict) or e.get('type') not in EVENTS:
            continue
        movie = str(e.get('id', ''))
        print(json.dumps({'ab_event': e['type'], 'sid': sid, 'variant': variant,
                          'id': movie if IMDB_ID.match(movie) else None,
                          'action': e.get('action') if e.get('action') in ('right', 'up', 'down', 'left', 'skip') else None,
                          'pos': e.get('pos') if isinstance(e.get('pos'), int) else None,
                          'surface': e.get('surface') if e.get('surface') in ('discover', 'foryou') else None}),
              flush=True)
    return jsonify({"success": True})


@app.route('/api/facets', methods=['GET'])
def get_facets():
    return jsonify({"success": True, **catalog.facets()})


@app.route('/api/stats', methods=['GET'])
def get_stats():
    return jsonify({"success": True, "stats": catalog.stats()})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"[CineRank] Servidor em http://127.0.0.1:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
