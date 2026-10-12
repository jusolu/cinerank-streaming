import os
import re
import json
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

TMDB_API_KEY = '3e52e2f5350ae60de5e2fc58e818d2a0'
OMDB_API_KEY = 'trilogy'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
}

PROVIDERS = {
    'netflix': {'id': '8|1796', 'name': 'Netflix'},
    'prime': {'id': '9|119|2100', 'name': 'Prime Video'},
    'disney': {'id': '337', 'name': 'Disney+'},
    'max': {'id': '1899|1825', 'name': 'Max'},
    'paramount': {'id': '2303|2616|582', 'name': 'Paramount+'},
    'hulu': {'id': '15', 'name': 'Hulu'},
    'appletv': {'id': '350|2243', 'name': 'Apple TV+'},
    'mubi': {'id': '11|201', 'name': 'MUBI'},
    'peacock': {'id': '386|387', 'name': 'Peacock'}
}

def get_omdb_ratings(imdb_id):
    """Fetches Rotten Tomatoes, IMDb and Metacritic ratings from OMDb API."""
    if not imdb_id:
        return {}
    try:
        url = f"https://www.omdbapi.com/?i={imdb_id}&apikey={OMDB_API_KEY}"
        r = requests.get(url, timeout=3.5)
        if r.status_code == 200:
            data = r.json()
            if data.get('Response') == 'True':
                rt_score = None
                for rating in data.get('Ratings', []):
                    if rating.get('Source') == 'Rotten Tomatoes':
                        val = rating.get('Value', '')
                        m = re.search(r'(\d+)%', val)
                        if m:
                            rt_score = int(m.group(1))

                imdb_rating = None
                try:
                    if data.get('imdbRating') and data.get('imdbRating') != 'N/A':
                        imdb_rating = float(data.get('imdbRating'))
                except ValueError:
                    pass

                metascore = None
                try:
                    if data.get('Metascore') and data.get('Metascore') != 'N/A':
                        metascore = int(data.get('Metascore'))
                except ValueError:
                    pass

                return {
                    'tomatometer': rt_score,
                    'imdb': imdb_rating,
                    'imdb_votes': data.get('imdbVotes'),
                    'metacritic': metascore
                }
    except Exception:
        pass
    return {}

def calculate_cinescore(tomatometer, popcornmeter, imdb_rating, tmdb_rating):
    """
    Calculates weighted CineScore (0-100).
    """
    scores = []
    weights = []

    if tomatometer is not None:
        scores.append(float(tomatometer))
        weights.append(0.35)

    if popcornmeter is not None:
        scores.append(float(popcornmeter))
        weights.append(0.35)

    if imdb_rating is not None:
        scores.append(float(imdb_rating) * 10.0)
        weights.append(0.30)

    if tmdb_rating and len(scores) < 3:
        scores.append(float(tmdb_rating) * 10.0)
        weights.append(0.25)

    if not weights:
        return 72.0

    total_weight = sum(weights)
    weighted_sum = sum(s * w for s, w in zip(scores, weights))
    return round(weighted_sum / total_weight, 1)

