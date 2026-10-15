"""
Acesso ao catálogo publicado (data/catalog.db, gerado por `python -m ingest.export`).

O banco é aberto em modo somente leitura e imutável, o que funciona no
filesystem read-only da Vercel e dispensa locks.
"""
import json
import os
import sqlite3
import unicodedata
from contextlib import closing

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('CINERANK_CATALOG', os.path.join(BASE_DIR, 'data', 'catalog.db'))

# Gêneros do IMDb (chave canônica no banco) → rótulo em português.
GENRES_PT = {
    'Action': 'Ação', 'Adventure': 'Aventura', 'Animation': 'Animação', 'Biography': 'Biografia',
    'Comedy': 'Comédia', 'Crime': 'Crime', 'Documentary': 'Documentário', 'Drama': 'Drama',
    'Family': 'Família', 'Fantasy': 'Fantasia', 'Film-Noir': 'Noir', 'Game-Show': 'Game show',
    'History': 'História', 'Horror': 'Terror', 'Music': 'Música', 'Musical': 'Musical',
    'Mystery': 'Mistério', 'News': 'Jornalismo', 'Reality-TV': 'Reality', 'Romance': 'Romance',
    'Sci-Fi': 'Ficção científica', 'Sport': 'Esporte', 'Talk-Show': 'Talk show',
    'Thriller': 'Suspense', 'War': 'Guerra', 'Western': 'Faroeste',
}


def genre_pt(genre):
    return GENRES_PT.get(genre, genre)


def _translate(movie):
    if movie.get('genres'):
        movie['genres'] = ', '.join(genre_pt(g) for g in movie['genres'].split(', '))
    return movie


SORTS = {
    'cinescore': 'rank ASC',
    'imdb': 'imdb_rating DESC, imdb_votes DESC',
    'votes': 'imdb_votes DESC',
    'newest': 'year DESC, rank ASC',
}

LIST_COLUMNS = ('imdb_id, title, original_title, year, runtime, genres, directors, '
                'imdb_rating, imdb_votes, cinescore, low_confidence, rank, '
                'cast_names AS cast, synopsis, synopsis_lang, poster_url, media_source, source_url, trailer_id')


def connect():
    conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro&immutable=1', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params)]


def _filters(year=None, genre=None, search=None, min_votes=None, decade=None):
    where, params = [], []
    if decade:
        where.append('year BETWEEN ? AND ?')
        params += [decade, decade + 9]
    if year:
        where.append('year = ?')
        params.append(year)
    if genre:
        where.append('imdb_id IN (SELECT imdb_id FROM movie_genres WHERE genre = ?)')
        params.append(genre)
    if min_votes:
        where.append('imdb_votes >= ?')
        params.append(min_votes)
    for token in (search or '').lower().split():
        like = f'%{token}%'
        where.append("""(
            lower(title) LIKE ? OR lower(coalesce(original_title, '')) LIKE ? OR imdb_id = ?
            OR imdb_id IN (SELECT mp.imdb_id FROM movie_people mp JOIN people p USING (person_id)
                           WHERE lower(p.name) LIKE ?)
        )""")
        params += [like, like, token, like]
    return (' WHERE ' + ' AND '.join(where)) if where else '', params


def _with_facets(movie):
    raw = movie.pop('facets', None)
    movie['facets'] = json.loads(raw) if raw else {}
    return movie


def list_movies(year=None, genre=None, search=None, sort='cinescore', min_votes=None, page=1, page_size=50,
                decade=None, with_facets=False):
    where, params = _filters(year, genre, search, min_votes, decade)
    order = SORTS.get(sort, SORTS['cinescore'])
    offset = (page - 1) * page_size
    columns = LIST_COLUMNS + (', facets' if with_facets else '')
    with closing(connect()) as conn:
        total = conn.execute(f'SELECT COUNT(*) FROM movies{where}', params).fetchone()[0]
        items = _rows(conn, f'SELECT {columns} FROM movies{where} ORDER BY {order} LIMIT ? OFFSET ?',
                      params + [page_size, offset])
    items = [_translate(m) for m in items]
    return ([_with_facets(m) for m in items] if with_facets else items), total


