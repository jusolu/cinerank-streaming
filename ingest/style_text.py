"""
Tom da narrativa: agrupa os filmes pelo "jeito" da história, a partir do texto.

1. Para cada filme, junta slogan + sinopse + temas + subgêneros (prefere o texto em inglês,
   para que o agrupamento não se divida por idioma).
2. Gera um vetor por filme com um modelo multilíngue rodando localmente (FastEmbed/ONNX, sem chave).
3. Agrupa os vetores com k-means.
4. Cada grupo recebe um nome em português definido em ingest/tone_labels.json, escrito a partir do
   relatório (filmes e temas mais típicos de cada grupo).

Roda no ambiente .venv-ml (fastembed, scikit-learn):
    .venv-ml/Scripts/python -m ingest.style_text --report   # gera vetores, agrupa e imprime o relatório
    .venv-ml/Scripts/python -m ingest.style_text --apply    # grava o tom de cada filme (usa tone_labels.json)
"""
import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict

import numpy as np

from ingest.db import BASE_DIR, connect

MODEL = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
K = 20
SEED = 42
MIN_VOTES = 500
EMB_PATH = os.path.join(BASE_DIR, 'data', 'raw', 'text_embeddings.npz')
LABELS_PATH = os.path.join(os.path.dirname(__file__), 'tone_labels.json')


def log(msg):
    print(f"[tom] {msg}", flush=True)


def load_texts(conn):
    rows = conn.execute("""
        SELECT m.imdb_id, m.imdb_votes, COALESCE(m.title_pt, m.title),
               d.tagline, d.overview_en, w.synopsis,
               (SELECT group_concat(keyword, ', ') FROM (SELECT keyword FROM movie_keywords k
                    WHERE k.imdb_id = m.imdb_id LIMIT 12)),
               (SELECT group_concat(value, ', ') FROM movie_wikidata x WHERE x.imdb_id = m.imdb_id AND x.prop = 'subgenre')
        FROM movies m
        LEFT JOIN movie_dump d USING (imdb_id)
        LEFT JOIN movie_wiki w USING (imdb_id)
        WHERE m.imdb_votes >= ?
    """, (MIN_VOTES,)).fetchall()
    ids, texts, meta = [], [], {}
    for iid, votes, title, tagline, ov_en, syn, kws, subg in rows:
        story = ov_en or syn
        if not story and not kws:
            continue
        parts = [tagline, story, f'Themes: {kws}' if kws else None, f'Genres: {subg}' if subg else None]
        ids.append(iid)
        texts.append(' '.join(p for p in parts if p)[:1500])
        meta[iid] = {'votes': votes, 'title': title, 'keywords': kws or '', 'subgenres': subg or ''}
    return ids, texts, meta


def embed(ids, texts):
    if os.path.exists(EMB_PATH):
        cached = np.load(EMB_PATH, allow_pickle=False)
        if list(cached['ids']) == ids:
            log('usando vetores já calculados')
            return cached['vectors']
    from fastembed import TextEmbedding
    model = TextEmbedding(MODEL)
    t0 = time.time()
    vectors = []
    for i, v in enumerate(model.embed(texts, batch_size=128)):
        vectors.append(v)
        if (i + 1) % 5000 == 0:
            log(f'{i + 1:,}/{len(texts):,} vetores ({(i + 1) / (time.time() - t0):.0f}/s)')
    vectors = np.asarray(vectors, dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-9
    np.savez_compressed(EMB_PATH, ids=np.array(ids), vectors=vectors)
    log(f'{len(vectors):,} vetores em {time.time() - t0:.0f}s')
    return vectors


def cluster(vectors):
    from sklearn.cluster import KMeans
    km = KMeans(n_clusters=K, random_state=SEED, n_init=4)
    labels = km.fit_predict(vectors)
    return labels, km


def report(ids, labels, meta, vectors, km):
    """Por grupo: tamanho, filmes mais votados perto do centro, temas mais característicos."""
    pos = {iid: i for i, iid in enumerate(ids)}
    by_cluster = defaultdict(list)
    for iid, c in zip(ids, labels):
        by_cluster[c].append(iid)
    total_kw = Counter(k for m in meta.values() for k in m['keywords'].split(', ') if k)
    n_total = len(ids)
    for c in range(K):
        members = by_cluster[c]
        kw = Counter(k for i in members for k in meta[i]['keywords'].split(', ') if k)
        # Lift: quanto o tema aparece mais no grupo do que no catálogo.
        lift = sorted(((n / len(members)) / (total_kw[k] / n_total), k) for k, n in kw.items() if n >= 8)[::-1][:10]
        sub = Counter(s for i in members for s in meta[i]['subgenres'].split(', ') if s).most_common(6)
        idx = [pos[i] for i in members]
        dist = vectors[idx] @ km.cluster_centers_[c]
        central = [members[j] for j in np.argsort(-dist)[:40]]
        famous = sorted(central, key=lambda i: -meta[i]['votes'])[:8]
        print(f'\n=== Grupo {c} · {len(members):,} filmes')
        print('  típicos:', ' | '.join(meta[i]['title'] for i in famous))
        print('  temas:', ', '.join(k for _, k in lift))
        print('  subgêneros:', ', '.join(f'{s} ({n})' for s, n in sub))


def apply(conn, ids, labels):
    with open(LABELS_PATH, encoding='utf-8') as f:
        names = {int(k): v for k, v in json.load(f).items()}
    rows = [(iid, 'tone', names[int(c)]) for iid, c in zip(ids, labels) if names.get(int(c))]
    with conn:
        conn.execute("CREATE TABLE IF NOT EXISTS movie_style (imdb_id TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL, "
                     "PRIMARY KEY (imdb_id, kind, value))")
        conn.execute("DELETE FROM movie_style WHERE kind = 'tone'")
        conn.executemany('INSERT OR IGNORE INTO movie_style VALUES (?, ?, ?)', rows)
    log(f'tom gravado para {len(rows):,} filmes')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    conn = connect()
    ids, texts, meta = load_texts(conn)
    log(f'{len(ids):,} filmes com texto')
    vectors = embed(ids, texts)
    labels, km = cluster(vectors)
    if args.report:
        report(ids, labels, meta, vectors, km)
    if args.apply:
        apply(conn, ids, labels)
    return 0


if __name__ == '__main__':
    sys.exit(main())
