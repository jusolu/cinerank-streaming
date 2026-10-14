"""
Sinais por filme usados pelo site (recommender.py), exportados de data/cinerank.db para
data/film_signals.npz: faixa e índice de conhecimento (ingest/fame.py), prêmios e indicações de
prestígio (ingest/awards.py), curadoria do MUBI (ingest/xrefs.py) e franquia (Wikidata).

    .venv-ml/Scripts/python -m ingest.signals
"""
import os
import sys

import numpy as np

from ingest.db import BASE_DIR, connect
from ingest.hypotheses_awards import film_awards

OUT = os.path.join(BASE_DIR, 'data', 'film_signals.npz')
TIER_CODE = {'Pouco conhecido': 0, 'Médio': 1, 'Muito conhecido': 2}


def main():
    conn = connect()
    A = film_awards(conn)
    mubi = {r[0] for r in conn.execute("SELECT imdb_id FROM movie_xref WHERE platform = 'mubi'")}
    franchise = {r[0] for r in conn.execute("SELECT imdb_id FROM movie_wikidata WHERE prop = 'franchise'")}
    ids = A.index.to_numpy(dtype=str)
    np.savez_compressed(
        OUT, ids=ids,
        tier=np.array([TIER_CODE.get(t, -1) for t in A.tier], dtype=np.int8),
        wins=A.wins.to_numpy(dtype=np.int16), noms=A.noms.to_numpy(dtype=np.int16),
        mubi=np.array([i in mubi for i in ids]), franchise=np.array([i in franchise for i in ids]))
    q = (A.wins + A.noms > 0) | np.array([i in mubi for i in ids])
    print(f'[sinais] {len(ids):,} filmes · {int(q.sum()):,} com prêmio, indicação ou MUBI · '
          f'{os.path.getsize(OUT) / 1e3:.0f} KB em {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
