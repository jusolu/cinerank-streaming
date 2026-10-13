"""
Vetores de recomendação: um conjunto de vetores por filme, usados pelo backend (/api/recommend).

Três visões complementares, 64 dimensões cada:
  content  do que o filme trata: vetores das sinopses (ingest/style_text.py), reduzidos por PCA.
  graph    quem fez e de que é feito: rede filme ↔ equipe/temas/estúdio/subgênero/universo/idioma,
           ponderada por raridade (TF-IDF) e comprimida por SVD (embedding espectral do grafo bipartido).
  collab   quem gostou: avaliações do MovieLens 32M (nota ≥ 4 = curtiu), fatoração por SVD.

Também avalia a recomendação com usuários reais do MovieLens: esconde as últimas curtidas de cada
um e mede quantas aparecem entre as 20 primeiras sugestões (recall@20), por método.

Roda no ambiente .venv-ml:
    .venv-ml/Scripts/python -m ingest.vectors
"""
import json
import os
import sys
import time
from collections import defaultdict

import numpy as np
import pandas as pd
import scipy.sparse as sp
from sklearn.decomposition import PCA, TruncatedSVD
from sklearn.preprocessing import normalize

from ingest.db import BASE_DIR, connect

DIM = 64
MIN_VOTES = 500
ML_DIR = os.path.join(BASE_DIR, 'data', 'raw', 'movielens', 'ml-32m')
TEXT_EMB = os.path.join(BASE_DIR, 'data', 'raw', 'text_embeddings.npz')
OUTPUT = os.path.join(BASE_DIR, 'data', 'reco_vectors.npz')
REPORT = os.path.join(BASE_DIR, 'data', 'raw', 'reco_eval.json')

EVAL_USERS = 3000
SEED = 7
LIKE = 4.0
TOP_K = 20


def log(msg):
    print(f"[vetores] {msg}", flush=True)


def catalog(conn):
    rows = conn.execute('SELECT imdb_id, imdb_votes, cinescore FROM movies WHERE imdb_votes >= ? '
                        'ORDER BY imdb_votes DESC', (MIN_VOTES,)).fetchall()
    ids = [r[0] for r in rows]
    return ids, {i: n for n, i in enumerate(ids)}, np.array([r[1] for r in rows], dtype=np.float64), \
        np.array([r[2] or 0 for r in rows], dtype=np.float32)


# ------------------------------------------------------------------ conteúdo
def content_vectors(ids, index):
    out = np.zeros((len(ids), DIM), dtype=np.float32)
    if not os.path.exists(TEXT_EMB):
        log('vetores de texto não encontrados; rode ingest.style_text antes')
        return out
    data = np.load(TEXT_EMB, allow_pickle=False)
    rows = [(index[i], k) for k, i in enumerate(data['ids']) if i in index]
    reduced = PCA(n_components=DIM, random_state=SEED).fit_transform(data['vectors'][[k for _, k in rows]])
    out[[r for r, _ in rows]] = normalize(reduced)
    log(f'conteúdo: {len(rows):,} filmes')
    return out


# ------------------------------------------------------------------ grafo
def graph_vectors(conn, ids, index):
    feats = defaultdict(set)
    q = conn.execute
    for iid, role, pid, ord_ in q("SELECT imdb_id, role, person_id, ord FROM movie_people WHERE person_id NOT LIKE 'tmdb:%'"):
        if iid in index and (role != 'cast' or ord_ < 4):
            feats[iid].add(f'{role}:{pid}')
    for iid, kw in q('SELECT imdb_id, keyword FROM movie_keywords'):
        if iid in index:
            feats[iid].add(f'kw:{kw.lower()}')
    for iid, lang, countries, companies in q('SELECT imdb_id, original_language, countries, companies FROM movie_dump'):
        if iid in index:
            if lang:
                feats[iid].add(f'lang:{lang}')
            for c in (countries or '').split(','):
                if c.strip():
                    feats[iid].add(f'country:{c.strip()}')
            for c in (companies or '').split(','):
                if c.strip():
                    feats[iid].add(f'studio:{c.strip()}')
    for iid, prop, value in q("SELECT imdb_id, prop, value FROM movie_wikidata "
                              "WHERE prop IN ('subgenre', 'franchise', 'based_on', 'subject', 'setting')"):
        if iid in index:
            feats[iid].add(f'{prop}:{value}')
    has_style = q("SELECT 1 FROM sqlite_master WHERE name = 'movie_style'").fetchone()
    if has_style:
        for iid, kind, value in q("SELECT imdb_id, kind, value FROM movie_style WHERE kind = 'tone'"):
            if iid in index:
                feats[iid].add(f'tone:{value}')
    for iid, genre in q('SELECT imdb_id, genre FROM movie_genres'):
        if iid in index:
            feats[iid].add(f'genre:{genre}')

    # Matriz filme × atributo, só com atributos que ligam pelo menos 2 filmes.
    df = defaultdict(int)
    for fs in feats.values():
        for f in fs:
            df[f] += 1
    n = len(ids)
    vocab = {f: k for k, f in enumerate(f for f, c in df.items() if 2 <= c <= 0.2 * n)}
    rows, cols, vals = [], [], []
    for iid, fs in feats.items():
        for f in fs:
            k = vocab.get(f)
            if k is not None:
                rows.append(index[iid])
                cols.append(k)
                vals.append(np.log(n / df[f]))  # ligações raras pesam mais
    m = normalize(sp.csr_matrix((vals, (rows, cols)), shape=(n, len(vocab)), dtype=np.float32))
    vecs = TruncatedSVD(n_components=DIM, random_state=SEED, n_iter=7).fit_transform(m)
    out = normalize(vecs).astype(np.float32)
    out[np.asarray(m.getnnz(axis=1)) == 0] = 0
    log(f'grafo: {len(vocab):,} atributos de ligação, {m.nnz:,} ligações')
    return out


