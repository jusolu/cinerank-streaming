"""
Avaliação de descoberta com o MovieLens Beliefs 2024 (Aridor et al., RecSys 2024).

O conjunto traz, para filmes que a pessoa AINDA NÃO VIU, a nota que ela espera dar
(userPredictRating) e a certeza (1 a 5), além do histórico de notas dela. É o que a simulação
com o MovieLens 32M não enxerga: se o sistema mostra filmes novos que a pessoa quer ver.

Para cada usuário e variante:
  1. Monta as respostas a partir das 60 notas mais recentes do histórico (sem os filmes da crença).
  2. Pede 50 recomendações ao mesmo motor do site (recommender.rank).
  3. Conta, entre os filmes não vistos sobre os quais a pessoa opinou:
       quero ver     expectativa ≥ 4     (acerto de descoberta)
       não quero     expectativa ≤ 2     (erro)
  Também separa os acertos por faixa de conhecimento (ingest/fame.py).

Uso (.venv-ml):
    .venv-ml/Scripts/python -m ingest.belief_eval --users 1500
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import recommender  # noqa: E402
from ingest.db import connect  # noqa: E402
from ingest.vectors import ML_DIR  # noqa: E402

B = os.path.join(recommender.BASE_DIR, 'data', 'raw', 'grouplens', 'ml_belief_2024_data_release_2', 'data_release')
REPORT = os.path.join(recommender.BASE_DIR, 'data', 'raw', os.environ.get('BELIEF_REPORT', 'belief_report.json'))
SEED = 3
HISTORY = 60
TOP_N = 50


def log(msg):
    print(f'[crenças] {msg}', flush=True)


def to_action(r):
    return 'right' if r >= 4 else 'left' if r <= 2.5 else 'down'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--users', type=int, default=1500)
    ap.add_argument('--variants', default=','.join(recommender.VARIANTS))
    args = ap.parse_args()
    variants = args.variants.split(',')
    t0 = time.time()
    m = recommender.model()

    links = pd.read_csv(os.path.join(ML_DIR, 'links.csv'), dtype={'imdbId': str})
    ml2col = {int(a): m.row['tt' + b.zfill(7)] for a, b in zip(links.movieId, links.imdbId) if 'tt' + b.zfill(7) in m.row}
    beliefs = pd.read_csv(os.path.join(B, 'belief_data.csv'), usecols=['userId', 'movieId', 'isSeen', 'userPredictRating', 'userCertainty'])
    beliefs = beliefs[(beliefs.isSeen == 0) & beliefs.userPredictRating.notna()]
    beliefs['col'] = beliefs.movieId.map(ml2col)
    beliefs = beliefs.dropna(subset=['col'])
    hist = pd.read_csv(os.path.join(B, 'user_rating_history.csv'))
    hist['col'] = hist.movieId.map(ml2col)
    hist = hist.dropna(subset=['col']).sort_values(['userId', 'tstamp'])
    counts = hist.groupby('userId').size()
    ok = set(counts[counts >= 20].index) & set(beliefs.userId)
    rng = np.random.default_rng(SEED)
    users = rng.choice(sorted(ok), size=min(args.users, len(ok)), replace=False)
    log(f'{len(ok):,} usuários com histórico e crenças no catálogo; avaliando {len(users):,}')

    tier = {m.row[i]: t for i, t in connect().execute('SELECT imdb_id, tier FROM movie_fame') if i in m.row}
    B_by_user = {u: g for u, g in beliefs[beliefs.userId.isin(users)].groupby('userId')}
    H_by_user = {u: g for u, g in hist[hist.userId.isin(users)].groupby('userId')}

    res = {v: {'up': 0, 'down': 0, 'mid': 0, 'users_hit': 0, 'by_tier': {}, 'shown_tier': {}} for v in variants}
    stats = {'beliefs_up': 0, 'beliefs_down': 0, 'beliefs_by_tier': {}}
    for n, u in enumerate(users, 1):
        bel = B_by_user[u]
        bcols = set(bel.col.astype(int))
        h = H_by_user[u]
        h = h[~h.col.astype(int).isin(bcols)].tail(HISTORY)
        answers = [{'id': m.ids[int(c)], 'action': to_action(r)} for c, r in zip(h.col, h.rating)]
        exp = dict(zip(bel.col.astype(int), bel.userPredictRating))
        for c, e in exp.items():
            if e >= 4:
                stats['beliefs_up'] += 1
                t = tier.get(c, 'sem dado')
                stats['beliefs_by_tier'][t] = stats['beliefs_by_tier'].get(t, 0) + 1
            elif e <= 2:
                stats['beliefs_down'] += 1
        for v in variants:
            chosen = recommender.rank(m, answers, n=TOP_N, min_votes=20000, variant=v)[0]
            r = res[v]
            hit = False
            for k in chosen:
                t = tier.get(k, 'sem dado')
                r['shown_tier'][t] = r['shown_tier'].get(t, 0) + 1
                e = exp.get(k)
                if e is None:
                    continue
                if e >= 4:
                    r['up'] += 1
                    hit = True
                    r['by_tier'][t] = r['by_tier'].get(t, 0) + 1
                elif e <= 2:
                    r['down'] += 1
                else:
                    r['mid'] += 1
            r['users_hit'] += hit
        if n % 100 == 0:
            log(f'{n}/{len(users)} ({time.time() - t0:.0f}s)')

    print(f'\n== Descoberta (Beliefs 2024): {len(users):,} usuários, {TOP_N} recomendações cada')
    print(f'   crenças no catálogo: {stats["beliefs_up"]:,} "quero ver" (≥4), {stats["beliefs_down"]:,} "não quero" (≤2)')
    print(f'{"var":4}{"quero ver":>11}{"não quero":>11}{"precisão":>10}{"usuários c/ acerto":>20}   acertos por faixa')
    for v in variants:
        r = res[v]
        prec = r['up'] / max(r['up'] + r['down'], 1)
        r['precision'] = prec
        print(f'{v:4}{r["up"]:11}{r["down"]:11}{prec:10.0%}{r["users_hit"] / len(users):20.1%}   '
              + ', '.join(f'{t}: {c}' for t, c in sorted(r['by_tier'].items())))
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, 'w', encoding='utf-8') as f:
        json.dump({'users': int(len(users)), 'top_n': TOP_N, 'history': HISTORY, 'stats': stats, 'results': res,
                   'labels': {v: recommender.VARIANTS[v]['label'] for v in variants}}, f, ensure_ascii=False, indent=2)
    log(f'relatório em {REPORT} ({time.time() - t0:.0f}s)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
