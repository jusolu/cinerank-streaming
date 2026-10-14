"""
PCA e clusters dos filmes do catálogo, a partir dos vetores da recomendação.

Usa os vetores de conteúdo (sinopse) e de grafo (equipe, temas, estúdio, universo), que todo filme
tem. O gosto coletivo fica de fora porque só existe para ~30% dos filmes e dividiria o catálogo
entre "tem" e "não tem". Passos:
  1. PCA: quantos componentes explicam 80% da variação e o que separa os dois primeiros.
  2. KMeans sobre os componentes; k escolhido pela silhueta numa amostra.
  3. Nome de cada cluster pelos gêneros, década e percepções que mais o distinguem,
     com a mistura de faixas de conhecimento (ingest/fame.py).

Grava movie_cluster (imdb_id, cluster, name, pc1, pc2) e data/raw/film_clusters.json (para o relatório).
    .venv-ml/Scripts/python -m ingest.film_clusters
"""
import json
import os
import sys

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import normalize

from ingest.db import BASE_DIR, connect
from ingest.personas import GENRE_PT

VECTORS = os.path.join(BASE_DIR, 'data', 'reco_vectors.npz')
OUT = os.path.join(BASE_DIR, 'data', 'raw', 'film_clusters.json')
SEED = 5
LANG_PT = {'tr': 'turco', 'hi': 'hindi', 'ja': 'japonês', 'ko': 'coreano', 'fr': 'francês', 'es': 'espanhol',
           'it': 'italiano', 'de': 'alemão', 'zh': 'chinês', 'cn': 'cantonês', 'ta': 'tâmil', 'te': 'télugo',
           'ml': 'malaiala', 'pt': 'português', 'ru': 'russo', 'fa': 'persa', 'sv': 'sueco', 'da': 'dinamarquês',
           'th': 'tailandês', 'pl': 'polonês', 'mr': 'marata', 'bn': 'bengali', 'id': 'indonésio', 'tl': 'tagalo'}