def movies_by_ids(ids):
    """Vários filmes de uma vez (colunas da listagem + atributos), na ordem pedida."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []
    marks = ','.join('?' * len(ids))
    with closing(connect()) as conn:
        rows = {r['imdb_id']: _with_facets(_translate(dict(r))) for r in conn.execute(
            f'SELECT {LIST_COLUMNS}, facets FROM movies WHERE imdb_id IN ({marks})', ids)}
    return [rows[i] for i in ids if i in rows]


def _credits(conn, imdb_id):
    credits = {'director': [], 'writer': [], 'cast': []}
    for r in conn.execute("""
        SELECT mp.role, p.name FROM movie_people mp JOIN people p USING (person_id)
        WHERE mp.imdb_id = ? ORDER BY mp.role, mp.ord
    """, (imdb_id,)):
        credits[r['role']].append(r['name'])
    return credits


def get_movie(imdb_id):
    with closing(connect()) as conn:
        row = conn.execute(f'SELECT {LIST_COLUMNS}, facets FROM movies WHERE imdb_id = ?', (imdb_id,)).fetchone()
        if not row:
            return None
        movie = _with_facets(_translate(dict(row)))
        credits = _credits(conn, imdb_id)
    movie.update(directors_list=credits['director'], writers=credits['writer'], cast=credits['cast'])
    return movie


def get_movies_brief(ids):
    """Dados mínimos (duração, ano, gêneros) de vários filmes de uma vez, para as horas assistidas do perfil."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []
    marks = ','.join('?' * len(ids))
    with closing(connect()) as conn:
        rows = _rows(conn, f'SELECT imdb_id, title, year, runtime, genres FROM movies WHERE imdb_id IN ({marks})', ids)
    for r in rows:
        r['genres_pt'] = [genre_pt(g) for g in (r['genres'] or '').split(', ') if g]
    return rows


def facets():
    with closing(connect()) as conn:
        years = _rows(conn, 'SELECT year, COUNT(*) AS count FROM movies GROUP BY year ORDER BY year DESC')
        genres = _rows(conn, 'SELECT genre, COUNT(*) AS count FROM movie_genres GROUP BY genre')
    for g in genres:
        g['label'] = genre_pt(g['genre'])
    genres.sort(key=lambda g: unicodedata.normalize('NFD', g['label']).encode('ascii', 'ignore').lower())
    return {'years': years, 'genres': genres}


def stats():
    with closing(connect()) as conn:
        meta = {r['key']: r['value'] for r in conn.execute('SELECT key, value FROM meta')}
        total, confident = conn.execute(
            'SELECT COUNT(*), SUM(CASE WHEN low_confidence = 0 THEN 1 ELSE 0 END) FROM movies').fetchone()
        min_year, max_year = conn.execute('SELECT MIN(year), MAX(year) FROM movies').fetchone()
    return {
        'total_movies': total,
        'confident_movies': confident,
        'min_year': min_year,
        'max_year': max_year,
        'updated_at': (meta.get('imdb_loaded_at') or '')[:10] or None,
        'methodology': {
            'prior_mean': float(meta.get('prior_mean') or 0),
            'prior_votes': int(meta.get('prior_votes') or 0),
            'min_votes': int(meta.get('min_votes') or 0),
        },
    }


# ----------------------------------------------------------------------
# Grafo de conexões
# ----------------------------------------------------------------------
GENRE_WEIGHT = 35
DIRECTOR_WEIGHT = 25
WRITER_WEIGHT = 20
CAST_WEIGHT_EACH = 10
CAST_WEIGHT_MAX = 20
GENRE_POOL = 800  # candidatos por gênero, além dos que compartilham equipe


def _profiles(conn, ids):
    """Gêneros e pessoas (por função) de uma lista de filmes."""
    ids = list(ids)
    prof = {i: {'genres': set(), 'director': set(), 'writer': set(), 'cast': set()} for i in ids}
    marks = ','.join('?' * len(ids))
    for r in conn.execute(f'SELECT imdb_id, genre FROM movie_genres WHERE imdb_id IN ({marks})', ids):
        prof[r['imdb_id']]['genres'].add(r['genre'])
    for r in conn.execute(f'SELECT imdb_id, person_id, role FROM movie_people WHERE imdb_id IN ({marks})', ids):
        prof[r['imdb_id']][r['role']].add(r['person_id'])
    return prof


def similarity(a, b, names):
    """Afinidade 0-100 entre dois perfis, com os motivos legíveis."""
    score, reasons = 0.0, []
    union = a['genres'] | b['genres']
    common_genres = sorted(a['genres'] & b['genres'])
    if union:
        score += GENRE_WEIGHT * len(common_genres) / len(union)
    if common_genres:
        reasons.append(f"Gêneros em comum: {', '.join(genre_pt(g) for g in common_genres)}")

    for role, weight, label in (('director', DIRECTOR_WEIGHT, 'Mesma direção'), ('writer', WRITER_WEIGHT, 'Mesmo roteiro')):
        shared = a[role] & b[role]
        if shared:
            score += weight
            reasons.append(f"{label}: {', '.join(sorted(names.get(p, p) for p in shared))}")

    shared_cast = a['cast'] & b['cast']
    if shared_cast:
        score += min(CAST_WEIGHT_MAX, CAST_WEIGHT_EACH * len(shared_cast))
        reasons.append(f"Elenco em comum: {', '.join(sorted(names.get(p, p) for p in shared_cast))}")

    return int(min(99, round(score))), reasons