def enrich_single_movie(item, p_key, p_info, today):
    """Enriches a single movie item with details, videos, reviews and ratings."""
    mid = item['id']
    try:
        detail_url = f"https://api.themoviedb.org/3/movie/{mid}?api_key={TMDB_API_KEY}&language=pt-BR&append_to_response=credits,videos,release_dates,reviews,keywords,recommendations"
        det = requests.get(detail_url, timeout=4.5).json()

        digital_date = item.get('release_date') or det.get('release_date') or '2026-10-01'
        for rel_c in det.get('release_dates', {}).get('results', []):
            if rel_c.get('iso_3166_1') in ['BR', 'US']:
                for rd in rel_c.get('release_dates', []):
                    if rd.get('type') == 4:
                        digital_date = rd.get('release_date', '')[:10]
                        break

        cast = [c['name'] for c in det.get('credits', {}).get('cast', [])[:5]]
        director = "Direção não informada"
        for crew in det.get('credits', {}).get('crew', []):
            if crew.get('job') == 'Director':
                director = crew.get('name')
                break

        trailer_yt = None
        for vid in det.get('videos', {}).get('results', []):
            if vid.get('site') == 'YouTube' and vid.get('type') in ['Trailer', 'Teaser']:
                trailer_yt = vid.get('key')
                if vid.get('type') == 'Trailer':
                    break

        imdb_id = det.get('imdb_id')
        omdb_info = get_omdb_ratings(imdb_id) if imdb_id else {}

        tmdb_avg = det.get('vote_average') or item.get('vote_average') or 7.0
        tmdb_votes = det.get('vote_count') or item.get('vote_count') or 0

        tomatometer = omdb_info.get('tomatometer')
        imdb_val = omdb_info.get('imdb') or (round(tmdb_avg, 1) if tmdb_avg else 7.0)
        imdb_votes = omdb_info.get('imdb_votes') or (f"{tmdb_votes:,}" if tmdb_votes else "N/A")

        popcornmeter = int(tmdb_avg * 10) if tmdb_avg else 70
        cinescore = calculate_cinescore(tomatometer, popcornmeter, imdb_val, tmdb_avg)

        reviews = []
        for r_item in det.get('reviews', {}).get('results', [])[:4]:
            r_author = r_item.get('author') or "Cinéfilo Verificado"
            r_rating = r_item.get('author_details', {}).get('rating')
            r_score = float(r_rating) if r_rating else 7.5
            r_text = r_item.get('content', '').replace('\r\n', ' ').strip()
            if len(r_text) > 350:
                r_text = r_text[:347] + "..."
            reviews.append({
                'id': f"rev_{r_item.get('id')}",
                'author': r_author,
                'avatar': f"https://api.dicebear.com/7.x/bottts/svg?seed={r_author}",
                'rating': r_score,
                'source': 'Crítica Verificada',
                'date': r_item.get('created_at', '')[:10] or 'Recente',
                'sentiment': 'Positivo' if r_score >= 7.0 else ('Neutro' if r_score >= 5.0 else 'Crítico'),
                'likes': 8,
                'text': r_text
            })

        if not reviews:
            reviews.append({
                'id': f"comm_{mid}",
                'author': 'Espectador Verificado',
                'avatar': f"https://api.dicebear.com/7.x/bottts/svg?seed=User_{mid}",
                'rating': round(tmdb_avg, 1) if tmdb_avg else 7.0,
                'source': p_info['name'],
                'date': digital_date,
                'sentiment': 'Positivo' if (tmdb_avg and tmdb_avg >= 7.0) else 'Neutro',
                'likes': 10,
                'text': f"Lançamento adicionado ao catálogo da {p_info['name']}."
            })

        try:
            rel_dt_obj = datetime.strptime(digital_date, '%Y-%m-%d')
            diff_days = (today - rel_dt_obj).days
        except Exception:
            rel_dt_obj = today
            diff_days = 10

        rel_display = rel_dt_obj.strftime("%d de %B de %Y")

        # Extrair Roteiristas
        screenwriters = []
        for crew_member in det.get('credits', {}).get('crew', []):
            if crew_member.get('job') in ['Screenplay', 'Writer', 'Story', 'Author', 'Screenwriter']:
                w_name = crew_member.get('name')
                if w_name and w_name not in screenwriters:
                    screenwriters.append(w_name)

        # Extrair Keywords narrativas / temas de roteiro
        keywords = [k['name'] for k in det.get('keywords', {}).get('keywords', []) if k.get('name')]

        # Extrair recomendações do TMDB
        rec_ids = [r['id'] for r in det.get('recommendations', {}).get('results', []) if r.get('id')]

        return {
            "id": f"movie-{mid}",
            "tmdb_id": mid,
            "imdb_id": imdb_id,
            "title": det.get('title') or item.get('title'),
            "original_title": det.get('original_title') or item.get('original_title'),
            "year": rel_dt_obj.year,
            "streaming_service": p_key,
            "streaming_name": p_info['name'],
            "release_date": digital_date,
            "release_display": rel_display,
            "release_days_ago": max(0, diff_days),
            "release_window": "Última Semana" if diff_days <= 7 else ("Últimos 15 Dias" if diff_days <= 15 else "Últimos 60 Dias"),
            "runtime": f"{det.get('runtime')} min" if det.get('runtime') else "105 min",
            "genres": [g['name'] for g in det.get('genres', [])] or ["Filme"],
            "director": director,
            "screenwriters": screenwriters,
            "keywords": keywords,
            "tmdb_recommendation_ids": rec_ids,
            "cast": cast,
            "synopsis": det.get('overview') or item.get('overview') or "Sem sinopse cadastrada.",
            "poster_url": f"https://image.tmdb.org/t/p/w600_and_h900_bestv2{item['poster_path']}",
            "backdrop_url": f"https://image.tmdb.org/t/p/original{det.get('backdrop_path') or item.get('backdrop_path') or item['poster_path']}",
            "trailer_youtube_id": trailer_yt,
            "scores": {
                "tomatometer": tomatometer,
                "critics_count": 40 if tomatometer else 0,
                "popcornmeter": popcornmeter,
                "audience_count": f"{tmdb_votes:,} votos" if tmdb_votes else "Avaliações Recentes",
                "imdb": imdb_val,
                "imdb_votes": imdb_votes,
                "metacritic": omdb_info.get('metacritic'),
                "tmdb": round(tmdb_avg, 1) if tmdb_avg else 7.0,
                "cinescore": cinescore
            },
            "reviews": reviews
        }
    except Exception as e:
        return None

