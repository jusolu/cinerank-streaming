"""
Hipótese 9: premiação como sinal. Prêmios e indicações completos do Wikidata (ingest/awards.py).

  H9a  Entre filmes do mesmo nível de conhecimento, os premiados recebem notas melhores no MovieLens
       (nota descontada da média de cada pessoa) — sobretudo os menos conhecidos.
  H9b  Prêmios ajudam a prever o que a pessoa quer ver (MovieLens Beliefs) além do número de votos.
  H9c  Quantos filmes menos conhecidos têm prêmio de festival ou da crítica (candidatos a uma vaga
       "premiado pouco conhecido").

Resultados em data/raw/hypotheses_awards.json.
    .venv-ml/Scripts/python -m ingest.hypotheses_awards
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
from ingest.vectors import ML_DIR

RAW = os.path.join(BASE_DIR, 'data', 'raw')
OUT = os.path.join(RAW, 'hypotheses_awards.json')
TIERS = ['Pouco conhecido', 'Médio', 'Muito conhecido']
PRESTIGE = ['oscar', 'globo_de_ouro', 'bafta', 'cannes', 'berlim', 'veneza', 'sundance', 'criticos', 'sindicatos', 'festivais', 'nacionais']
FEST = ['cannes', 'berlim', 'veneza', 'sundance', 'festivais', 'criticos']


def film_awards(conn):
    F = pd.DataFrame(conn.execute('SELECT imdb_id, tier, votes FROM movie_fame').fetchall(), columns=['imdb_id', 'tier', 'votes']).set_index('imdb_id')
    a = pd.DataFrame(conn.execute('SELECT imdb_id, kind, category FROM movie_award').fetchall(), columns=['imdb_id', 'kind', 'category'])
    a = a[a.category.isin(PRESTIGE)]
    F['wins'] = a[a.kind == 'win'].groupby('imdb_id').size().reindex(F.index).fillna(0)
    F['noms'] = a[a.kind == 'nom'].groupby('imdb_id').size().reindex(F.index).fillna(0)
    F['fest_win'] = a[(a.kind == 'win') & a.category.isin(FEST)].groupby('imdb_id').size().reindex(F.index).fillna(0) > 0
    F['oscar_nom'] = a[a.category == 'oscar'].groupby('imdb_id').size().reindex(F.index).fillna(0) > 0
    F['awarded'] = F.wins > 0
    F['recognized'] = (F.wins + F.noms) > 0
    return F


def main():
    conn = connect()
    F = film_awards(conn)
    res = {}

    # H9c — oferta
    print('== H9c · Filmes premiados ou indicados (prêmios de prestígio), por faixa')
    sup = F.groupby('tier').agg(filmes=('wins', 'size'), premiados=('awarded', 'sum'), reconhecidos=('recognized', 'sum'),
                                festival_ou_critica=('fest_win', 'sum')).reindex(TIERS)
    print(sup.to_string())
    res['H9c'] = sup.reset_index().to_dict('records')

    # H9a — notas do MovieLens
    links = pd.read_csv(os.path.join(ML_DIR, 'links.csv'), dtype={'imdbId': str})
    links['imdb_id'] = 'tt' + links['imdbId'].str.zfill(7)
    r = pd.read_csv(os.path.join(ML_DIR, 'ratings.csv'), engine='pyarrow', usecols=['userId', 'movieId', 'rating'])
    r = r.merge(links[['movieId', 'imdb_id']], on='movieId').join(F[['tier', 'awarded', 'fest_win']], on='imdb_id', how='inner')
    r['centered'] = r.rating - r.groupby('userId').rating.transform('mean')
    print('\n== H9a · Nota no MovieLens (descontada a média de cada pessoa): premiados × não premiados, por faixa')
    out = []
    for t in TIERS:
        sub = r[r.tier == t]
        # por filme, para não pesar os filmes muito votados
        pf = sub.groupby(['imdb_id', 'awarded']).centered.mean().reset_index()
        a, b = pf[pf.awarded].centered, pf[~pf.awarded].centered
        p = mannwhitneyu(a, b).pvalue if len(a) > 5 else None
        lk = sub.groupby('awarded').rating.apply(lambda x: (x >= 4).mean())
        row = {'tier': t, 'filmes_premiados': int(len(a)), 'filmes_sem': int(len(b)), 'nota_premiados': float(a.mean()),
               'nota_sem': float(b.mean()), 'diferenca': float(a.mean() - b.mean()), 'p': None if p is None else float(p),
               'gostou_premiados': float(lk.get(True, np.nan)), 'gostou_sem': float(lk.get(False, np.nan))}
        out.append(row)
        print(f'  {t:16} premiados {a.mean():+.3f} ({len(a):,} filmes) × sem prêmio {b.mean():+.3f} ({len(b):,}) · '
              f'diferença {a.mean() - b.mean():+.3f} estrelas · p={p:.1e} · gostou {lk.get(True, 0):.0%} × {lk.get(False, 0):.0%}')
    res['H9a'] = out

    # H9b — Beliefs: votos × votos + prêmios
    B = os.path.join(RAW, 'grouplens', 'ml_belief_2024_data_release_2', 'data_release')
    b = pd.read_csv(os.path.join(B, 'belief_data.csv'), usecols=['userId', 'movieId', 'isSeen', 'userPredictRating'])
    b = b[(b.isSeen == 0) & b.userPredictRating.notna()].merge(links[['movieId', 'imdb_id']], on='movieId')
    b = b.join(F, on='imdb_id', how='inner')
    b = b[(b.userPredictRating >= 4) | (b.userPredictRating <= 2)]
    y = (b.userPredictRating >= 4).astype(int)
    X1 = np.log10(b[['votes']] + 1)
    X2 = X1.assign(wins=np.log1p(b.wins), noms=np.log1p(b.noms), fest=b.fest_win.astype(int), oscar=b.oscar_nom.astype(int))
    auc1 = cross_val_score(LogisticRegression(max_iter=1000), X1, y, cv=5, scoring='roc_auc').mean()
    auc2 = cross_val_score(LogisticRegression(max_iter=1000), X2, y, cv=5, scoring='roc_auc').mean()
    auc_aw = cross_val_score(LogisticRegression(max_iter=1000), X2.drop(columns='votes'), y, cv=5, scoring='roc_auc').mean()
    print(f'\n== H9b · Prever "quero ver" × "não quero" (Beliefs, {len(b):,} opiniões, validação cruzada)')
    print(f'   só votos AUC {auc1:.3f} · só prêmios {auc_aw:.3f} · votos + prêmios {auc2:.3f} (ganho {auc2 - auc1:+.3f})')
    res['H9b'] = {'n': int(len(b)), 'auc_votes': float(auc1), 'auc_awards_only': float(auc_aw), 'auc_votes_awards': float(auc2)}
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(f'\n[prêmios] resultados em {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
