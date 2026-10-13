"""
Classificação dos roteiros e busca de padrões, a partir dos perfis em movie_script.

1. Rótulos por filme (percentis do próprio acervo): "Muito dialogado", "Protagonista dominante"...
2. Estilos de roteiro: KMeans sobre as medidas padronizadas; cada grupo é nomeado pelos traços
   que mais o distinguem da média.
3. Padrões: correlação (Spearman) das medidas com nota e ano, traços típicos de cada gênero e
   nota média por forma de arco emocional.

Grava movie_script_class (imdb_id, style, tags JSON) e imprime o relatório.
    .venv-ml/Scripts/python -m ingest.script_patterns
"""
import json
import sys

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from ingest.db import connect

LABEL = {
    'scenes': 'nº de cenas', 'words_per_scene': 'cenas longas', 'dialog_share': 'diálogo',
    'speakers': 'personagens falantes', 'lead_share': 'peso do protagonista', 'ext_share': 'externas',
    'night_share': 'noite', 'swears_per_1k': 'palavrões', 'final_speedup': 'aceleração final',
    'words_per_speech': 'falas longas', 'question_share': 'perguntas', 'exclaim_share': 'exclamações',
    'voiceover_share': 'narração em off', 'speakers_per_scene': 'personagens por cena',
    'interaction_pairs': 'rede de interações', 'locations': 'nº de locações', 'location_reuse': 'locações repetidas',
    'action_sentence_len': 'descrição extensa', 'lexical_richness': 'vocabulário variado',
    'dev_flashback': 'flashbacks', 'dev_montage': 'montagens', 'dev_intercut': 'cortes paralelos',
    'dev_slowmo': 'câmera lenta', 'dev_pov': 'planos subjetivos', 'dev_super': 'letreiros',
    'emo_fear': 'medo', 'emo_anger': 'raiva', 'emo_joy': 'alegria', 'emo_sadness': 'tristeza', 'emo_love': 'amor',
    'emo_violence': 'violência', 'emo_tech': 'tecnologia', 'emo_family': 'família', 'emo_money': 'dinheiro',
    'low_point': 'fundo do poço tardio',
}
# rótulo: (medida, lado, percentil)
TAGS = {
    'Muito dialogado': ('dialog_share', '>', 80), 'Mais ação que diálogo': ('dialog_share', '<', 20),
    'Protagonista dominante': ('lead_share', '>', 80), 'Elenco coral': ('speakers_per_scene', '>', 80),
    'Noturno': ('night_share', '>', 80), 'Linguagem pesada': ('swears_per_1k', '>', 85),
    'Narração em off': ('voiceover_share', '>', 85), 'Usa flashbacks': ('dev_flashback', '>', 85),
    'Cenas curtas e rápidas': ('words_per_scene', '<', 20), 'Cenas longas': ('words_per_scene', '>', 80),
    'Clímax acelerado': ('final_speedup', '>', 80), 'Ao ar livre': ('ext_share', '>', 80),
    'Ambiente confinado': ('location_reuse', '>', 85), 'Falas longas': ('words_per_speech', '>', 85),
    'Vocabulário rico': ('lexical_richness', '>', 85), 'Violento': ('emo_violence', '>', 85),
    'Clima de medo': ('emo_fear', '>', 85), 'Romântico': ('emo_love', '>', 85), 'Familiar': ('emo_family', '>', 85),
    'Sobre dinheiro': ('emo_money', '>', 85), 'Tecnológico': ('emo_tech', '>', 85),
}


def load(conn):
    rows = conn.execute('SELECT s.imdb_id, m.title, m.year, m.imdb_rating, m.imdb_votes, s.profile '
                        'FROM movie_script s JOIN movies m USING(imdb_id)').fetchall()
    recs = []
    for iid, title, year, rating, votes, p in rows:
        p = json.loads(p)
        if 'pace_act1' not in p or p['speakers'] < 5 or p['dialog_share'] < .15:
            continue  # perfil antigo ou formatação que o analisador não entendeu
        p = {k: v for k, v in p.items() if not isinstance(v, list)}
        recs.append({'imdb_id': iid, 'title': title, 'year': year, 'rating': rating, 'votes': votes, **p})
    df = pd.DataFrame(recs).set_index('imdb_id')
    g = pd.DataFrame(conn.execute('SELECT imdb_id, genre FROM movie_genres').fetchall(), columns=['imdb_id', 'genre'])
    return df, g[g.imdb_id.isin(df.index)]


