import os
import json
import uuid
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, render_template
from movie_api import sync_streaming_releases_api, calculate_cinescore, build_movie_graph, calculate_movie_similarity

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(BASE_DIR, 'static'), template_folder=os.path.join(BASE_DIR, 'templates'))
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
MOVIES_FILE = os.path.join(DATA_DIR, 'movies.json')
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')

def load_movies():
    if not os.path.exists(MOVIES_FILE):
        return []
    with open(MOVIES_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_movies(movies):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(MOVIES_FILE, 'w', encoding='utf-8') as f:
        json.dump(movies, f, ensure_ascii=False, indent=2)

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {"tmdb_api_key": "", "omdb_api_key": ""}
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_config(cfg):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

@app.route('/')
@app.route('/api/index')
@app.route('/api/index/')
def index():
    return render_template('index.html')

@app.route('/api/movies', methods=['GET'])
def get_movies():
    movies = load_movies()
    service = request.args.get('service', 'all').lower()
    sort_by = request.args.get('sort', 'cinescore')
    timeframe = request.args.get('timeframe', 'all')
    search = request.args.get('search', '').strip().lower()

    # Filter by streaming service
    if service != 'all':
        movies = [m for m in movies if m.get('streaming_service', '').lower() == service]

    # Filter by timeframe
    if timeframe.isdigit():
        days = int(timeframe)
        today = datetime.now()
        filtered = []
        for m in movies:
            r_date = m.get('release_date')
            if r_date:
                try:
                    dt = datetime.strptime(r_date[:10], '%Y-%m-%d')
                    if (today - dt).days <= days:
                        filtered.append(m)
                except ValueError:
                    filtered.append(m)
            else:
                filtered.append(m)
        movies = filtered

    # Search filter (smart search across title, original title, director, cast, genres, synopsis)
    if search:
        tokens = search.split()
        movies = [
            m for m in movies
            if all(
                tok in m.get('title', '').lower()
                or tok in m.get('original_title', '').lower()
                or tok in m.get('director', '').lower()
                or any(tok in g.lower() for g in m.get('genres', []))
                or any(tok in c.lower() for c in m.get('cast', []))
                or tok in m.get('synopsis', '').lower()
                for tok in tokens
            )
        ]

    # Sort
    if sort_by == 'cinescore':
        movies.sort(key=lambda x: x.get('scores', {}).get('cinescore', 0) or 0, reverse=True)
    elif sort_by == 'tomatometer':
        movies.sort(key=lambda x: x.get('scores', {}).get('tomatometer') or 0, reverse=True)
    elif sort_by == 'popcornmeter':
        movies.sort(key=lambda x: x.get('scores', {}).get('popcornmeter') or 0, reverse=True)
    elif sort_by == 'imdb':
        movies.sort(key=lambda x: x.get('scores', {}).get('imdb') or 0, reverse=True)
    elif sort_by == 'newest':
        movies.sort(key=lambda x: x.get('release_date', ''), reverse=True)
    elif sort_by == 'votes':
        movies.sort(key=lambda x: int(str(x.get('scores', {}).get('imdb_votes', '0')).replace(',', '').replace('.', '') or 0), reverse=True)

    return jsonify({"success": True, "count": len(movies), "movies": movies})

@app.route('/api/movies/<movie_id>', methods=['GET'])
def get_movie_detail(movie_id):
    movies = load_movies()
    for m in movies:
        if m.get('id') == movie_id:
            return jsonify({"success": True, "movie": m})
    return jsonify({"success": False, "error": "Filme não encontrado"}), 404

@app.route('/api/movies/<movie_id>/graph', methods=['GET'])
def get_movie_graph_route(movie_id):
    movies = load_movies()
    min_sim = request.args.get('min_sim', 20, type=int)
    max_nodes = request.args.get('limit', 12, type=int)
    graph_data = build_movie_graph(movie_id, movies, max_neighbors=max_nodes, min_similarity=min_sim)
    return jsonify({"success": True, "graph": graph_data})

@app.route('/api/graph/default', methods=['GET'])
def get_default_graph():
    movies = load_movies()
    if not movies:
        return jsonify({"success": False, "error": "Nenhum filme cadastrado"}), 404
    top_movie = sorted(movies, key=lambda x: x.get('scores', {}).get('cinescore', 0), reverse=True)[0]
    min_sim = request.args.get('min_sim', 20, type=int)
    max_nodes = request.args.get('limit', 12, type=int)
    graph_data = build_movie_graph(top_movie['id'], movies, max_neighbors=max_nodes, min_similarity=min_sim)
    return jsonify({"success": True, "graph": graph_data})

@app.route('/api/movies/<movie_id>/reviews', methods=['POST'])
def add_review(movie_id):
    data = request.json or {}
    author = data.get('author', '').strip() or "Cinéfilo Anônimo"
    text = data.get('text', '').strip()
    rating = float(data.get('rating', 8.0))

    if not text:
        return jsonify({"success": False, "error": "O comentário não pode ficar vazio."}), 400

    sentiment = "Positivo" if rating >= 7.0 else ("Neutro" if rating >= 5.0 else "Crítico")

    movies = load_movies()
    target_movie = None
    for m in movies:
        if m.get('id') == movie_id:
            target_movie = m
            break

    if not target_movie:
        return jsonify({"success": False, "error": "Filme não encontrado"}), 404

    new_review = {
        "id": f"user_{uuid.uuid4().hex[:8]}",
        "author": author,
        "avatar": f"https://api.dicebear.com/7.x/bottts/svg?seed={author}",
        "rating": rating,
        "source": "Comunidade CineRank",
        "date": datetime.now().strftime("%d de %B de %Y"),
        "sentiment": sentiment,
        "likes": 1,
        "text": text
    }

    if "reviews" not in target_movie:
        target_movie["reviews"] = []
    target_movie["reviews"].insert(0, new_review)

    # Recalculate community rating and CineScore
    ratings = [float(r.get('rating', 7.0)) for r in target_movie["reviews"]]
    avg_comm = sum(ratings) / len(ratings)

    scores = target_movie.get('scores', {})
    new_cinescore = calculate_cinescore(
        tomatometer=scores.get('tomatometer'),
        popcornmeter=scores.get('popcornmeter'),
        imdb_rating=scores.get('imdb'),
        tmdb_rating=avg_comm
    )
    scores['cinescore'] = new_cinescore
    scores['community_rating'] = round(avg_comm, 1)
    target_movie['scores'] = scores

    save_movies(movies)
    return jsonify({"success": True, "review": new_review, "scores": scores})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    movies = load_movies()
    if not movies:
        return jsonify({"success": True, "stats": {}})

    total_movies = len(movies)
    scores = [m.get('scores', {}).get('cinescore', 0) for m in movies if m.get('scores', {}).get('cinescore')]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0
    top_movie = max(movies, key=lambda m: m.get('scores', {}).get('cinescore', 0))

    # Platform counts and averages
    platforms = {}
    for m in movies:
        plat = m.get('streaming_name', 'Outros')
        cinescore = m.get('scores', {}).get('cinescore', 0)
        if plat not in platforms:
            platforms[plat] = []
        platforms[plat].append(cinescore)

    top_platform = max(platforms.items(), key=lambda x: sum(x[1])/len(x[1]))[0] if platforms else "Netflix"
    total_reviews = sum(len(m.get('reviews', [])) for m in movies)

    return jsonify({
        "success": True,
        "stats": {
            "total_movies": total_movies,
            "avg_score": avg_score,
            "top_movie": {
                "title": top_movie.get('title'),
                "score": top_movie.get('scores', {}).get('cinescore'),
                "streaming": top_movie.get('streaming_name')
            },
            "top_platform": top_platform,
            "total_reviews": total_reviews
        }
    })

@app.route('/api/sync', methods=['POST'])
def sync_scores():
    """
    Live API Synchronization from TMDB & OMDb for recent streaming releases.
    """
    try:
        updated_movies = sync_streaming_releases_api(days_back=35)
        if updated_movies:
            save_movies(updated_movies)
            return jsonify({"success": True, "count": len(updated_movies)})
        return jsonify({"success": False, "error": "Nenhum filme retornado pelas APIs."}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/config', methods=['GET', 'POST'])
def config():
    if request.method == 'POST':
        cfg = request.json or {}
        save_config(cfg)
        return jsonify({"success": True, "config": cfg})
    return jsonify({"success": True, "config": load_config()})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"[CineRank] Server iniciado em http://127.0.0.1:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
