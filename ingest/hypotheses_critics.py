"""
Hipótese 11: a nota da crítica (Metacritic e Rotten Tomatoes, extraídas da Wikipédia por
ingest/wiki_fields.py) prevê o gosto do público melhor do que a nota do IMDb, sobretudo nos filmes
menos conhecidos, onde a nota do IMDb tem poucos votos e pode ser inflada (hipótese 3).

  H11a  Correlação, por filme, entre a nota média no MovieLens (descontada a média de cada pessoa)
        e: nota do IMDb, Metacritic, Rotten Tomatoes; por faixa de conhecimento.
  H11b  Prever "quero ver" (Beliefs): votos + prêmios, com e sem a nota da crítica.

    .venv-ml/Scripts/python -m ingest.hypotheses_critics
"""
import json
import os
import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score

from ingest.db import BASE_DIR, connect
from ingest.hypotheses_awards import TIERS, film_awards
from ingest.vectors import ML_DIR

OUT = os.path.join(BASE_DIR, 'data', 'raw', 'hypotheses_critics.json')


def main():
    conn = connect()
    F = film_awards(conn)
    w = pd.DataFrame(conn.execute('SELECT w.imdb_id, w.rt, w.metacritic, m.imdb_rating FROM movie_wikifields w JOIN movies m USING(imdb_id)').fetchall(),
                     columns=['imdb_id', 'rt', 'metacritic', 'imdb_rating']).set_index('imdb_id')
    F = F.join(w, how='left')
    F['imdb_rating'] = F['imdb_rating'].fillna(pd.Series(dict(conn.execute('SELECT imdb_id, imdb_rating FROM movies').fetchall())))
    links = pd.read_csv(os.path.join(ML_DIR, 'links.csv'), dtype={'imdbId': str})
    links['imdb_id'] = 'tt' + links['imdbId'].str.zfill(7)
    r = pd.read_csv(os.path.join(ML_DIR, 'ratings.csv'), engine='pyarrow', usecols=['userId', 'movieId', 'rating'])
    r = r.merge(links[['movieId', 'imdb_id']], on='movieId')
    r['centered'] = r.rating - r.groupby('userId').rating.transform('mean')
    film = r.groupby('imdb_id').agg(ml=('centered', 'mean'), n=('centered', 'size'))
    film = film[film.n >= 10].join(F, how='inner')
    res = {'H11a': []}
    print('== H11a · Spearman entre a nota no MovieLens (descontada a média da pessoa) e cada nota, por faixa (filmes com 10+ notas no ML)')
    for t in TIERS:
        sub = film[film.tier == t]
        both = sub.dropna(subset=['metacritic', 'imdb_rating'])
        row = {'tier': t, 'filmes': int(len(sub)), 'com_metacritic': int(len(both)),
               'imdb_todos': float(spearmanr(sub.imdb_rating, sub.ml, nan_policy='omit').correlation),
               'imdb_mesmos': float(spearmanr(both.imdb_rating, both.ml).correlation) if len(both) > 20 else None,
               'metacritic': float(spearmanr(both.metacritic, both.ml).correlation) if len(both) > 20 else None}
        rt = sub.dropna(subset=['rt'])
        row['rt'] = float(spearmanr(rt.rt, rt.ml).correlation) if len(rt) > 20 else None
        row['com_rt'] = int(len(rt))
        res['H11a'].append(row)
        print(f"  {t:16} IMDb {row['imdb_todos']:+.3f} ({len(sub):,} filmes) · nos {len(both):,} com Metacritic: IMDb "
              f"{row['imdb_mesmos'] if row['imdb_mesmos'] is None else round(row['imdb_mesmos'], 3)} × Metacritic "
              f"{row['metacritic'] if row['metacritic'] is None else round(row['metacritic'], 3)} · RT {row['rt'] if row['rt'] is None else round(row['rt'], 3)} ({len(rt):,})")

    B = os.path.join(BASE_DIR, 'data', 'raw', 'grouplens', 'ml_belief_2024_data_release_2', 'data_release')
    b = pd.read_csv(os.path.join(B, 'belief_data.csv'), usecols=['userId', 'movieId', 'isSeen', 'userPredictRating'])
    b = b[(b.isSeen == 0) & b.userPredictRating.notna()].merge(links[['movieId', 'imdb_id']], on='movieId').join(F, on='imdb_id', how='inner')
    b = b[((b.userPredictRating >= 4) | (b.userPredictRating <= 2)) & b.metacritic.notna()]
    y = (b.userPredictRating >= 4).astype(int)
    X = pd.DataFrame({'votes': np.log10(b.votes + 1), 'wins': np.log1p(b.wins), 'noms': np.log1p(b.noms), 'fest': b.fest_win.astype(int)})
    auc = lambda X: cross_val_score(LogisticRegression(max_iter=2000), X, y, cv=5, scoring='roc_auc').mean()
    a0, a1, a2 = auc(X), auc(X.assign(imdb=b.imdb_rating)), auc(X.assign(imdb=b.imdb_rating, mc=b.metacritic))
    print(f'\n== H11b · "quero ver" × "não quero" (Beliefs, {len(b):,} opiniões em filmes com Metacritic): votos+prêmios {a0:.3f} · '
          f'+ nota IMDb {a1:.3f} · + Metacritic {a2:.3f} (ganho da crítica {a2 - a1:+.3f})')
    res['H11b'] = {'n': int(len(b)), 'auc_votes_awards': float(a0), 'auc_plus_imdb': float(a1), 'auc_plus_metacritic': float(a2)}
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == '__main__':
    sys.exit(main())
