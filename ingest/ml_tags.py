"""
Percepção do público a partir das tags do MovieLens 32M (2 milhões de tags dadas por quem assistiu).

Só tags de percepção e estilo (não gênero nem nomes), traduzidas. Para cada filme, guarda as
percepções citadas por pelo menos 2 pessoas, até 6, das mais citadas para as menos.

Roda no ambiente .venv-ml:
    .venv-ml/Scripts/python -m ingest.ml_tags
"""
import os
import sys

import pandas as pd

from ingest.db import connect
from ingest.vectors import ML_DIR

PERCEPTION = {
    'atmospheric': 'Atmosférico', 'visually appealing': 'Visual marcante', 'visually stunning': 'Visual marcante',
    'beautifully filmed': 'Visual marcante', 'cinematography': 'Fotografia marcante',
    'twist ending': 'Final surpreendente', 'plot twist': 'Reviravolta', 'surreal': 'Surreal',
    'funny': 'Engraçado', 'hilarious': 'Engraçado', 'thought-provoking': 'Faz pensar',
    'dark comedy': 'Humor ácido', 'black comedy': 'Humor ácido', 'dark humor': 'Humor ácido',
    'psychological': 'Psicológico', 'psychology': 'Psicológico', 'dark': 'Sombrio',
    'great soundtrack': 'Trilha marcante', 'soundtrack': 'Trilha marcante', 'quirky': 'Excêntrico',
    'mindfuck': 'Mexe com a cabeça', 'mind-bending': 'Mexe com a cabeça', 'stylized': 'Estilizado',
    'disturbing': 'Perturbador', 'great acting': 'Grandes atuações', 'predictable': 'Previsível',
    'philosophical': 'Filosófico', 'philosophy': 'Filosófico', 'dreamlike': 'Onírico',
    'inspirational': 'Inspirador', 'nonlinear': 'Narrativa não linear', 'bittersweet': 'Agridoce',
    'cult film': 'Cult', 'cult classic': 'Cult', 'boring': 'Arrastado', 'emotional': 'Emocionante',
    'intelligent': 'Inteligente', 'clever': 'Inteligente', 'tense': 'Tenso', 'intense': 'Tenso',
    'suspense': 'Tenso', 'suspenseful': 'Tenso', 'beautiful': 'Belo', 'witty': 'Espirituoso',
    'touching': 'Tocante', 'slow': 'Lento', 'slow paced': 'Lento', 'violence': 'Violento', 'violent': 'Violento',
    'feel-good': 'Para se sentir bem', 'feel good movie': 'Para se sentir bem', 'heartwarming': 'Aconchegante',
    'social commentary': 'Crítica social', 'satire': 'Sátira', 'quotable': 'Frases marcantes',
    'overrated': 'Superestimado', 'melancholy': 'Melancólico', 'melancholic': 'Melancólico', 'gritty': 'Cru',
    'epic': 'Épico', 'cheesy': 'Brega', 'gory': 'Sangrento', 'creepy': 'Assustador', 'scary': 'Assustador',
    'romantic': 'Romântico', 'sad': 'Triste', 'depressing': 'Deprimente', 'complex': 'Complexo',
    'confusing': 'Confuso', 'realistic': 'Realista', 'entertaining': 'Divertido', 'fast paced': 'Ritmo acelerado',
    'fast-paced': 'Ritmo acelerado', 'slow burn': 'Tensão crescente', 'character study': 'Estudo de personagem',
    'ensemble cast': 'Elenco coral', 'dialogue': 'Diálogos marcantes', 'great dialogue': 'Diálogos marcantes',
    'minimalist': 'Minimalista', 'claustrophobic': 'Claustrofóbico', 'absurd': 'Absurdo', 'whimsical': 'Fantasioso',
    'nostalgic': 'Nostálgico', 'tearjerker': 'Para chorar', 'sentimental': 'Sentimental',
}
MIN_USERS = 2
PER_FILM = 6


def main():
    conn = connect()
    links = pd.read_csv(os.path.join(ML_DIR, 'links.csv'), dtype={'imdbId': str})
    links['imdb_id'] = 'tt' + links['imdbId'].str.zfill(7)
    t = pd.read_csv(os.path.join(ML_DIR, 'tags.csv'), engine='pyarrow')
    t['label'] = t['tag'].astype(str).str.lower().str.strip().map(PERCEPTION)
    t = t.dropna(subset=['label']).merge(links[['movieId', 'imdb_id']], on='movieId')
    counts = t.groupby(['imdb_id', 'label'])['userId'].nunique().reset_index(name='users')
    counts = counts[counts['users'] >= MIN_USERS].sort_values(['imdb_id', 'users'], ascending=[True, False])
    counts = counts.groupby('imdb_id').head(PER_FILM)
    known = {r[0] for r in conn.execute('SELECT imdb_id FROM movies')}
    rows = [(i, l, int(u)) for i, l, u in counts.itertuples(index=False) if i in known]
    with conn:
        conn.execute('CREATE TABLE IF NOT EXISTS movie_tags (imdb_id TEXT NOT NULL, tag TEXT NOT NULL, '
                     'users INTEGER NOT NULL, PRIMARY KEY (imdb_id, tag))')
        conn.execute('DELETE FROM movie_tags')
        conn.executemany('INSERT OR REPLACE INTO movie_tags VALUES (?, ?, ?)', rows)
    print(f'[tags] {len(rows):,} percepções em {len({r[0] for r in rows}):,} filmes', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
