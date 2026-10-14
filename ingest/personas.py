"""
Personas de teste: grupos de usuários reais e anônimos do MovieLens com gosto parecido.

Cada usuário é descrito pelo que curte (gêneros, época, popularidade, idioma, percepções do
público como "sombrio" ou "engraçado") e pelo jeito de avaliar (exigente ou generoso, quantos
filmes viu). O KMeans separa ~24 personas. Cada uma ganha um nome, traços, filmes-assinatura
(curtidos muito mais por ela do que pela média) e filmes-semente para testes manuais.

Saídas:
  data/personas.json               definição das personas (agregada, vai para o git)
  data/raw/persona_users.parquet   notas dos usuários de cada persona (local; licença do MovieLens)

Uso (.venv-ml):
    .venv-ml/Scripts/python -m ingest.personas --personas 24 --users 6000
"""
import argparse
import json
import os
import sys
from collections import Counter

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

from ingest.db import BASE_DIR, connect
from ingest.ranker import load_ratings

OUT = os.path.join(BASE_DIR, 'data', 'personas.json')
USERS_OUT = os.path.join(BASE_DIR, 'data', 'raw', 'persona_users.parquet')
SEED = 23
MIN_VOTES = 20000  # mesmo universo de filmes do site
GENRE_PT = {'Action': 'ação', 'Adventure': 'aventura', 'Animation': 'animação', 'Biography': 'biografias',
            'Comedy': 'comédia', 'Crime': 'crime', 'Documentary': 'documentários', 'Drama': 'drama',
            'Family': 'família', 'Fantasy': 'fantasia', 'History': 'história', 'Horror': 'terror',
            'Music': 'música', 'Musical': 'musicais', 'Mystery': 'mistério', 'Romance': 'romance',
            'Sci-Fi': 'ficção científica', 'Sport': 'esporte', 'Thriller': 'suspense', 'War': 'guerra',
            'Western': 'faroeste'}
TAGS = ['Sombrio', 'Engraçado', 'Faz pensar', 'Atmosférico', 'Violento', 'Romântico', 'Aconchegante',
        'Mexe com a cabeça', 'Visual marcante', 'Para se sentir bem', 'Psicológico', 'Cult']


