"""
Experimentos com a ordenação aprendida (hipóteses 1 e 2 do relatório de avaliação).

  H1  Sem os sinais de popularidade (votos, CineScore, bilheteria, tem gosto coletivo), o modelo
      expõe mais filmes menos conhecidos e perde pouco acerto.
  H2  A afinidade com o Tag Genome 2021 (relevância de 1.084 etiquetas por filme) melhora o recall@20.

Mesmo protocolo de ingest/ranker.py: usuários do MovieLens com as 20% curtidas mais recentes
escondidas, 300 candidatos do híbrido, LightGBM lambdarank. Mede, para cada modelo:
  recall@20 geral e por faixa de conhecimento do filme escondido (ingest/fame.py),
  e quanto do top 20 é de filmes fora da faixa "muito conhecido".

Não grava modelo; só o relatório em data/raw/ranker_experiments.json.
    .venv-ml/Scripts/python -m ingest.ranker_experiments
"""
import json
import os
import sys
import time

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.preprocessing import normalize

from ingest import ranker as R
from ingest.db import BASE_DIR, connect
from ingest.vectors import collab_vectors

GENOME = os.path.join(BASE_DIR, 'data', 'raw', 'grouplens', 'genome_2021', 'movie_dataset_public_final')
REPORT = os.path.join(BASE_DIR, 'data', 'raw', 'ranker_experiments.json')
POPULARITY = ['log_votes', 'cinescore', 'log_box_office', 'has_collab', 'critic', 'oscar']
TIERS = ['Pouco conhecido', 'Médio', 'Muito conhecido']


def log(msg):
    print(f'[exp] {msg}', flush=True)


def genome_matrix(index):
    meta = {}
    with open(os.path.join(GENOME, 'raw', 'metadata_updated.json'), encoding='utf-8') as f:
        for line in f:
            x = json.loads(line)
            meta[x['item_id']] = 'tt' + str(x['imdbId']).zfill(7)
    s = pd.read_csv(os.path.join(GENOME, 'scores', 'tagdl.csv'), engine='pyarrow')
    s['col'] = s['item_id'].map(lambda i: index.get(meta.get(i)))
    s = s.dropna(subset=['col'])
    tags = {t: k for k, t in enumerate(sorted(s['tag'].unique()))}
    G = np.zeros((len(index), len(tags)), dtype=np.float32)
    G[s['col'].astype(int).to_numpy(), s['tag'].map(tags).to_numpy()] = s['score'].to_numpy(dtype=np.float32)
    has = (np.abs(G).sum(axis=1) > 0)
    G = normalize(G - G[has].mean(axis=0) * has[:, None])  # centraliza para o cosseno medir diferença de perfil
    log(f'Tag Genome: {has.sum():,} filmes do catálogo com {len(tags)} etiquetas')
    return G, has.astype(np.float32)


def genome_cols(G, has, liked, cands):
    lk = [k for k in liked if has[k]]
    if not lk:
        return np.zeros(len(cands), np.float32), has[cands], np.zeros(len(cands), np.float32)
    prof = G[lk].mean(axis=0)
    prof /= max(np.linalg.norm(prof), 1e-9)
    sims = G[cands] @ G[lk].T
    return (G[cands] @ prof) * has[cands], has[cands], sims.max(axis=1) * has[cands]


