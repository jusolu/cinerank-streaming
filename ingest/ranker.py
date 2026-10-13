"""
Ordenação aprendida (learning to rank) para a recomendação do backend.

Para cada par (usuário, filme candidato) calcula ~30 sinais e treina um LightGBM (lambdarank)
com o comportamento real de usuários do MovieLens: dado o que a pessoa curtiu e rejeitou antes,
quais candidatos ela curtiu depois.

Sinais:
  similaridade com o perfil       conteúdo, grafo, gosto coletivo (3)
  parecido com o que curtiu        máximo e média da similaridade com cada curtida (2)
  parecido com o que rejeitou      máximo da similaridade com cada rejeição (1)
  afinidade por atributo           gênero, década, idioma, universo, visual, temas, direção,
                                   roteiro, elenco, fotografia/trilha (10)
  o filme em si                    votos, CineScore, ano, duração, crítica, Oscar, bilheteria,
                                   tem gosto coletivo (8)
  o usuário                        quantidade de curtidas e de rejeições (2)

Avalia contra a recomendação híbrida atual nos mesmos usuários de teste e só grava o modelo se ganhar.

Roda no ambiente .venv-ml:
    .venv-ml/Scripts/python -m ingest.ranker
"""
import json
import math
import os
import re
import sys
import time
from collections import defaultdict

import lightgbm as lgb
import numpy as np
import pandas as pd
import scipy.sparse as sp
from sklearn.preprocessing import normalize

from ingest.db import BASE_DIR, connect
from ingest.vectors import ML_DIR, collab_vectors

VECTORS = os.path.join(BASE_DIR, 'data', 'reco_vectors.npz')
CONFIG = os.path.join(BASE_DIR, 'data', 'reco_config.json')
FEATURES = os.path.join(BASE_DIR, 'data', 'reco_features.npz')
MODEL = os.path.join(BASE_DIR, 'data', 'ranker.txt')
REPORT = os.path.join(BASE_DIR, 'data', 'raw', 'ranker_eval.json')

SEED = 7
LIKE, DISLIKE = 4.0, 2.5
TRAIN_USERS, TEST_USERS = 8000, 3000
CANDIDATES = 300
TOP_K = 20
TABLE_GROUPS = ['genre', 'decade', 'lang', 'tone', 'visual', 'theme', 'director', 'writer', 'cast', 'crew',
                'ptag', 'producer', 'cast_all']


def log(msg):
    print(f"[ranker] {msg}", flush=True)