def main():
    conn = connect()
    df, genres = load(conn)
    feats = [c for c in LABEL if c in df.columns]
    print(f'== {len(df)} roteiros válidos, {len(feats)} medidas\n')

    # 1. rótulos por percentil
    cuts = {t: np.percentile(df[c], p) for t, (c, _, p) in TAGS.items()}
    tags = {iid: [t for t, (c, side, _) in TAGS.items()
                  if (row[c] > cuts[t] if side == '>' else row[c] < cuts[t])] for iid, row in df.iterrows()}

    # 2. estilos (KMeans, k pela silhueta)
    # corta extremos para que um filme atípico (ex.: Cães de Aluguel) não vire um grupo sozinho
    X = np.clip(StandardScaler().fit_transform(df[feats]), -2.5, 2.5)
    best = max(range(4, 9), key=lambda k: silhouette_score(X, KMeans(k, n_init=20, random_state=0).fit_predict(X)))
    km = KMeans(best, n_init=20, random_state=0).fit(X)
    df['style'] = km.labels_
    names = {}
    print(f'== Estilos de roteiro (k={best})')
    for k in range(best):
        cen = pd.Series(km.cluster_centers_[k], index=feats)
        hi = [LABEL[f] for f in cen.sort_values(ascending=False).index[:3]]
        lo = [LABEL[f] for f in cen.sort_values().index[:2]]
        names[k] = 'Mais ' + ', '.join(hi[:2]) + ' · menos ' + lo[0]
        sub = df[df.style == k].sort_values('votes', ascending=False)
        print(f'[{k}] {len(sub):3} filmes · nota média {sub.rating.mean():.2f} · ↑ {", ".join(hi)} · ↓ {", ".join(lo)}')
        print('     ex.: ' + '; '.join(sub.title.head(6)))

    with conn:
        conn.execute('CREATE TABLE IF NOT EXISTS movie_script_class (imdb_id TEXT PRIMARY KEY, style TEXT, tags TEXT)')
        conn.execute('DELETE FROM movie_script_class')
        conn.executemany('INSERT INTO movie_script_class VALUES (?, ?, ?)',
                         [(i, names[int(s)], json.dumps(tags[i], ensure_ascii=False)) for i, s in df['style'].items()])

    # 3. padrões
    print('\n== O que acompanha nota alta (Spearman com a nota do IMDb)')
    corr = df[feats].corrwith(df.rating, method='spearman').dropna().sort_values()
    for f, r in list(corr.items())[:5] + list(corr.items())[-5:]:
        print(f'  {LABEL[f]:24} {r:+.2f}')
    print('\n== Tendência ao longo dos anos (Spearman com o ano)')
    ys = df[feats].corrwith(df.year, method='spearman').dropna()
    for f, r in ys.reindex(ys.abs().sort_values(ascending=False).index)[:8].items():
        print(f'  {LABEL[f]:24} {r:+.2f}')
    print('\n== Traços típicos por gênero (média padronizada, gêneros com ≥10 roteiros)')
    z = pd.DataFrame(X, index=df.index, columns=feats)
    for gname, ids in genres.groupby('genre').imdb_id:
        if len(ids) < 10:
            continue
        m = z.loc[ids].mean().sort_values()
        print(f'  {gname:12} ({len(ids):3}) ↑ ' + ', '.join(f'{LABEL[f]} {v:+.1f}' for f, v in m[::-1][:3].items())
              + ' · ↓ ' + ', '.join(f'{LABEL[f]} {v:+.1f}' for f, v in m[:2].items()))
    print('\n== Forma do arco emocional × nota')
    print(df.groupby('arc_shape').rating.agg(['count', 'mean']).round(2).sort_values('mean').to_string())
    print('\n== Rótulos mais comuns')
    print(pd.Series([t for ts in tags.values() for t in ts]).value_counts().head(12).to_string())
    return 0


if __name__ == '__main__':
    sys.exit(main())