def film_info(conn):
    films = pd.DataFrame(conn.execute('SELECT imdb_id, title, year, imdb_votes FROM movies').fetchall(),
                         columns=['imdb_id', 'title', 'year', 'votes']).set_index('imdb_id')
    lang = dict(conn.execute('SELECT imdb_id, original_language FROM movie_dump').fetchall())
    films['foreign'] = [float(bool(lang.get(i)) and lang.get(i) != 'en') for i in films.index]
    genres = pd.DataFrame(conn.execute('SELECT imdb_id, genre FROM movie_genres').fetchall(), columns=['imdb_id', 'genre'])
    tags = pd.DataFrame(conn.execute('SELECT imdb_id, tag FROM movie_tags').fetchall(), columns=['imdb_id', 'tag'])
    return films, genres, tags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--personas', type=int, default=24)
    ap.add_argument('--users', type=int, default=6000)
    args = ap.parse_args()
    conn = connect()
    films, genres, tags = film_info(conn)
    ids = list(films.index)
    index = {iid: k for k, iid in enumerate(ids)}

    r = load_ratings(index)
    r['imdb_id'] = np.array(ids)[r['col'].to_numpy()]
    r_all = r[['userId', 'imdb_id', 'rating']]  # todas as notas no catálogo (para simular filmes menos conhecidos)
    r = r[r['imdb_id'].map(films['votes']).fillna(0).to_numpy() >= MIN_VOTES]
    counts = r.groupby('userId').size()
    rng = np.random.default_rng(SEED)
    eligible = counts[(counts >= 40) & (counts <= 1500)].index.to_numpy()
    chosen = rng.choice(eligible, size=min(args.users, len(eligible)), replace=False)
    r = r[r['userId'].isin(set(chosen.tolist()))][['userId', 'imdb_id', 'rating']]
    print(f'[personas] {r.userId.nunique():,} usuários, {len(r):,} notas', flush=True)

    # ---- traços por usuário
    likes = r[r.rating >= 4]
    top_genres = [g for g in genres.genre.value_counts().index if g in GENRE_PT][:16]
    G = pd.crosstab(genres.imdb_id, genres.genre).reindex(columns=top_genres, fill_value=0).clip(upper=1)
    T = pd.crosstab(tags.imdb_id, tags.tag).reindex(columns=TAGS, fill_value=0).clip(upper=1)
    lk = likes.join(G, on='imdb_id').join(T, on='imdb_id').fillna(0)
    lk = lk.join(films[['year', 'votes', 'foreign']], on='imdb_id')
    lk['logv'] = np.log10(lk['votes'] + 1)
    g = lk.groupby('userId')
    feats = pd.concat([
        g[top_genres].mean(), g[TAGS].mean().add_prefix('tag_'),
        g['year'].median().rename('year'), g['year'].std().fillna(0).rename('year_spread'),
        g['logv'].median().rename('mainstream'), g['foreign'].mean().rename('foreign'),
        r.groupby('userId').rating.mean().rename('mean_rating'),
        np.log10(r.groupby('userId').size()).rename('activity'),
    ], axis=1).dropna()
    X = StandardScaler().fit_transform(feats)
    # pesos: gosto (gêneros/percepções) conta mais que o jeito de avaliar
    w = np.array([1.0] * len(top_genres) + [0.6] * len(TAGS) + [1.2, 0.5, 1.0, 1.0, 0.8, 0.5])
    km = KMeans(args.personas, n_init=20, random_state=SEED).fit(X * w)
    feats['persona'] = km.labels_
    Z = pd.DataFrame(X, index=feats.index, columns=feats.columns[:-1]).groupby(feats['persona']).mean()

    # ---- filmes-assinatura: curtidos por muito mais gente da persona do que da média
    liked_by = likes.merge(feats[['persona']], left_on='userId', right_index=True)
    n_users = feats.groupby('persona').size()
    share_all = liked_by.groupby('imdb_id').userId.nunique() / len(feats)

    personas, used = [], Counter()
    for p in range(args.personas):
        z = Z.loc[p]
        gz = z[top_genres].sort_values(ascending=False)
        main = [GENRE_PT[x] for x in gz.index[:2] if gz[x] > 0.25] or ['de tudo um pouco']
        era = 'clássicos' if z['year'] < -0.6 else 'lançamentos' if z['year'] > 0.6 else None
        traits = []
        if z['mainstream'] > 0.5:
            traits.append('blockbusters')
        if z['mainstream'] < -0.5:
            traits.append('fora do circuito')
        if z['foreign'] > 0.6:
            traits.append('cinema internacional')
        if z['mean_rating'] < -0.6:
            traits.append('exigente')
        if z['mean_rating'] > 0.6:
            traits.append('generoso nas notas')
        if z['activity'] > 0.7:
            traits.append('cinéfilo de muitas horas')
        tz = z[[f'tag_{t}' for t in TAGS]].sort_values(ascending=False)
        mood = [t[4:].lower() for t in tz.index[:2] if tz[t] > 0.4]
        name = 'Fã de ' + ' e '.join(main) + (f', {era}' if era else '')
        used[name] += 1
        if used[name] > 1:
            name += f' ({", ".join(traits[:1] or mood[:1] or [str(used[name])])})'
        sub = liked_by[liked_by.persona == p]
        share_p = sub.groupby('imdb_id').userId.nunique() / n_users[p]
        lift = (share_p / share_all.reindex(share_p.index)).where(share_p >= 0.15).dropna().sort_values(ascending=False)
        signature = [films.at[i, 'title'] for i in lift.index[:8]]
        seeds = list(lift.index[:6])  # os mais característicos, não os populares que todo mundo curte
        personas.append({
            'id': f'P{p + 1:02d}', 'name': name, 'users': int(n_users[p]),
            'traits': traits, 'moods': mood,
            'profile': {'gêneros': {GENRE_PT[x]: round(float(gz[x]), 2) for x in gz.index[:4]},
                        'ano_mediano_curtidos': int(feats[feats.persona == p]['year'].median()),
                        'nota_media': round(float(feats[feats.persona == p]['mean_rating'].mean()), 2),
                        'internacional': round(float(feats[feats.persona == p]['foreign'].mean()), 2)},
            'signature': signature,
            'signature_ids': list(lift.index[:8]),
            'seed_likes': list(dict.fromkeys(seeds))[:6],
        })
    personas.sort(key=lambda x: -x['users'])
    pid = {int(x['id'][1:]) - 1: x['id'] for x in personas}

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump({'source': 'MovieLens 32M (usuários anônimos)', 'min_votes': MIN_VOTES, 'personas': personas},
                  f, ensure_ascii=False, indent=2)
    users = r_all.merge(feats['persona'].map(pid).rename('persona'), left_on='userId', right_index=True)
    users.to_parquet(USERS_OUT, index=False)
    print(f'[personas] {len(personas)} personas em {OUT}; notas dos usuários em {USERS_OUT}\n')
    for x in personas:
        print(f'{x["id"]} {x["users"]:4}  {x["name"]}' + (f' · {", ".join(x["traits"] + x["moods"])}' if x['traits'] or x['moods'] else ''))
        print(f'          ex.: {"; ".join(x["signature"][:4])}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
