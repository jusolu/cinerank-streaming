"""
Testes das hipóteses 3 a 6 do relatório de avaliação (as 1 e 2 estão em ingest/ranker_experiments.py).

  H3  Notas infladas: filmes de alguns idiomas (hindi, tâmil, télugo) têm nota no IMDb bem acima da
      nota do MovieLens, o que faz a exploração por "melhor nota geral" escolhê-los para todo mundo.
  H4  Quem vê filmes pouco conhecidos gosta deles tanto quanto dos muito conhecidos
      (nota já descontada da média de cada pessoa).
  H5  O interesse atual (visualizações na Wikipédia) prevê melhor o que a pessoa quer ver
      (MovieLens Beliefs) do que os votos acumulados no IMDb.
  H6  Quanto mais distante o filme do gosto da pessoa, mais ele parece "diferente do habitual",
      sem perder a satisfação (MovieLens Serendipity 2018).

Resultados em data/raw/hypotheses.json.
    .venv-ml/Scripts/python -m ingest.hypotheses
"""
import json
import os
import sys

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu, spearmanr
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import normalize

from ingest.db import BASE_DIR, connect
from ingest.vectors import ML_DIR

RAW = os.path.join(BASE_DIR, 'data', 'raw')
OUT = os.path.join(RAW, 'hypotheses.json')
LANG_PT = {'en': 'inglês', 'hi': 'hindi', 'ta': 'tâmil', 'te': 'télugo', 'ml': 'malaiala', 'ko': 'coreano',
           'ja': 'japonês', 'fr': 'francês', 'es': 'espanhol', 'it': 'italiano', 'de': 'alemão', 'tr': 'turco',
           'zh': 'chinês', 'pt': 'português', 'ru': 'russo', 'fa': 'persa', 'kn': 'canarês', 'mr': 'marata'}
TIERS = ['Pouco conhecido', 'Médio', 'Muito conhecido']


def films(conn):
    df = pd.DataFrame(conn.execute(
        'SELECT m.imdb_id, m.title, m.imdb_rating, m.imdb_votes, f.tier, f.fame, f.pageviews, f.sitelinks, d.original_language '
        'FROM movies m JOIN movie_fame f USING(imdb_id) LEFT JOIN movie_dump d USING(imdb_id)').fetchall(),
        columns=['imdb_id', 'title', 'imdb_rating', 'votes', 'tier', 'fame', 'pageviews', 'sitelinks', 'lang'])
    return df.drop_duplicates('imdb_id').set_index('imdb_id')


def ml_ratings():
    links = pd.read_csv(os.path.join(ML_DIR, 'links.csv'), dtype={'imdbId': str})
    links['imdb_id'] = 'tt' + links['imdbId'].str.zfill(7)
    r = pd.read_csv(os.path.join(ML_DIR, 'ratings.csv'), engine='pyarrow', usecols=['userId', 'movieId', 'rating'])
    return r.merge(links[['movieId', 'imdb_id']], on='movieId'), links


def h3(F, r):
    g = r.groupby('imdb_id').rating.agg(['mean', 'size'])
    df = F.join(g, how='inner')
    df = df[(df['size'] >= 30) & df.imdb_rating.notna()]
    df['gap'] = df.imdb_rating / 2 - df['mean']  # as duas em escala 0–5
    by = df.groupby('lang').agg(filmes=('gap', 'size'), gap=('gap', 'mean'), imdb=('imdb_rating', 'mean'), ml=('mean', 'mean'))
    by = by[by.filmes >= 20].sort_values('gap', ascending=False)
    by.index = [LANG_PT.get(i, i) for i in by.index]
    indic = df[df.lang.isin(['hi', 'ta', 'te', 'ml', 'kn'])].gap
    rest = df[~df.lang.isin(['hi', 'ta', 'te', 'ml', 'kn'])].gap
    p = mannwhitneyu(indic, rest).pvalue
    top = df[df.lang.isin(['hi', 'ta', 'te', 'ml'])].sort_values('gap', ascending=False).head(6)
    print('\n== H3 · Nota do IMDb (÷2) menos nota média do MovieLens, por idioma original (filmes com 30+ notas no ML)')
    print(by.round(2).head(12).to_string())
    print(f'   idiomas indianos: diferença média {indic.mean():+.2f} contra {rest.mean():+.2f} nos demais '
          f'({len(indic)} e {len(rest):,} filmes; Mann-Whitney p={p:.1e})')
    return {'by_lang': by.round(3).reset_index(names='idioma').to_dict('records'), 'indian_gap': float(indic.mean()),
            'other_gap': float(rest.mean()), 'n_indian': int(len(indic)), 'n_other': int(len(rest)), 'p': float(p),
            'examples': [{'title': t, 'imdb': float(a), 'ml': round(float(b), 2), 'n_ml': int(n)}
                         for t, a, b, n in zip(top.title, top.imdb_rating, top['mean'], top['size'])]}