# ------------------------------------------------------------------ gosto coletivo (MovieLens)
def load_movielens(index):
    links = pd.read_csv(os.path.join(ML_DIR, 'links.csv'), dtype={'imdbId': str})
    links['imdb'] = 'tt' + links['imdbId'].str.zfill(7)
    ml_to_col = {int(m): index[i] for m, i in zip(links['movieId'], links['imdb']) if i in index}
    t0 = time.time()
    r = pd.read_csv(os.path.join(ML_DIR, 'ratings.csv'), engine='pyarrow',
                    dtype={'userId': 'int32', 'movieId': 'int32', 'rating': 'float32', 'timestamp': 'int64'})
    r = r[r['rating'] >= LIKE]
    r = r[r['movieId'].isin(ml_to_col.keys())]
    r['col'] = r['movieId'].map(ml_to_col).astype('int32')
    log(f'MovieLens: {len(r):,} curtidas em filmes do catálogo ({len(ml_to_col):,} filmes ligados, {time.time() - t0:.0f}s)')
    return r


def split_eval(likes):
    """Separa usuários de teste e esconde as 20% curtidas mais recentes de cada um."""
    counts = likes.groupby('userId').size()
    eligible = counts[counts >= 15].index.to_numpy()
    rng = np.random.default_rng(SEED)
    test_users = set(rng.choice(eligible, size=min(EVAL_USERS, len(eligible)), replace=False).tolist())
    test = likes[likes['userId'].isin(test_users)].sort_values(['userId', 'timestamp'])
    profiles, hidden = {}, {}
    for uid, g in test.groupby('userId'):
        cols = g['col'].to_numpy()
        cut = max(2, int(len(cols) * 0.2))
        profiles[uid], hidden[uid] = cols[:-cut], set(cols[-cut:].tolist())
    hidden_pairs = {(u, c) for u, cs in hidden.items() for c in cs}
    mask = np.array([(u, c) not in hidden_pairs for u, c in zip(likes['userId'], likes['col'])]) \
        if len(hidden_pairs) else np.ones(len(likes), bool)
    return likes[mask], profiles, hidden


def collab_vectors(train, n_items):
    users = train['userId'].astype('category')
    x = sp.csr_matrix((np.ones(len(train), dtype=np.float32), (users.cat.codes.to_numpy(), train['col'].to_numpy())),
                      shape=(len(users.cat.categories), n_items))
    df = np.asarray((x > 0).sum(axis=0)).ravel()
    idf = np.log((x.shape[0] + 1) / (df + 1)).astype(np.float32)
    x = normalize(x @ sp.diags(idf))
    t0 = time.time()
    svd = TruncatedSVD(n_components=DIM, random_state=SEED, n_iter=7).fit(x)
    items = normalize((svd.components_.T * svd.singular_values_).astype(np.float32))
    items[df < 5] = 0  # sem curtidas suficientes, sem vetor
    log(f'gosto coletivo: {(df >= 5).sum():,} filmes com vetor ({time.time() - t0:.0f}s)')
    return items