def main():
    conn = connect()
    d = np.load(VECTORS, allow_pickle=False)
    ids = [str(i) for i in d['ids']]
    # cada parte com norma 1 (sem padronizar dimensão a dimensão: isso apagaria a ordem de importância dos eixos)
    X = np.hstack([normalize(d['content']), normalize(d['graph'])])

    pca = PCA(n_components=60, random_state=SEED).fit(X)
    cum = np.cumsum(pca.explained_variance_ratio_)
    n80 = int(np.searchsorted(cum, 0.80) + 1) if cum[-1] >= 0.8 else 60
    Z = pca.transform(X)[:, :min(n80, 60)]
    print(f'== PCA: PC1 {pca.explained_variance_ratio_[0]:.1%}, PC2 {pca.explained_variance_ratio_[1]:.1%}, '
          f'10 primeiros {cum[9]:.1%}, {min(n80, 60)} componentes para {min(cum[-1], 0.8):.0%}')

    rng = np.random.default_rng(SEED)
    sample = rng.choice(len(ids), 8000, replace=False)
    sil = {k: silhouette_score(Z[sample], KMeans(k, n_init=6, random_state=SEED).fit(Z[sample]).labels_)
           for k in range(8, 25, 2)}
    k = max(sil, key=sil.get)
    km = KMeans(k, n_init=10, random_state=SEED).fit(Z)
    labels = km.labels_
    print(f'== KMeans: k={k} (silhueta {sil[k]:.3f}; testados {", ".join(f"{a}:{b:.3f}" for a, b in sil.items())})')

    films = pd.DataFrame(conn.execute('SELECT imdb_id, title, year, imdb_votes FROM movies').fetchall(),
                         columns=['imdb_id', 'title', 'year', 'votes']).set_index('imdb_id').reindex(ids)
    films['cluster'] = labels
    films['pc1'], films['pc2'] = Z[:, 0], Z[:, 1]
    tier = dict(conn.execute('SELECT imdb_id, tier FROM movie_fame').fetchall()) if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'movie_fame'").fetchone() else {}
    films['tier'] = [tier.get(i) for i in ids]
    G = pd.crosstab(*zip(*conn.execute('SELECT imdb_id, genre FROM movie_genres').fetchall())).reindex(ids).fillna(0).clip(upper=1)
    G = G[[g for g in G.columns if g in GENRE_PT]]
    tags = conn.execute('SELECT imdb_id, tag FROM movie_tags').fetchall()
    T = pd.crosstab(*zip(*tags)).reindex(ids).fillna(0).clip(upper=1)
    lang = dict(conn.execute('SELECT imdb_id, original_language FROM movie_dump').fetchall())
    films['foreign'] = [float(bool(lang.get(i)) and lang.get(i) != 'en') for i in ids]

    clusters = []
    base_g, base_t, base_f = G.mean(), T.mean(), films['foreign'].mean()
    for c in range(k):
        m = films.cluster == c
        share = G[m.values].mean()
        lift = (share / base_g).where(share > 0.12).dropna().sort_values(ascending=False)
        top = [GENRE_PT[g] for g in lift.index[:2]] or ['vários gêneros']
        dec = int(films.loc[m, 'year'].median() // 10 * 10)
        era = f'anos {dec}' if films.loc[m, 'year'].quantile(.75) - films.loc[m, 'year'].quantile(.25) < 15 else 'várias épocas'
        tl = (T[m.values].mean() / base_t).where(T[m.values].mean() > 0.01).dropna().sort_values(ascending=False)
        foreign = films.loc[m, 'foreign'].mean()
        langs = pd.Series([lang.get(i) for i in films.index[m.values]]).value_counts(normalize=True)
        lang_name = next((LANG_PT[l] for l, sh in langs.items() if l != 'en' and sh > 0.3 and l in LANG_PT), None)
        name = ' e '.join(top).capitalize() + f', {era}' + (f' · {lang_name}' if lang_name
                                                               else ' · internacional' if foreign > base_f + 0.2 else '')
        if tl.size and name in {x['name'] for x in clusters}:
            name += f' · {tl.index[0].lower()}'
        sub = films[m].sort_values('votes', ascending=False)
        mix = sub['tier'].value_counts(normalize=True).round(3).to_dict()
        clusters.append({'id': c, 'name': name, 'films': int(m.sum()),
                         'recommendable': int((sub['votes'] >= 20000).sum()),
                         'median_votes': int(sub['votes'].median()), 'foreign': round(float(foreign), 2),
                         'tags': [t.lower() for t in tl.index[:3]], 'tiers': mix,
                         'examples': sub['title'].head(5).tolist(), 'example_ids': sub.index[:5].tolist()})
    clusters.sort(key=lambda x: -x['films'])
    print(f'\n{"cluster":46}{"filmes":>8}{"recom.":>8}{"votos med.":>12}  pouco/médio/muito')
    for x in clusters:
        t = x['tiers']
        print(f'{x["id"]:>2} {x["name"][:43]:43}{x["films"]:8,}{x["recommendable"]:8,}{x["median_votes"]:12,}  '
              f'{t.get("Pouco conhecido", 0):.0%}/{t.get("Médio", 0):.0%}/{t.get("Muito conhecido", 0):.0%}  · {"; ".join(x["examples"][:3])}')

    names = {x['id']: x['name'] for x in clusters}
    with conn:
        conn.execute('CREATE TABLE IF NOT EXISTS movie_cluster (imdb_id TEXT PRIMARY KEY, cluster INTEGER, name TEXT, pc1 REAL, pc2 REAL)')
        conn.execute('DELETE FROM movie_cluster')
        conn.executemany('INSERT INTO movie_cluster VALUES (?, ?, ?, ?, ?)',
                         [(i, int(r.cluster), names[int(r.cluster)], float(r.pc1), float(r.pc2)) for i, r in films.iterrows()])
    pts = films.sample(3000, random_state=SEED)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump({'k': k, 'silhouette': {str(a): round(b, 4) for a, b in sil.items()},
                   'variance': [round(float(v), 4) for v in pca.explained_variance_ratio_[:20]],
                   'n_components_80': int(min(n80, 60)), 'clusters': clusters,
                   'points': [[round(float(r.pc1), 2), round(float(r.pc2), 2), int(r.cluster), r.tier, r.title]
                              for _, r in pts.iterrows()]}, f, ensure_ascii=False)
    print(f'\n[clusters] gravado em movie_cluster e {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