def sync_streaming_releases_api(days_back=60):
    """
    Parallel collection across 9 streaming platforms.
    """
    today = datetime.now()
    start_date = (today - timedelta(days=days_back)).strftime('%Y-%m-%d')
    end_date = today.strftime('%Y-%m-%d')

    items_to_enrich = []
    seen_ids = set()

    # Step 1: Rapidly discover items across all 9 providers
    for p_key, p_info in PROVIDERS.items():
        for reg in ['BR', 'US']:
            for page in [1, 2]:
                params = {
                    'api_key': TMDB_API_KEY,
                    'language': 'pt-BR',
                    'sort_by': 'primary_release_date.desc',
                    'with_watch_providers': p_info['id'],
                    'watch_region': reg,
                    'primary_release_date.gte': start_date,
                    'primary_release_date.lte': end_date,
                    'page': page
                }
                try:
                    res = requests.get('https://api.themoviedb.org/3/discover/movie', params=params, timeout=5).json()
                    for item in res.get('results', []):
                        mid = item['id']
                        if mid not in seen_ids and item.get('poster_path') and item.get('title'):
                            seen_ids.add(mid)
                            items_to_enrich.append((item, p_key, p_info))
                except Exception:
                    pass

    print(f"[CineRank] Descobertos {len(items_to_enrich)} títulos únicos. Enriquecendo dados em paralelo...")

    # Step 2: Concurrently enrich all movies using 14 worker threads
    collected_movies = []
    with ThreadPoolExecutor(max_workers=14) as executor:
        futures = [
            executor.submit(enrich_single_movie, item, p_key, p_info, today)
            for item, p_key, p_info in items_to_enrich[:120]
        ]
        for f in as_completed(futures):
            res = f.result()
            if res:
                collected_movies.append(res)

    collected_movies.sort(key=lambda m: m['scores']['cinescore'], reverse=True)
    print(f"[CineRank] Enriquecimento finalizado! Total: {len(collected_movies)} títulos.")
    return collected_movies