def main():
    t0 = time.time()
    conn = connect()
    data = np.load(R.VECTORS, allow_pickle=False)
    ids = [str(i) for i in data['ids']]
    index = {iid: k for k, iid in enumerate(ids)}
    cfg = json.load(open(R.CONFIG, encoding='utf-8'))
    weights = {k: float(v) for k, v in cfg['weights'].items() if v}
    prior_weight = float(cfg.get('prior', 0.03))
    tables, numeric = R.film_tables(conn, ids, index)
    pop, q = numeric[:, 0], numeric[:, 1]
    prior = (0.5 * (pop - pop.mean()) / pop.std() + 0.5 * (q - q.mean()) / (q.std() + 1e-6)).astype(np.float32)
    tier = np.array([None] * len(ids), dtype=object)
    for iid, t in conn.execute('SELECT imdb_id, tier FROM movie_fame'):
        if iid in index:
            tier[index[iid]] = t
    G, has_g = genome_matrix(index)
    from ingest.hypotheses_awards import film_awards
    A = film_awards(conn).reindex(ids)
    award_feats = np.column_stack([np.log1p(A.wins.fillna(0)), np.log1p(A.noms.fillna(0)),
                                   A.fest_win.fillna(False).astype(float), A.oscar_nom.fillna(False).astype(float)]).astype(np.float32)

    R.TRAIN_USERS, R.TEST_USERS = 6000, 3000
    users, train_likes = R.split_users(R.load_ratings(index))
    parts = {'content': data['content'].astype(np.float32), 'graph': data['graph'].astype(np.float32),
             'collab': collab_vectors(train_likes, len(ids))}
    fz = R.Featurizer(parts, weights, tables, numeric)
    names = R.FEATURE_NAMES + ['genome_aff', 'has_genome', 'genome_max', 'award_wins', 'award_noms', 'festival_win', 'oscar_nom']
    Xtr, ytr, grp, tests = [], [], [], []
    for k, (uid, d) in enumerate(users.items()):
        prof = fz.profiles(d['liked'])
        s = R.hybrid_scores(fz, prof, weights, prior_weight, prior)
        s[d['liked']] = -np.inf
        s[d['disliked']] = -np.inf
        cands = np.argpartition(-s, R.CANDIDATES)[:R.CANDIDATES]
        cands = cands[np.argsort(-s[cands])]
        X = np.column_stack([fz(d['liked'], d['disliked'], cands, prof), *genome_cols(G, has_g, d['liked'], cands), award_feats[cands]])
        y = np.array([c in d['target'] for c in cands], dtype=np.float32)
        if d['test']:
            tests.append((cands, X, d['target']))
        elif y.any():
            Xtr.append(X)
            ytr.append(y)
            grp.append(len(cands))
        if (k + 1) % 2000 == 0:
            log(f'sinais {k + 1:,}/{len(users):,} ({time.time() - t0:.0f}s)')
    X, y = np.vstack(Xtr), np.concatenate(ytr)
    base_cols = list(range(len(R.FEATURE_NAMES)))
    nb = len(R.FEATURE_NAMES)
    award_cols = [nb + 3 + j for j in range(4)]
    sets = {
        'atual': base_cols,
        'com_premios': base_cols + award_cols,
        'com_premios_genome': base_cols + [nb + j for j in range(3)] + award_cols,
        'sem_popularidade': [i for i in base_cols if names[i] not in POPULARITY],
        'com_genome': base_cols + [nb + j for j in range(3)],
        'sem_pop_com_premios': [i for i in base_cols if names[i] not in POPULARITY] + award_cols,
        'com_genome_sem_pop': [i for i in base_cols if names[i] not in POPULARITY] + [nb + j for j in range(3)],
    }
    results = {}

    def evaluate(order_fn):
        rec, by_tier, tail = [], {t: [0, 0] for t in TIERS}, []
        for cands, Xt, target in tests:
            top = order_fn(cands, Xt)[:R.TOP_K].tolist()
            rec.append(len(target & set(top)) / min(R.TOP_K, len(target)))
            tail.append(np.mean([tier[c] != 'Muito conhecido' for c in top]))
            for c in target:
                t = tier[c]
                if t in by_tier:
                    by_tier[t][0] += c in top
                    by_tier[t][1] += 1
        return {'recall_at_20': float(np.mean(rec)), 'tail_share_top20': float(np.mean(tail)),
                'recall_by_tier': {t: (h / n if n else None) for t, (h, n) in by_tier.items()},
                'targets_by_tier': {t: n for t, (h, n) in by_tier.items()}}

    results['hibrido'] = evaluate(lambda c, f: c)
    for name, cols in sets.items():
        m = lgb.LGBMRanker(objective='lambdarank', n_estimators=400, learning_rate=0.05, num_leaves=63,
                           min_child_samples=50, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                           random_state=R.SEED, verbose=-1)
        m.fit(X[:, cols], y, group=grp)
        results[name] = evaluate(lambda c, f, m=m, cols=cols: c[np.argsort(-m.predict(f[:, cols]))])
        imp = sorted(zip([names[i] for i in cols], m.booster_.feature_importance('gain')), key=lambda x: -x[1])
        tot = sum(v for _, v in imp)
        results[name]['top_features'] = [(n, round(v / tot, 3)) for n, v in imp[:6]]
        log(f'{name}: treinado ({time.time() - t0:.0f}s)')

    print(f'\n== {len(tests):,} usuários de teste, {len(grp):,} de treino')
    print(f'{"modelo":22}{"recall@20":>10}{"fora de muito conhecido no top20":>34}   recall por faixa do filme escondido')
    for name, r in results.items():
        print(f'{name:22}{r["recall_at_20"]:10.3f}{r["tail_share_top20"]:34.1%}   ' + ' · '.join(
            f'{t}: {"—" if v is None else f"{v:.3f}"}' for t, v in r['recall_by_tier'].items()))
    print('alvos por faixa:', results['hibrido']['targets_by_tier'])
    for name in sets:
        print(f'  {name}: ' + ', '.join(f'{n} {v:.0%}' for n, v in results[name]['top_features']))
    with open(REPORT, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    log(f'concluído em {time.time() - t0:.0f}s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
