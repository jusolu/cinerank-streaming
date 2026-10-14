"""
O que torna uma recomendação surpreendente e bem-vinda? Análise do MovieLens Serendipity 2018
(Kotkov et al., SAC 2018) cruzada com o índice de conhecimento (ingest/fame.py).

Cada resposta é uma pessoa avaliando um filme que gostou (nota ≥ 3,5) com afirmações de 1 a 5:
  s1 ouvi falar pela primeira vez pelo MovieLens   s5 é diferente do que costumo ver
  s4 não acharia sozinho                           s7 estou feliz de ter visto
  s8 ampliou meus gostos                           s_ser_* serendipidade (combinações das anteriores)

Uso (.venv-ml):
    .venv-ml/Scripts/python -m ingest.serendipity
"""
import json
import os
import sys

import pandas as pd

from ingest.db import BASE_DIR, connect
from ingest.vectors import ML_DIR

S = os.path.join(BASE_DIR, 'data', 'raw', 'grouplens', 'serendipity-sac2018', 'serendipity-sac2018')
REPORT = os.path.join(BASE_DIR, 'data', 'raw', 'serendipity_report.json')
TIERS = ['Pouco conhecido', 'Médio', 'Muito conhecido']
LABEL = {'s1': 'Conheceu pelo MovieLens', 's4': 'Não acharia sozinho', 's5': 'Diferente do habitual',
         's6': 'Ficou surpreso com a sugestão', 's7': 'Feliz de ter visto', 's8': 'Ampliou os gostos'}


def main():
    a = pd.read_csv(os.path.join(S, 'answers.csv'))
    links = pd.read_csv(os.path.join(ML_DIR, 'links.csv'), dtype={'imdbId': str})
    a = a.merge(links[['movieId', 'imdbId']], on='movieId', how='left')
    a['imdb_id'] = 'tt' + a['imdbId'].str.zfill(7)
    conn = connect()
    fame = pd.DataFrame(conn.execute('SELECT imdb_id, fame, tier FROM movie_fame').fetchall(),
                        columns=['imdb_id', 'fame', 'tier']).set_index('imdb_id')
    a = a.join(fame, on='imdb_id')
    a['tier'] = a['tier'].fillna('Fora do catálogo (<500 votos ou antes de 1990)')
    a['any_ser'] = a[[c for c in a.columns if c.startswith('s_ser_')]].any(axis=1)

    print(f'== {len(a):,} respostas, {a.userId.nunique()} pessoas, {a.movieId.nunique():,} filmes\n')
    order = TIERS + ['Fora do catálogo (<500 votos ou antes de 1990)']
    g = a.groupby('tier')
    table = pd.DataFrame({'respostas': g.size(), 'serendipidade estrita': g['any_ser'].mean(),
                          **{LABEL[k]: g[k].apply(lambda x: (x >= 4).mean()) for k in LABEL}}).reindex(order)
    print('Proporção que concorda (4 ou 5) por faixa de conhecimento:')
    print(table.round(2).to_string())
    corr = a[['fame'] + list(LABEL)].corr(method='spearman')['fame'].drop('fame').rename(index=LABEL)
    print('\nCorrelação com o índice de conhecimento (Spearman):\n' + corr.round(2).to_string())
    glad = a.groupby(a['s4'] >= 4)['s7'].apply(lambda x: (x >= 4).mean())
    print(f'\nFeliz de ter visto: {glad.get(True, 0):.0%} quando "não acharia sozinho" vs {glad.get(False, 0):.0%} nos demais')
    with open(REPORT, 'w', encoding='utf-8') as f:
        json.dump({'answers': int(len(a)), 'users': int(a.userId.nunique()),
                   'by_tier': {t: {k: (None if pd.isna(v) else float(v)) for k, v in row.items()} for t, row in table.iterrows()},
                   'corr_fame': {k: float(v) for k, v in corr.items()},
                   'glad_if_not_findable': float(glad.get(True, 0)), 'glad_otherwise': float(glad.get(False, 0))},
                  f, ensure_ascii=False, indent=2)
    return 0


if __name__ == '__main__':
    sys.exit(main())
