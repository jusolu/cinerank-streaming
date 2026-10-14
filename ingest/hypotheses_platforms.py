"""
Hipótese 10: presença em plataformas (ingest/xrefs.py) como sinal.

  H10a  Filmes na curadoria do MUBI recebem notas melhores no MovieLens, em cada faixa de conhecimento.
  H10b  Estar num catálogo de streaming (Netflix, Apple TV) e o número de plataformas em que o filme
        aparece ajudam a prever o "quero ver" (Beliefs) além dos votos e dos prêmios.

Resultados em data/raw/hypotheses_platforms.json.
    .venv-ml/Scripts/python -m ingest.hypotheses_platforms
"""
import json
import os
import sys

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score

from ingest.db import BASE_DIR, connect
from ingest.hypotheses_awards import TIERS, film_awards
from ingest.vectors import ML_DIR

RAW = os.path.join(BASE_DIR, 'data', 'raw')
OUT = os.path.join(RAW, 'hypotheses_platforms.json')


def main():
    conn = connect()
    F = film_awards(conn)
    x = pd.DataFrame(conn.execute('SELECT imdb_id, platform FROM movie_xref').fetchall(), columns=['imdb_id', 'platform'])
    P = pd.crosstab(x.imdb_id, x.platform).clip(upper=1).reindex(F.index).fillna(0)
    F['n_platforms'] = P.sum(axis=1)
    F['mubi'] = P['mubi'] > 0
    F['streaming'] = (P[['netflix', 'appletv', 'primevideo']].sum(axis=1) > 0)
    res = {'coverage_by_tier': F.groupby('tier')[['mubi', 'streaming']].mean().reindex(TIERS).round(3).to_dict()}
    print('== Cobertura por faixa:\n' + F.groupby('tier')[['mubi', 'streaming', 'n_platforms']].mean().reindex(TIERS).round(2).to_string())

    links = pd.read_csv(os.path.join(ML_DIR, 'links.csv'), dtype={'imdbId': str})
    links['imdb_id'] = 'tt' + links['imdbId'].str.zfill(7)
    r = pd.read_csv(os.path.join(ML_DIR, 'ratings.csv'), engine='pyarrow', usecols=['userId', 'movieId', 'rating'])
    r = r.merge(links[['movieId', 'imdb_id']], on='movieId').join(F[['tier', 'mubi', 'streaming']], on='imdb_id', how='inner')
    r['centered'] = r.rating - r.groupby('userId').rating.transform('mean')
    out = []
    print('\n== H10a · Nota no MovieLens (descontada a média da pessoa), por filme: no MUBI × fora, por faixa')
    for t in TIERS:
        pf = r[r.tier == t].groupby(['imdb_id', 'mubi', 'streaming']).centered.mean().reset_index()
        a, b = pf[pf.mubi].centered, pf[~pf.mubi].centered
        s1, s0 = pf[pf.streaming].centered, pf[~pf.streaming].centered
        p = mannwhitneyu(a, b).pvalue if len(a) > 5 else float('nan')
        out.append({'tier': t, 'mubi': float(a.mean()), 'nao_mubi': float(b.mean()), 'n_mubi': int(len(a)), 'p': float(p),
                    'streaming': float(s1.mean()), 'sem_streaming': float(s0.mean())})
        print(f'  {t:16} MUBI {a.mean():+.3f} ({len(a):,}) × fora {b.mean():+.3f} ({len(b):,}) · diferença {a.mean() - b.mean():+.3f} · p={p:.1e}'
              f' | streaming {s1.mean():+.3f} × sem {s0.mean():+.3f}')
    res['H10a'] = out

    B = os.path.join(RAW, 'grouplens', 'ml_belief_2024_data_release_2', 'data_release')
    b = pd.read_csv(os.path.join(B, 'belief_data.csv'), usecols=['userId', 'movieId', 'isSeen', 'userPredictRating'])
    b = b[(b.isSeen == 0) & b.userPredictRating.notna()].merge(links[['movieId', 'imdb_id']], on='movieId').join(F, on='imdb_id', how='inner')
    b = b[(b.userPredictRating >= 4) | (b.userPredictRating <= 2)]
    y = (b.userPredictRating >= 4).astype(int)
    base = pd.DataFrame({'votes': np.log10(b.votes + 1)})
    aw = base.assign(wins=np.log1p(b.wins), noms=np.log1p(b.noms), fest=b.fest_win.astype(int), oscar=b.oscar_nom.astype(int))
    pl = aw.assign(n_platforms=b.n_platforms, mubi=b.mubi.astype(int), streaming=b.streaming.astype(int))
    auc = lambda X: cross_val_score(LogisticRegression(max_iter=2000), X, y, cv=5, scoring='roc_auc').mean()
    a0, a1, a2 = auc(base), auc(aw), auc(pl)
    a3 = auc(pd.DataFrame({'n_platforms': b.n_platforms, 'mubi': b.mubi.astype(int), 'streaming': b.streaming.astype(int)}))
    print(f'\n== H10b · Prever "quero ver" × "não quero" (Beliefs, {len(b):,} opiniões): votos {a0:.3f} · + prêmios {a1:.3f} · '
          f'+ plataformas {a2:.3f} (ganho {a2 - a1:+.3f}) · só plataformas {a3:.3f}')
    res['H10b'] = {'n': int(len(b)), 'auc_votes': float(a0), 'auc_votes_awards': float(a1), 'auc_votes_awards_platforms': float(a2),
                   'auc_platforms_only': float(a3)}
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == '__main__':
    sys.exit(main())