def h4(F, r):
    r = r.join(F[['tier']], on='imdb_id', how='inner')
    r['centered'] = r.rating - r.groupby('userId').rating.transform('mean')
    by = r.groupby('tier').agg(notas=('rating', 'size'), usuarios=('userId', 'nunique'), media=('rating', 'mean'),
                               centrada=('centered', 'mean'), gostou=('rating', lambda x: (x >= 4).mean())).reindex(TIERS)
    # mesma pessoa, faixas diferentes: só quem avaliou filmes das três faixas
    piv = r.groupby(['userId', 'tier']).centered.mean().unstack().dropna()
    diff = piv['Pouco conhecido'] - piv['Muito conhecido']
    print('\n== H4 · Notas do MovieLens por faixa de conhecimento do filme')
    print(by.round(3).to_string())
    print(f'   mesma pessoa ({len(piv):,} usuários com notas nas três faixas): pouco conhecido − muito conhecido = '
          f'{diff.mean():+.3f} estrelas (mediana {diff.median():+.3f}; {(diff > 0).mean():.0%} dos usuários dão mais aos pouco conhecidos)')
    return {'by_tier': by.round(4).reset_index().to_dict('records'), 'paired_users': int(len(piv)),
            'paired_diff_mean': float(diff.mean()), 'paired_diff_median': float(diff.median()),
            'share_users_prefer_less_known': float((diff > 0).mean())}


def h5(F, links):
    B = os.path.join(RAW, 'grouplens', 'ml_belief_2024_data_release_2', 'data_release')
    b = pd.read_csv(os.path.join(B, 'belief_data.csv'), usecols=['userId', 'movieId', 'isSeen', 'userPredictRating', 'userCertainty'])
    b = b[(b.isSeen == 0) & b.userPredictRating.notna()].merge(links[['movieId', 'imdb_id']], on='movieId')
    b = b.join(F[['votes', 'pageviews', 'sitelinks', 'fame']], on='imdb_id', how='inner')
    b['user_mean'] = b.groupby('userId').userPredictRating.transform('mean')
    b['centered'] = b.userPredictRating - b.user_mean
    signals = {'votos IMDb': np.log10(b.votes + 1), 'visualizações 60 dias': np.log10(b.pageviews.fillna(0) + 1),
               'Wikipédias': b.sitelinks.fillna(0), 'índice de conhecimento': b.fame}
    yes = b.userPredictRating >= 4
    no = b.userPredictRating <= 2
    mask = yes | no
    out = {}
    print(f'\n== H5 · O que prevê a expectativa de nota para filmes não vistos (Beliefs, {len(b):,} opiniões, {b.userId.nunique():,} pessoas)')
    for name, s in signals.items():
        rho = spearmanr(s, b.centered).correlation
        auc = roc_auc_score(yes[mask], s[mask])
        out[name] = {'spearman_centered': float(rho), 'auc_want_vs_not': float(auc)}
        print(f'   {name:24} Spearman com a expectativa (descontada a média da pessoa) {rho:+.3f} · AUC quero ver × não quero {auc:.3f}')
    return {'n': int(len(b)), 'users': int(b.userId.nunique()), 'signals': out}


def h6(F):
    S = os.path.join(RAW, 'grouplens', 'serendipity-sac2018', 'serendipity-sac2018')
    a = pd.read_csv(os.path.join(S, 'answers.csv'))
    tr = pd.read_csv(os.path.join(S, 'training.csv'), usecols=['userId', 'movieId', 'rating'], engine='pyarrow')
    links = pd.read_csv(os.path.join(ML_DIR, 'links.csv'), dtype={'imdbId': str})
    links['imdb_id'] = 'tt' + links['imdbId'].str.zfill(7)
    d = np.load(os.path.join(BASE_DIR, 'data', 'reco_vectors.npz'))
    index = {str(i): k for k, i in enumerate(d['ids'])}
    V = normalize(np.hstack([normalize(d['content']), normalize(d['graph'])]))
    m2col = {m: index[i] for m, i in zip(links.movieId, links.imdb_id) if i in index}
    tr = tr[(tr.rating >= 4) & tr.movieId.isin(m2col) & tr.userId.isin(set(a.userId))]
    liked = {u: [m2col[m] for m in g.movieId] for u, g in tr.groupby('userId')}
    rows = []
    for u, mv, *rest in a[['userId', 'movieId']].itertuples(index=False):
        if mv not in m2col or u not in liked:
            continue
        k = m2col[mv]
        hist = [x for x in liked[u] if x != k]
        if len(hist) < 5:
            continue
        prof = V[hist].mean(axis=0)
        prof /= max(np.linalg.norm(prof), 1e-9)
        rows.append((u, mv, 1 - float(V[k] @ prof), float((V[hist] @ V[k]).max())))
    dist = pd.DataFrame(rows, columns=['userId', 'movieId', 'distance', 'max_sim'])
    x = a.merge(dist, on=['userId', 'movieId'])
    x['q'] = pd.qcut(x.distance, 4, labels=['mais perto', '2º quartil', '3º quartil', 'mais longe'])
    cols = {'s5': 'Diferente do habitual', 's4': 'Não acharia sozinho', 's6': 'Surpreso com a sugestão',
            's7': 'Feliz de ter visto', 's8': 'Ampliou os gostos'}
    tab = x.groupby('q', observed=True)[list(cols)].agg(lambda s: (s >= 4).mean()).rename(columns=cols)
    corr = {cols[c]: float(spearmanr(x.distance, x[c], nan_policy='omit').correlation) for c in cols}
    print(f'\n== H6 · Distância do filme ao gosto da pessoa × respostas (Serendipity 2018, {len(x):,} respostas)')
    print(tab.round(2).to_string())
    print('   Spearman com a distância: ' + ', '.join(f'{k} {v:+.2f}' for k, v in corr.items()))
    return {'n': int(len(x)), 'by_quartile': {str(i): {k: float(v) for k, v in row.items()} for i, row in tab.iterrows()},
            'spearman_distance': corr}


def main():
    conn = connect()
    F = films(conn)
    r, links = ml_ratings()
    res = {'H3': h3(F, r), 'H4': h4(F, r), 'H5': h5(F, links), 'H6': h6(F)}
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(f'\n[hipóteses] resultados em {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