# ------------------------------------------------------------------ atributos por filme
def film_tables(conn, ids, index):
    """Matrizes esparsas filme × valor, por grupo de atributo, e atributos numéricos."""
    n = len(ids)
    entries = defaultdict(list)

    def add(group, iid, value):
        k = index.get(iid)
        if k is not None and value:
            entries[group].append((k, str(value)))

    q = conn.execute
    roles = {'director': 'director', 'writer': 'writer', 'cast': 'cast', 'photography': 'crew', 'music': 'crew',
             'editing': 'crew', 'design': 'crew', 'costume': 'crew', 'producer': 'producer', 'exec_producer': 'producer'}
    for iid, role, pid, ord_ in q("SELECT imdb_id, role, person_id, ord FROM movie_people WHERE person_id NOT LIKE 'tmdb:%'"):
        if role == 'cast':
            add('cast_all', iid, pid)  # elenco inteiro
            if ord_ < 4:
                add('cast', iid, pid)  # protagonistas
        elif role in roles:
            add(roles[role], iid, pid)
    if q("SELECT 1 FROM sqlite_master WHERE name = 'movie_tags'").fetchone():
        for iid, tag in q('SELECT imdb_id, tag FROM movie_tags'):
            add('ptag', iid, tag)
    for iid, g in q('SELECT imdb_id, genre FROM movie_genres'):
        add('genre', iid, g)
    numeric = np.zeros((n, 8), dtype=np.float32)
    for iid, year, runtime, votes, score in q('SELECT imdb_id, year, runtime, imdb_votes, cinescore FROM movies'):
        k = index.get(iid)
        if k is None:
            continue
        add('decade', iid, (year // 10) * 10 if year else None)
        numeric[k, 0] = math.log10((votes or 0) + 1)
        numeric[k, 1] = score or 0
        numeric[k, 2] = year or 0
        numeric[k, 3] = runtime or 0
    for iid, lang in q('SELECT imdb_id, original_language FROM movie_dump'):
        add('lang', iid, lang)
    for iid, kw in q('SELECT imdb_id, keyword FROM movie_keywords'):
        add('theme', iid, f'kw:{kw.lower()}')
    critic = {}
    for iid, prop, value in q("SELECT imdb_id, prop, value FROM movie_wikidata "
                              "WHERE prop IN ('subgenre', 'subject', 'score_metacritic', 'score_rt', 'award', 'box_office_usd')"):
        k = index.get(iid)
        if k is None:
            continue
        if prop in ('subgenre', 'subject'):
            add('theme', iid, f'{prop}:{value}')
        elif prop in ('score_metacritic', 'score_rt'):
            m = re.match(r'\s*(\d+(?:\.\d+)?)\s*(%|/100)', value)
            if m:
                critic.setdefault(k, []).append(float(m.group(1)))
        elif prop == 'award' and re.search(r'[oó]scar|academy award', value, re.I):
            numeric[k, 5] = 1
        elif prop == 'box_office_usd':
            try:
                numeric[k, 6] = max(numeric[k, 6], math.log10(float(value) + 1))
            except ValueError:
                pass
    for k, vals in critic.items():
        numeric[k, 4] = float(np.mean(vals))
    if q("SELECT 1 FROM sqlite_master WHERE name = 'movie_style'").fetchone():
        for iid, kind, value in q('SELECT imdb_id, kind, value FROM movie_style'):
            add('tone' if kind == 'tone' else 'visual', iid, f'{kind}:{value}')

    tables = {}
    for g in TABLE_GROUPS:
        vocab = {}
        rows, cols = [], []
        for k, v in entries[g]:
            rows.append(k)
            cols.append(vocab.setdefault(v, len(vocab)))
        m = sp.csr_matrix((np.ones(len(rows), dtype=np.float32), (rows, cols)), shape=(n, max(1, len(vocab))))
        m.data[:] = 1.0  # binário (duplicatas viram 1)
        tables[g] = m
        log(f'atributo {g:9}: {len(vocab):,} valores')
    return tables, numeric


# ------------------------------------------------------------------ sinais por (usuário, candidato)
FEATURE_NAMES = (['sim_content', 'sim_graph', 'sim_collab', 'max_sim_liked', 'mean_sim_liked', 'max_sim_disliked']
                 + [f'aff_{g}' for g in TABLE_GROUPS]
                 + ['log_votes', 'cinescore', 'year', 'runtime', 'critic', 'oscar', 'log_box_office', 'has_collab']
                 + ['n_liked', 'n_disliked'])


class Featurizer:
    def __init__(self, parts, weights, tables, numeric):
        self.parts = parts
        self.tables = tables
        self.numeric = numeric
        stacked = np.hstack([parts[k] * np.sqrt(weights.get(k, 0)) for k in ('content', 'graph', 'collab')])
        self.combined = stacked / np.maximum(np.linalg.norm(stacked, axis=1, keepdims=True), 1e-6)
        self.has_collab = (np.abs(parts['collab']).sum(axis=1) > 0).astype(np.float32)

    def profiles(self, liked):
        return {k: normalize(v[liked].mean(axis=0, keepdims=True))[0] for k, v in self.parts.items()}

    def __call__(self, liked, disliked, cands, prof=None):
        prof = prof or self.profiles(liked)
        cols = [self.parts[k][cands] @ prof[k] for k in ('content', 'graph', 'collab')]
        sim_l = self.combined[cands] @ self.combined[liked].T
        cols += [sim_l.max(axis=1), sim_l.mean(axis=1)]
        if len(disliked):
            cols.append((self.combined[cands] @ self.combined[disliked].T).max(axis=1))
        else:
            cols.append(np.zeros(len(cands), dtype=np.float32))
        for g in TABLE_GROUPS:
            t = self.tables[g]
            pg = np.asarray(t[liked].sum(axis=0)).ravel() / len(liked)
            cols.append(t[cands] @ pg)
        num = self.numeric[cands]
        cols += [num[:, i] for i in range(7)] + [self.has_collab[cands]]
        cols += [np.full(len(cands), math.log1p(len(liked)), dtype=np.float32),
                 np.full(len(cands), math.log1p(len(disliked)), dtype=np.float32)]
        return np.column_stack(cols).astype(np.float32)


def hybrid_scores(fz, prof, weights, prior_weight, prior):
    s = prior_weight * prior.copy()
    for k, w in weights.items():
        s = s + w * (fz.parts[k] @ prof[k])
    return s


# ------------------------------------------------------------------ dados do MovieLens
def load_ratings(index):
    links = pd.read_csv(os.path.join(ML_DIR, 'links.csv'), dtype={'imdbId': str})
    links['imdb'] = 'tt' + links['imdbId'].str.zfill(7)
    ml_to_col = {int(m): index[i] for m, i in zip(links['movieId'], links['imdb']) if i in index}
    r = pd.read_csv(os.path.join(ML_DIR, 'ratings.csv'), engine='pyarrow',
                    dtype={'userId': 'int32', 'movieId': 'int32', 'rating': 'float32', 'timestamp': 'int64'})
    r = r[r['movieId'].isin(ml_to_col.keys())]
    r['col'] = r['movieId'].map(ml_to_col).astype('int32')
    return r


def split_users(r):
    likes = r[r['rating'] >= LIKE]
    counts = likes.groupby('userId').size()
    eligible = counts[counts >= 15].index.to_numpy()
    rng = np.random.default_rng(SEED)
    chosen = rng.choice(eligible, size=min(TRAIN_USERS + TEST_USERS, len(eligible)), replace=False)
    test_ids, train_ids = set(chosen[:TEST_USERS].tolist()), set(chosen[TEST_USERS:].tolist())
    users = {}
    sub = r[r['userId'].isin(test_ids | train_ids)].sort_values(['userId', 'timestamp'])
    for uid, g in sub.groupby('userId'):
        lk = g[g['rating'] >= LIKE]
        cut_n = max(2, int(len(lk) * 0.2))
        cut_ts = lk['timestamp'].iloc[-cut_n]
        before = g[g['timestamp'] < cut_ts]
        users[uid] = {
            'liked': before.loc[before['rating'] >= LIKE, 'col'].to_numpy(),
            'disliked': before.loc[before['rating'] <= DISLIKE, 'col'].to_numpy(),
            'target': set(lk['col'].iloc[-cut_n:].tolist()),
            'test': uid in test_ids,
        }
    users = {u: d for u, d in users.items() if len(d['liked']) >= 3}
    hidden = {(u, c) for u, d in users.items() for c in d['target']}
    keep = np.array([(u, c) not in hidden for u, c in zip(likes['userId'].to_numpy(), likes['col'].to_numpy())])
    return users, likes[keep]


def main():
    t0 = time.time()
    conn = connect()
    data = np.load(VECTORS, allow_pickle=False)
    ids = [str(i) for i in data['ids']]
    index = {iid: k for k, iid in enumerate(ids)}
    with open(CONFIG, encoding='utf-8') as f:
        cfg = json.load(f)
    weights = {k: float(v) for k, v in cfg['weights'].items() if v}
    prior_weight = float(cfg.get('prior', 0.03))

    tables, numeric = film_tables(conn, ids, index)
    pop = numeric[:, 0]
    q = numeric[:, 1]
    prior = (0.5 * (pop - pop.mean()) / pop.std() + 0.5 * (q - q.mean()) / (q.std() + 1e-6)).astype(np.float32)

    r = load_ratings(index)
    users, train_likes = split_users(r)
    log(f'{len(users):,} usuários ({sum(d["test"] for d in users.values()):,} de teste)')

    # Gosto coletivo sem as curtidas reservadas de ninguém (evita "colar" a resposta).
    collab_eval = collab_vectors(train_likes.rename(columns={}), len(ids))
    parts = {'content': data['content'].astype(np.float32), 'graph': data['graph'].astype(np.float32),
             'collab': collab_eval}
    fz = Featurizer(parts, weights, tables, numeric)

    X_train, y_train, groups = [], [], []
    test_cases = []
    for k, (uid, d) in enumerate(users.items()):
        liked, disliked = d['liked'], d['disliked']
        prof = fz.profiles(liked)
        s = hybrid_scores(fz, prof, weights, prior_weight, prior)
        s[liked] = -np.inf
        s[disliked] = -np.inf
        cands = np.argpartition(-s, CANDIDATES)[:CANDIDATES]
        cands = cands[np.argsort(-s[cands])]
        labels = np.array([c in d['target'] for c in cands], dtype=np.float32)
        if d['test']:
            test_cases.append((cands, fz(liked, disliked, cands, prof), d['target']))
        elif labels.any():
            X_train.append(fz(liked, disliked, cands, prof))
            y_train.append(labels)
            groups.append(len(cands))
        if (k + 1) % 2000 == 0:
            log(f'sinais: {k + 1:,}/{len(users):,} usuários ({time.time() - t0:.0f}s)')

    X = np.vstack(X_train)
    y = np.concatenate(y_train)
    log(f'treino: {len(groups):,} usuários, {len(y):,} pares, {int(y.sum()):,} positivos')
    ranker = lgb.LGBMRanker(objective='lambdarank', n_estimators=400, learning_rate=0.05, num_leaves=63,
                            min_child_samples=50, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                            random_state=SEED, verbose=-1)
    ranker.fit(X, y, group=groups, feature_name=FEATURE_NAMES)

    def recall(order_fn):
        vals = []
        for cands, feats, target in test_cases:
            top = order_fn(cands, feats)[:TOP_K]
            vals.append(len(target.intersection(top.tolist())) / min(TOP_K, len(target)))
        return float(np.mean(vals))

    base = recall(lambda c, f: c)  # ordem do híbrido atual
    learned = recall(lambda c, f: c[np.argsort(-ranker.predict(f))])
    importance = sorted(zip(FEATURE_NAMES, ranker.booster_.feature_importance('gain')), key=lambda x: -x[1])
    log(f'recall@20 · híbrido atual: {base:.3f} · ordenação aprendida: {learned:.3f} ({(learned / base - 1) * 100:+.0f}%)')
    log('sinais mais importantes: ' + ', '.join(f'{n} ({v / sum(x for _, x in importance):.0%})' for n, v in importance[:10]))

    report = {'recall_at_20': {'hibrido_atual': base, 'ordenacao_aprendida': learned},
              'test_users': len(test_cases), 'train_users': len(groups),
              'importance': {n: float(v) for n, v in importance}}
    with open(REPORT, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    if learned > base:
        ranker.booster_.save_model(MODEL)
        np.savez_compressed(FEATURES, **{f'tbl_{g}_{a}': getattr(tables[g], a) for g in TABLE_GROUPS
                                         for a in ('data', 'indices', 'indptr')},
                            **{f'tbl_{g}_shape': np.array(tables[g].shape) for g in TABLE_GROUPS},
                            numeric=numeric)
        cfg['ranker'] = {'recall_at_20': learned, 'baseline_recall_at_20': base, 'candidates': CANDIDATES}
        with open(CONFIG, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        log(f'modelo gravado em {MODEL} ({os.path.getsize(MODEL) / 1e6:.1f} MB) e atributos em {FEATURES} '
            f'({os.path.getsize(FEATURES) / 1e6:.1f} MB)')
    else:
        log('a ordenação aprendida não superou o híbrido; nada gravado')
    log(f'concluído em {time.time() - t0:.0f}s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
