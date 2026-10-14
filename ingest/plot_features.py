"""
Perfil emocional do enredo completo da Wikipédia (ingest/wiki_fields.py), para os ~34 mil filmes
que têm a seção Plot: tamanho, tom médio (VADER), arco em 10 trechos, forma do arco, diferença
entre o fim e o começo, e emoções por palavras-chave (mesmos léxicos de ingest/scripts.py).

Grava movie_plot_profile.
    .venv-ml/Scripts/python -m ingest.plot_features
"""
import json
import re
import sys

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from ingest.db import connect
from ingest.scripts import LEX, shape


def profile(text, vader):
    sents = [s for s in re.split(r'(?<=[.!?])\s+', text) if len(s) > 3]
    if len(sents) < 10:
        return None
    scores = [vader.polarity_scores(s)['compound'] for s in sents]
    k = len(scores)
    arc = [round(sum(scores[i * k // 10:(i + 1) * k // 10]) / max(len(scores[i * k // 10:(i + 1) * k // 10]), 1), 3) for i in range(10)]
    words = len(text.split())
    return {'words': words, 'sentences': k, 'tone': round(sum(scores) / k, 3), 'arc': arc, 'shape': shape(arc),
            'end_minus_start': round(sum(arc[-2:]) / 2 - sum(arc[:2]) / 2, 3), 'ending': arc[-1],
            **{f'emo_{n}': round(1000 * len(rx.findall(text)) / max(words, 1), 2) for n, rx in LEX.items()}}


def main():
    conn = connect()
    vader = SentimentIntensityAnalyzer()
    conn.execute('CREATE TABLE IF NOT EXISTS movie_plot_profile (imdb_id TEXT PRIMARY KEY, profile TEXT)')
    rows = conn.execute('SELECT w.imdb_id, w.plot FROM movie_wikifields w LEFT JOIN movie_plot_profile p USING(imdb_id) '
                        'WHERE w.plot IS NOT NULL AND p.imdb_id IS NULL').fetchall()
    print(f'[enredo] {len(rows):,} enredos', flush=True)
    out = []
    for n, (iid, text) in enumerate(rows, 1):
        p = profile(text, vader)
        if p:
            out.append((iid, json.dumps(p)))
        if n % 5000 == 0:
            with conn:
                conn.executemany('INSERT OR REPLACE INTO movie_plot_profile VALUES (?, ?)', out)
            out = []
            print(f'  {n:,}', flush=True)
    with conn:
        conn.executemany('INSERT OR REPLACE INTO movie_plot_profile VALUES (?, ?)', out)
    print(f'[enredo] {conn.execute("SELECT COUNT(*) FROM movie_plot_profile").fetchone()[0]:,} perfis', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
