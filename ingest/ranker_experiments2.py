"""
Experimentos da ordenação aprendida, 2ª rodada: o "não gostei" e 10 hipóteses sobre sinais (N1–N10),
mais a calibração do indicador de força (N11).

Protocolo (como ingest/ranker.py): usuários do MovieLens 32M em ordem de tempo; o que veio depois do
corte (as 20% curtidas mais recentes) fica escondido, INCLUINDO as notas baixas (≤ 2,5) dadas nesse
período. 300 candidatos do híbrido por usuário, LightGBM lambdarank.

Medidas no top 20:
  recall@20      fração das curtidas escondidas que aparecem
  ndcg@20        qualidade da ordem com relevância graduada (5★ > 4★ > 3★)
  detestados@20  fração dos filmes escondidos com nota ≤ 2,5 que aparecem (quanto menor, melhor)

Hipóteses
  N1  Sinais de "não gostei" (perfil e atributos do que a pessoa rejeitou) tiram filmes detestados do top 20.
  N2  Rótulos graduados, com o detestado ABAIXO do desconhecido, ensinam o modelo a evitar o que desagrada.
  N3  Curtidas recentes dizem mais que as antigas (perfil com peso pelo tempo).
  N4  O jeito de avaliar da pessoa (média, dispersão, taxa de curtidas) ajuda.
  N5  Para pessoas ecléticas (gostos espalhados), o gosto coletivo pesa mais que o conteúdo.
  N6  Para quem avaliou muito, direção e roteiro importam mais.
  N7  O perfil emocional do enredo (tom, final, violência, romance…) aproxima filmes de gosto parecido.
  N8  Duração e prêmios ajudam a ordenar (hipóteses H9 e H13).
  N9  A distância de época (ano do filme × época preferida) ajuda.
  N10 A distância de popularidade (quão "de nicho" a pessoa é × quão popular é o filme) ajuda,
      sobretudo para quem gosta de filmes menos vistos.
  N11 O indicador de força (percentil da pontuação) é calibrado: mais força, mais acerto.

    .venv-ml/Scripts/python -m ingest.ranker_experiments2
"""
import json
import math
import os
import sys
import time

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.preprocessing import normalize

from ingest import ranker as R
from ingest.db import BASE_DIR, connect
from ingest.hypotheses_awards import film_awards
from ingest.vectors import collab_vectors

REPORT = os.path.join(BASE_DIR, 'data', 'raw', 'ranker_experiments2.json')
TRAIN_USERS, TEST_USERS = 6000, 3000
NEG_GROUPS = ['genre', 'director', 'cast', 'theme', 'ptag']


def log(msg):
    print(f'[exp2] {msg}', flush=True)


def split(r):
    likes = r[r.rating >= 4]
    counts = likes.groupby('userId').size()
    eligible = counts[counts >= 15].index.to_numpy()
    rng = np.random.default_rng(R.SEED)
    chosen = rng.choice(eligible, size=min(TRAIN_USERS + TEST_USERS, len(eligible)), replace=False)
    test_ids = set(chosen[:TEST_USERS].tolist())
    users = {}
    for uid, g in r[r.userId.isin(set(chosen.tolist()))].sort_values(['userId', 'timestamp']).groupby('userId'):
        lk = g[g.rating >= 4]
        cut_n = max(2, int(len(lk) * 0.2))
        cut_ts = lk.timestamp.iloc[-cut_n]
        before, after = g[g.timestamp < cut_ts], g[g.timestamp >= cut_ts]
        liked = before[before.rating >= 4]
        if len(liked) < 3:
            continue
        users[uid] = {
            'liked': liked.col.to_numpy(), 'liked_rating': liked.rating.to_numpy(),
            'disliked': before.loc[before.rating <= 2.5, 'col'].to_numpy(),
            'ratings': before.rating.to_numpy(),
            'grade': {c: (3 if x >= 4.5 else 2 if x >= 4 else 1 if x >= 3 else -1) for c, x in zip(after.col, after.rating)},
            'test': uid in test_ids}
    hidden = {(u, c) for u, d in users.items() for c in d['grade']}
    keep = np.array([(u, c) not in hidden for u, c in zip(likes.userId.to_numpy(), likes.col.to_numpy())])
    return users, likes[keep]