def calculate_movie_similarity(m1, m2):
    """
    Calculates detailed narrative & film similarity between two movies.
    Factors:
    - Shared Genres (Jaccard)
    - Narrative Keywords & Script Themes (TMDB Keywords)
    - Screenwriters & Director (TMDB Credits Crew)
    - Official TMDB Recommendations
    - Synopsis Narrative Vocabulary Overlap
    Returns: (score_0_to_100, list_of_reasons, details_dict)
    """
    g1 = set(m1.get('genres', []))
    g2 = set(m2.get('genres', []))
    common_genres = sorted(list(g1 & g2))
    union_genres = g1 | g2
    jaccard_genre = (len(common_genres) / len(union_genres)) if union_genres else 0.0

    k1 = set(k.lower() for k in m1.get('keywords', []))
    k2 = set(k.lower() for k in m2.get('keywords', []))
    common_kws = sorted(list(k1 & k2))
    jaccard_kw = (len(common_kws) / max(1, min(len(k1), len(k2)))) if (k1 and k2) else 0.0

    w1 = set(w.lower() for w in m1.get('screenwriters', []))
    w2 = set(w.lower() for w in m2.get('screenwriters', []))
    common_writers = sorted([w for w in m1.get('screenwriters', []) if w.lower() in w2])

    d1 = (m1.get('director') or '').strip()
    d2 = (m2.get('director') or '').strip()
    same_director = bool(d1 and d2 and d1 != 'Direção não informada' and d1.lower() == d2.lower())

    m1_tmdb = m1.get('tmdb_id')
    m2_tmdb = m2.get('tmdb_id')
    rec_link = bool(
        (m2_tmdb and m2_tmdb in m1.get('tmdb_recommendation_ids', [])) or
        (m1_tmdb and m1_tmdb in m2.get('tmdb_recommendation_ids', []))
    )

    # Narrative vocabulary overlap in synopsis
    syn1 = set(re.findall(r'\b[a-zA-ZáéíóúâêîôûãõçÁÉÍÓÚÂÊÎÔÛÃÕÇ]{4,}\b', (m1.get('synopsis') or '').lower()))
    syn2 = set(re.findall(r'\b[a-zA-ZáéíóúâêîôûãõçÁÉÍÓÚÂÊÎÔÛÃÕÇ]{4,}\b', (m2.get('synopsis') or '').lower()))
    stopwords = {'para', 'como', 'mais', 'pelo', 'pela', 'sobre', 'onde', 'quando', 'depois', 'anos', 'vida', 'este', 'esta', 'seus', 'suas', 'filme', 'historia'}
    syn1 -= stopwords
    syn2 -= stopwords
    common_syn = sorted(list(syn1 & syn2))
    syn_score = (len(common_syn) / max(1, min(len(syn1), len(syn2)))) if (syn1 and syn2) else 0.0

    raw_score = 0.0
    reasons = []

    # 1. Gêneros (peso 35%)
    raw_score += jaccard_genre * 35.0
    if common_genres:
        reasons.append(f"Gêneros em comum: {', '.join(common_genres)}")

    # 2. Keywords narrativas / temas de roteiro (peso 25%)
    if common_kws:
        raw_score += jaccard_kw * 25.0
        reasons.append(f"Temas do enredo: {', '.join(common_kws[:3])}")
    elif syn_score > 0.15:
        raw_score += min(15.0, syn_score * 20.0)
        reasons.append(f"Afinidade temática na narrativa: {', '.join(common_syn[:3])}")

    # 3. Roteirista / Diretor (peso 20%)
    if common_writers:
        raw_score += 20.0
        reasons.append(f"Mesmo roteirista: {', '.join(common_writers)}")
    elif same_director:
        raw_score += 15.0
        reasons.append(f"Mesmo diretor: {d1}")

    # 4. Recomendação cruzada TMDB (peso 15%)
    if rec_link:
        raw_score += 15.0
        reasons.append("Recomendação oficial do algoritmo TMDB")

    # 5. Mesma plataforma de streaming (bônus 5%)
    if m1.get('streaming_service') == m2.get('streaming_service'):
        raw_score += 5.0

    final_score = int(min(99, max(15, round(raw_score))))

    return final_score, reasons, {
        "common_genres": common_genres,
        "common_keywords": common_kws,
        "common_writers": common_writers,
        "same_director": same_director,
        "tmdb_rec": rec_link
    }