def build_graph(center_id=None, max_neighbors=12, min_similarity=20):
    with closing(connect()) as conn:
        if center_id is None:
            row = conn.execute('SELECT imdb_id FROM movies ORDER BY rank LIMIT 1').fetchone()
            center_id = row['imdb_id'] if row else None
        center = conn.execute(f'SELECT {LIST_COLUMNS} FROM movies WHERE imdb_id = ?', (center_id,)).fetchone()
        if not center:
            return None

        # Candidatos: quem compartilha alguém da equipe + os mais bem ranqueados com gêneros em comum.
        people_ids = [r['imdb_id'] for r in conn.execute("""
            SELECT DISTINCT mp2.imdb_id FROM movie_people mp1
            JOIN movie_people mp2 ON mp2.person_id = mp1.person_id AND mp2.imdb_id <> mp1.imdb_id
            WHERE mp1.imdb_id = ?
        """, (center_id,))]
        genre_ids = [r['imdb_id'] for r in conn.execute("""
            SELECT g.imdb_id FROM movie_genres g JOIN movies m USING (imdb_id)
            WHERE g.genre IN (SELECT genre FROM movie_genres WHERE imdb_id = ?) AND g.imdb_id <> ?
            GROUP BY g.imdb_id ORDER BY COUNT(*) DESC, MIN(m.rank) ASC LIMIT ?
        """, (center_id, center_id, GENRE_POOL))]
        candidates = list(dict.fromkeys(people_ids + genre_ids))

        prof = _profiles(conn, [center_id] + candidates)
        all_people = set().union(*(p['director'] | p['writer'] | p['cast'] for p in prof.values()))
        names = {}
        if all_people:
            marks = ','.join('?' * len(all_people))
            names = {r['person_id']: r['name'] for r in conn.execute(
                f'SELECT person_id, name FROM people WHERE person_id IN ({marks})', list(all_people))}

        scored = []
        for cid in candidates:
            sim, reasons = similarity(prof[center_id], prof[cid], names)
            if sim >= min_similarity:
                scored.append((sim, cid, reasons))

        info = {}
        if scored:
            ids = [cid for _, cid, _ in scored]
            marks = ','.join('?' * len(ids))
            info = {r['imdb_id']: dict(r) for r in conn.execute(
                f'SELECT {LIST_COLUMNS} FROM movies WHERE imdb_id IN ({marks})', ids)}
        scored.sort(key=lambda x: (-x[0], info[x[1]]['rank']))
        neighbors = scored[:max_neighbors]

    def node(m, is_center, sim, reasons=()):
        p = prof[m['imdb_id']]
        return {
            **m,
            'id': m['imdb_id'],
            'genres': sorted(genre_pt(g) for g in p['genres']),
            'cast': [names.get(x, x) for x in list(p['cast'])[:4]],
            'writers': sorted(names.get(x, x) for x in p['writer']),
            'is_center': is_center,
            'similarity': sim,
            'reasons': list(reasons),
        }

    nodes = [node(dict(center), True, 100)]
    nodes += [node(info[cid], False, sim, reasons) for sim, cid, reasons in neighbors]
    links = [{'source': center_id, 'target': cid, 'similarity': sim, 'is_primary': True, 'reasons': list(reasons)}
             for sim, cid, reasons in neighbors]
    # Ligações entre vizinhos: cada filme mantém só as 2 mais fortes (afinidade mínima 30),
    # para a teia ficar conectada sem virar um emaranhado.
    pairs = []
    for i in range(len(neighbors)):
        for j in range(i + 1, len(neighbors)):
            a, b = neighbors[i][1], neighbors[j][1]
            sim, reasons = similarity(prof[a], prof[b], names)
            if sim >= 30:
                pairs.append((sim, a, b, reasons))
    degree = {}
    for sim, a, b, reasons in sorted(pairs, key=lambda x: -x[0]):
        if degree.get(a, 0) < 2 and degree.get(b, 0) < 2:
            links.append({'source': a, 'target': b, 'similarity': sim, 'is_primary': False, 'reasons': reasons})
            degree[a] = degree.get(a, 0) + 1
            degree[b] = degree.get(b, 0) + 1

    return {'center_id': center_id, 'nodes': nodes, 'links': links}