def plot_matrix(conn, index):
    keys = ['tone', 'end_minus_start', 'ending', 'emo_fear', 'emo_anger', 'emo_joy', 'emo_sadness', 'emo_love',
            'emo_violence', 'emo_tech', 'emo_family', 'emo_money']
    M = np.zeros((len(index), len(keys)), dtype=np.float32)
    has = np.zeros(len(index), dtype=np.float32)
    for iid, p in conn.execute('SELECT imdb_id, profile FROM movie_plot_profile'):
        k = index.get(iid)
        if k is not None:
            d = json.loads(p)
            M[k] = [d.get(x, 0) for x in keys]
            has[k] = 1
    on = has > 0
    M[on] = (M[on] - M[on].mean(axis=0)) / (M[on].std(axis=0) + 1e-6)
    return normalize(M), has


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
    A = film_awards(conn).reindex(ids)
    awards = np.column_stack([np.log1p(A.wins.fillna(0)), np.log1p(A.noms.fillna(0))]).astype(np.float32)
    PM, has_plot = plot_matrix(conn, index)
    log(f'enredos com perfil: {int(has_plot.sum()):,}')

    users, train_likes = split(R.load_ratings(index))
    log(f'{len(users):,} usuários ({sum(d["test"] for d in users.values()):,} de teste)')
    parts = {'content': data['content'].astype(np.float32), 'graph': data['graph'].astype(np.float32),
             'collab': collab_vectors(train_likes, len(ids))}
    fz = R.Featurizer(parts, weights, tables, numeric)

    extra_names, groups = [], {}

    def grp(name, cols):
        groups[name] = list(range(len(R.FEATURE_NAMES) + len(extra_names), len(R.FEATURE_NAMES) + len(extra_names) + len(cols)))
        extra_names.extend(cols)

    grp('neg', ['neg_sim_content', 'neg_sim_graph', 'neg_sim_collab'] + [f'neg_aff_{g}' for g in NEG_GROUPS])
    grp('recency', ['rec_sim_content', 'rec_sim_graph', 'rec_sim_collab'])
    grp('calib', ['user_mean', 'user_std', 'user_like_rate'])
    grp('eclectic', ['eclecticism'])
    grp('popgap', ['pop_gap_abs', 'pop_gap_signed'])
    grp('yeargap', ['year_gap'])
    grp('quality', ['award_wins', 'award_noms'])
    grp('plot', ['plot_sim', 'has_plot'])
    names = R.FEATURE_NAMES + extra_names

    def extra(d, cands):
        L, D = d['liked'], d['disliked']
        cols = []
        for k in ('content', 'graph', 'collab'):
            v = parts[k]
            if len(D):
                p = v[D].mean(axis=0)
                p /= max(np.linalg.norm(p), 1e-9)
                cols.append(v[cands] @ p)
            else:
                cols.append(np.zeros(len(cands), np.float32))
        for g in NEG_GROUPS:
            t = tables[g]
            cols.append(t[cands] @ (np.asarray(t[D].sum(axis=0)).ravel() / len(D)) if len(D) else np.zeros(len(cands), np.float32))
        w = np.exp(np.linspace(-2, 0, len(L)))  # curtidas recentes pesam até e² ≈ 7× mais
        for k in ('content', 'graph', 'collab'):
            p = (parts[k][L] * w[:, None]).sum(axis=0)
            p /= max(np.linalg.norm(p), 1e-9)
            cols.append(parts[k][cands] @ p)
        rt = d['ratings']
        cols += [np.full(len(cands), rt.mean()), np.full(len(cands), rt.std()), np.full(len(cands), (rt >= 4).mean())]
        C = fz.combined[L]
        sims = C @ C.T
        ecl = 1 - (sims.sum() - len(L)) / max(len(L) * (len(L) - 1), 1)
        cols.append(np.full(len(cands), ecl))
        mlv = numeric[L, 0].mean()
        cols += [np.abs(numeric[cands, 0] - mlv), numeric[cands, 0] - mlv]
        cols.append(np.abs(numeric[cands, 2] - np.median(numeric[L, 2])))
        cols += [awards[cands, 0], awards[cands, 1]]
        lp = [k for k in L if has_plot[k]]
        if lp:
            p = PM[lp].mean(axis=0)
            p /= max(np.linalg.norm(p), 1e-9)
            cols.append((PM[cands] @ p) * has_plot[cands])
        else:
            cols.append(np.zeros(len(cands), np.float32))
        cols.append(has_plot[cands])
        return np.column_stack(cols).astype(np.float32)

    train, tests = [], []
    for k, (uid, d) in enumerate(users.items()):
        prof = fz.profiles(d['liked'])
        s = R.hybrid_scores(fz, prof, weights, prior_weight, prior)
        s[d['liked']] = -np.inf
        s[d['disliked']] = -np.inf
        cands = np.argpartition(-s, R.CANDIDATES)[:R.CANDIDATES]
        cands = cands[np.argsort(-s[cands])]
        X = np.column_stack([fz(d['liked'], d['disliked'], cands, prof), extra(d, cands)])
        g = np.array([d['grade'].get(c, 0) for c in cands])
        meta = {'n_liked': len(d['liked']), 'ecl': float(X[0, names.index('eclecticism')]),
                'mainstream': float(numeric[d['liked'], 0].mean())}
        (tests if d['test'] else train).append((cands, X, g, meta))
        if (k + 1) % 2000 == 0:
            log(f'sinais {k + 1:,}/{len(users):,} ({time.time() - t0:.0f}s)')
    train = [t for t in train if (t[2] >= 2).any()]
    Xtr = np.vstack([t[1] for t in train])
    G = np.concatenate([t[2] for t in train])
    groups_len = [len(t[0]) for t in train]
    n_dis_test = sum(int((t[2] == -1).sum()) for t in tests)
    log(f'treino {len(train):,} usuários; teste {len(tests):,} com {n_dis_test:,} detestados escondidos entre os candidatos')

    def labels(kind):
        if kind == 'binario':
            return (G >= 2).astype(np.float32)
        return np.select([G == 3, G == 2, G == 1, G == 0, G == -1], [4, 3, 2, 1, 0]).astype(np.float32)  # detestado < desconhecido

    def fit(cols, kind):
        m = lgb.LGBMRanker(objective='lambdarank', n_estimators=400, learning_rate=0.05, num_leaves=63, min_child_samples=50,
                           subsample=0.8, subsample_freq=1, colsample_bytree=0.8, random_state=R.SEED, verbose=-1,
                           label_gain=[0, 1, 3, 7, 15])
        m.fit(Xtr[:, cols], labels(kind), group=groups_len)
        return m

    def evaluate(order_fn, seg=None):
        rec, ndcg, dis, top_scores = [], [], [], []
        for cands, X, g, meta in tests:
            if seg and not seg(meta):
                continue
            order = order_fn(cands, X)
            top = order[:R.TOP_K]
            pos = {c: i for i, c in enumerate(cands)}
            gt = np.array([g[pos[c]] for c in top])
            liked_hidden = int((g >= 2).sum())
            if liked_hidden:
                rec.append((gt >= 2).sum() / min(R.TOP_K, liked_hidden))
            gains = np.where(gt > 0, 2.0 ** gt - 1, 0)
            ideal = np.sort(np.where(g > 0, 2.0 ** g - 1, 0))[::-1][:R.TOP_K]
            if ideal.sum() > 0:
                disc = 1 / np.log2(np.arange(2, R.TOP_K + 2))
                ndcg.append((gains * disc[:len(gains)]).sum() / (ideal * disc[:len(ideal)]).sum())
            nd = int((g == -1).sum())
            if nd:
                dis.append((gt == -1).sum() / nd)
        return {'recall_at_20': float(np.mean(rec)), 'ndcg_at_20': float(np.mean(ndcg)),
                'disliked_in_top20': float(np.mean(dis)) if dis else None, 'users': len(rec)}

    base = list(range(len(R.FEATURE_NAMES)))
    allx = base + [c for g in groups.values() for c in g]
    configs = {
        'atual (binário)': (base, 'binario'),
        'N1 + sinais de não gostei': (base + groups['neg'], 'binario'),
        'N2 + rótulos graduados': (base, 'graduado'),
        'N1+N2': (base + groups['neg'], 'graduado'),
        'completo (N1–N10)': (allx, 'graduado'),
    }
    for gname in ['neg', 'recency', 'calib', 'eclectic', 'popgap', 'yeargap', 'quality', 'plot']:
        configs[f'completo sem {gname}'] = ([c for c in allx if c not in groups[gname]], 'graduado')

    res, models = {'hibrido': evaluate(lambda c, X: c)}, {}
    for name, (cols, kind) in configs.items():
        m = fit(cols, kind)
        models[name] = (m, cols)
        res[name] = evaluate(lambda c, X, m=m, cols=cols: c[np.argsort(-m.predict(X[:, cols]))])
        log(f'{name}: recall {res[name]["recall_at_20"]:.4f} · ndcg {res[name]["ndcg_at_20"]:.4f} · '
            f'detestados {res[name]["disliked_in_top20"]:.4f} ({time.time() - t0:.0f}s)')

    # segmentos (N5, N6, N10): atual × completo
    segs = {}
    ecl = np.array([t[3]['ecl'] for t in tests])
    nl = np.array([t[3]['n_liked'] for t in tests])
    ms = np.array([t[3]['mainstream'] for t in tests])
    cuts = {'ecl': np.quantile(ecl, [1 / 3, 2 / 3]), 'n': np.quantile(nl, [1 / 3, 2 / 3]), 'ms': np.quantile(ms, [1 / 3, 2 / 3])}
    seg_defs = {
        'ecléticos (terço mais espalhado)': lambda m: m['ecl'] >= cuts['ecl'][1],
        'focados (terço mais concentrado)': lambda m: m['ecl'] <= cuts['ecl'][0],
        'muitas avaliações (terço de cima)': lambda m: m['n_liked'] >= cuts['n'][1],
        'poucas avaliações (terço de baixo)': lambda m: m['n_liked'] <= cuts['n'][0],
        'gosto de nicho (terço menos popular)': lambda m: m['mainstream'] <= cuts['ms'][0],
        'gosto popular (terço mais popular)': lambda m: m['mainstream'] >= cuts['ms'][1],
    }
    for sname, f in seg_defs.items():
        segs[sname] = {}
        for mname in ['atual (binário)', 'completo (N1–N10)', 'completo sem popgap', 'completo sem eclectic']:
            m, cols = models[mname]
            segs[sname][mname] = evaluate(lambda c, X, m=m, cols=cols: c[np.argsort(-m.predict(X[:, cols]))], seg=f)['recall_at_20']
    res['segments'] = segs

    # importância por grupo no modelo completo (ganho) e contribuição de N5/N6 por interação
    m, cols = models['completo (N1–N10)']
    gain = dict(zip([names[c] for c in cols], m.booster_.feature_importance('gain')))
    tot = sum(gain.values())
    res['importance_full'] = sorted(((k, round(v / tot, 4)) for k, v in gain.items()), key=lambda x: -x[1])[:20]
    # N5/N6: peso relativo de sim_collab × sim_content e de aff_director/writer por segmento (contribuições SHAP)
    contrib = {}
    for sname in ['ecléticos (terço mais espalhado)', 'focados (terço mais concentrado)', 'muitas avaliações (terço de cima)', 'poucas avaliações (terço de baixo)']:
        f = seg_defs[sname]
        Xs = np.vstack([X[:, cols] for c, X, g, meta in tests if f(meta)][:400])
        sh = m.booster_.predict(Xs, pred_contrib=True)[:, :-1]
        mean_abs = np.abs(sh).mean(axis=0)
        idx = {names[c]: i for i, c in enumerate(cols)}
        contrib[sname] = {k: float(mean_abs[idx[k]]) for k in ['sim_collab', 'sim_content', 'sim_graph', 'aff_director', 'aff_writer', 'pop_gap_abs', 'log_votes']}
    res['contrib_by_segment'] = contrib

    # N11: calibração do indicador de força (percentil da pontuação entre os 300 candidatos)
    bins = np.zeros((10, 2))
    for cands, X, g, meta in tests:
        sc = m.predict(X[:, cols])
        pct = np.argsort(np.argsort(sc)) / (len(sc) - 1)
        b = np.minimum((pct * 10).astype(int), 9)
        for i in range(10):
            sel = b == i
            bins[i, 0] += (g[sel] >= 2).sum()
            bins[i, 1] += sel.sum()
    res['N11_calibration'] = [{'decil': i + 1, 'taxa_curtida': float(bins[i, 0] / bins[i, 1])} for i in range(10)]
    log('N11 · taxa de curtida por decil de força: ' + ', '.join(f'{x["taxa_curtida"]:.3%}' for x in res['N11_calibration']))

    print('\n== Resultado')
    for k, v in res.items():
        if isinstance(v, dict) and 'recall_at_20' in v:
            print(f'  {k:32} recall {v["recall_at_20"]:.4f}  ndcg {v["ndcg_at_20"]:.4f}  detestados {v["disliked_in_top20"]:.4f}')
    print('\n== Segmentos (recall@20)')
    for s, d in segs.items():
        print(f'  {s:38} ' + ' · '.join(f'{k}: {v:.4f}' for k, v in d.items()))
    print('\n== Contribuição média |SHAP| por segmento')
    for s, d in contrib.items():
        print(f'  {s:38} ' + ', '.join(f'{k} {v:.3f}' for k, v in d.items()))
    print('\n== Importância (modelo completo): ' + ', '.join(f'{k} {v:.1%}' for k, v in res['importance_full'][:12]))
    with open(REPORT, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    log(f'concluído em {time.time() - t0:.0f}s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