# ------------------------------------------------------------------ avaliação
def evaluate(parts, profiles, hidden, votes, quality):
    """recall@20 por método; o perfil do usuário é a média dos vetores das curtidas conhecidas."""
    users = list(profiles)
    pop = np.log10(votes + 1)
    pop_z = (pop - pop.mean()) / pop.std()
    q_z = (quality - quality.mean()) / (quality.std() + 1e-6)

    profile_cache = {}

    def profile_matrix(name):
        if name not in profile_cache:
            v = parts[name]
            profile_cache[name] = normalize(np.stack([v[profiles[u]].mean(axis=0) for u in users]))
        return profile_cache[name]

    def recall_for(weights, prior, popularity_only=False, chunk=500):
        hits = []
        for start in range(0, len(users), chunk):
            block = users[start:start + chunk]
            if popularity_only:
                total = np.tile(pop_z, (len(block), 1)).astype(np.float32)
            else:
                total = np.zeros((len(block), len(votes)), dtype=np.float32)
                for name, w in weights.items():
                    if w:
                        total += w * (profile_matrix(name)[start:start + len(block)] @ parts[name].T)
                total += prior * (0.5 * pop_z + 0.5 * q_z)[None, :].astype(np.float32)
            for k, u in enumerate(block):
                s = total[k]
                s[profiles[u]] = -np.inf
                top = np.argpartition(-s, TOP_K)[:TOP_K]
                h = hidden[u]
                hits.append(len(h.intersection(top.tolist())) / min(TOP_K, len(h)))
        return float(np.mean(hits))

    results = {}
    results['Populares (baseline)'] = recall_for({}, 0, popularity_only=True)
    log(f"{'Populares (baseline)':28} recall@20 = {results['Populares (baseline)']:.3f}")
    configs = {
        'Só conteúdo': ({'content': 1}, 0),
        'Só grafo': ({'graph': 1}, 0),
        'Só gosto coletivo': ({'collab': 1}, 0),
        'Conteúdo + grafo': ({'content': 0.5, 'graph': 0.5}, 0),
        'Híbrido 20/30/50': ({'content': 0.2, 'graph': 0.3, 'collab': 0.5}, 0),
        'Híbrido 10/20/70': ({'content': 0.1, 'graph': 0.2, 'collab': 0.7}, 0),
        'Híbrido 10/20/70 + prior': ({'content': 0.1, 'graph': 0.2, 'collab': 0.7}, 0.03),
        'Híbrido 15/25/60 + prior': ({'content': 0.15, 'graph': 0.25, 'collab': 0.6}, 0.03),
    }
    for name, (w, prior) in configs.items():
        results[name] = recall_for(w, prior)
        log(f'{name:28} recall@20 = {results[name]:.3f}')
    return results, configs


def main():
    t0 = time.time()
    conn = connect()
    ids, index, votes, quality = catalog(conn)
    log(f'{len(ids):,} filmes no catálogo')

    content = content_vectors(ids, index)
    graph = graph_vectors(conn, ids, index)
    likes = load_movielens(index)
    train, profiles, hidden = split_eval(likes)
    log(f'avaliação: {len(profiles):,} usuários, {sum(len(h) for h in hidden.values()):,} curtidas escondidas')
    collab = collab_vectors(train, len(ids))

    parts = {'content': content, 'graph': graph, 'collab': collab}
    results, configs = evaluate(parts, profiles, hidden, votes, quality)
    best = max((k for k in results if k in configs), key=results.get)
    weights, prior = configs[best]
    log(f'melhor configuração: {best} (recall@20 = {results[best]:.3f}, populares = {results["Populares (baseline)"]:.3f})')

    # Para servir, o gosto coletivo é recalculado com todas as curtidas (sem esconder nada).
    collab_full = collab_vectors(likes, len(ids))
    np.savez_compressed(OUTPUT, ids=np.array(ids), content=content.astype(np.float16),
                        graph=graph.astype(np.float16), collab=collab_full.astype(np.float16))
    with open(REPORT, 'w', encoding='utf-8') as f:
        json.dump({'recall_at_20': results, 'best': best, 'weights': weights, 'prior': prior,
                   'users': len(profiles)}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(BASE_DIR, 'data', 'reco_config.json'), 'w', encoding='utf-8') as f:
        json.dump({'weights': weights, 'prior': prior, 'best': best,
                   'recall_at_20': results[best], 'baseline_recall_at_20': results['Populares (baseline)']},
                  f, ensure_ascii=False, indent=2)
    log(f'{OUTPUT} gravado ({os.path.getsize(OUTPUT) / 1e6:.1f} MB) em {time.time() - t0:.0f}s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