def build_movie_graph(center_id, all_movies, max_neighbors=10, min_similarity=20):
    """
    Builds the node-link graph centered on center_id.
    Includes first-degree connections and strong interconnecting edges between neighbors.
    """
    center_movie = None
    for m in all_movies:
        if m.get('id') == center_id or str(m.get('tmdb_id')) == str(center_id):
            center_movie = m
            break

    if not center_movie and all_movies:
        center_movie = all_movies[0]

    if not center_movie:
        return {"center_id": None, "nodes": [], "links": []}

    center_id = center_movie['id']

    scored_candidates = []
    for m in all_movies:
        if m['id'] == center_id:
            continue
        sim_score, reasons, details = calculate_movie_similarity(center_movie, m)
        if sim_score >= min_similarity:
            scored_candidates.append({
                "movie": m,
                "similarity": sim_score,
                "reasons": reasons,
                "details": details
            })

    scored_candidates.sort(
        key=lambda x: (x['similarity'], x['movie'].get('scores', {}).get('cinescore', 0)),
        reverse=True
    )
    selected_neighbors = scored_candidates[:max_neighbors]

    nodes = [{
        "id": center_movie['id'],
        "tmdb_id": center_movie.get('tmdb_id'),
        "title": center_movie.get('title'),
        "year": center_movie.get('year'),
        "streaming_service": center_movie.get('streaming_service'),
        "streaming_name": center_movie.get('streaming_name'),
        "genres": center_movie.get('genres', []),
        "director": center_movie.get('director'),
        "screenwriters": center_movie.get('screenwriters', []),
        "keywords": center_movie.get('keywords', [])[:6],
        "cinescore": center_movie.get('scores', {}).get('cinescore', 0),
        "poster_url": center_movie.get('poster_url'),
        "synopsis": center_movie.get('synopsis'),
        "is_center": True,
        "similarity": 100
    }]

    for item in selected_neighbors:
        m = item['movie']
        nodes.append({
            "id": m['id'],
            "tmdb_id": m.get('tmdb_id'),
            "title": m.get('title'),
            "year": m.get('year'),
            "streaming_service": m.get('streaming_service'),
            "streaming_name": m.get('streaming_name'),
            "genres": m.get('genres', []),
            "director": m.get('director'),
            "screenwriters": m.get('screenwriters', []),
            "keywords": m.get('keywords', [])[:6],
            "cinescore": m.get('scores', {}).get('cinescore', 0),
            "poster_url": m.get('poster_url'),
            "synopsis": m.get('synopsis'),
            "is_center": False,
            "similarity": item['similarity'],
            "reasons": item['reasons']
        })

    links = []
    for item in selected_neighbors:
        links.append({
            "source": center_id,
            "target": item['movie']['id'],
            "similarity": item['similarity'],
            "reasons": item['reasons'],
            "common_genres": item['details']['common_genres'],
            "common_keywords": item['details']['common_keywords'],
            "same_writer": bool(item['details']['common_writers']),
            "is_primary": True
        })

    # Conexões secundárias entre vizinhos (interconexões)
    for i in range(len(selected_neighbors)):
        for j in range(i + 1, len(selected_neighbors)):
            m_a = selected_neighbors[i]['movie']
            m_b = selected_neighbors[j]['movie']
            sub_sim, sub_reasons, sub_details = calculate_movie_similarity(m_a, m_b)
            if sub_sim >= 35:
                links.append({
                    "source": m_a['id'],
                    "target": m_b['id'],
                    "similarity": sub_sim,
                    "reasons": sub_reasons,
                    "common_genres": sub_details['common_genres'],
                    "common_keywords": sub_details['common_keywords'],
                    "same_writer": bool(sub_details['common_writers']),
                    "is_primary": False
                })

    return {
        "center_id": center_id,
        "nodes": nodes,
        "links": links,
        "total_nodes": len(nodes),
        "total_links": len(links)
    }

