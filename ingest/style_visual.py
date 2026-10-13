"""
Estilo visual a partir dos pôsteres (aproximação: é o estilo do pôster, não da fotografia do filme).

Duas leituras, ambas locais e sem chave:
  palette  medição direta das cores (Pillow): escura/clara, vibrante/dessaturada, quente/fria, preto e branco.
  poster   composição por comparação imagem × texto com o modelo CLIP (FastEmbed/ONNX):
           ilustração, minimalista, close no rosto, atmosférico, retrô…

Roda no ambiente .venv-ml:
    .venv-ml/Scripts/python -m ingest.style_visual --download    # baixa pôsteres (cache em data/raw/posters)
    .venv-ml/Scripts/python -m ingest.style_visual --analyze     # mede cores, classifica e grava
"""
import argparse
import colorsys
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import requests

from ingest.db import BASE_DIR, connect

TOP_N = 15000
POSTER_DIR = os.path.join(BASE_DIR, 'data', 'raw', 'posters')
TMDB_IMG = 'https://image.tmdb.org/t/p/w185'
USER_AGENT = 'CineRank/1.0 (projeto pessoal, nao comercial)'

# Descrição em inglês (o CLIP foi treinado em inglês) → rótulo exibido.
POSTER_STYLES = [
    ('an illustrated or animated cartoon movie poster', 'Ilustração / animação'),
    ('a minimalist movie poster with a simple design and lots of empty space', 'Minimalista'),
    ('a movie poster with a large close-up of a face', 'Close no rosto'),
    ('a movie poster showing a group of many characters', 'Elenco reunido'),
    ('an action movie poster with explosions, fire and weapons', 'Ação explosiva'),
    ('a dark moody atmospheric movie poster with fog and shadows', 'Atmosférico / sombrio'),
    ('a vintage retro movie poster', 'Visual retrô'),
    ('a movie poster with a wide landscape or nature scenery', 'Paisagem'),
    ('a movie poster with a city skyline', 'Urbano'),
    ('a romantic movie poster with a couple', 'Casal romântico'),
    ('a horror movie poster with a scary creepy figure', 'Imagem assustadora'),
]
MIN_PROB = 0.30


def log(msg):
    print(f"[visual] {msg}", flush=True)


def targets(conn):
    return conn.execute("""
        SELECT m.imdb_id, d.poster_path, w.image_url
        FROM movies m
        LEFT JOIN movie_dump d USING (imdb_id)
        LEFT JOIN movie_wiki w USING (imdb_id)
        WHERE (d.poster_path IS NOT NULL OR w.image_url IS NOT NULL)
        ORDER BY m.imdb_votes DESC LIMIT ?
    """, (TOP_N,)).fetchall()


def download(conn):
    os.makedirs(POSTER_DIR, exist_ok=True)
    rows = [r for r in targets(conn) if not os.path.exists(os.path.join(POSTER_DIR, f'{r[0]}.jpg'))]
    log(f'{len(rows):,} pôsteres a baixar')
    session = requests.Session()
    session.headers['User-Agent'] = USER_AGENT

    def fetch(row):
        iid, path, wiki = row
        url = f'{TMDB_IMG}{path}' if path else wiki
        r = session.get(url, timeout=20)
        if r.ok and r.headers.get('content-type', '').startswith('image'):
            with open(os.path.join(POSTER_DIR, f'{iid}.jpg'), 'wb') as f:
                f.write(r.content)
            return True
        return False

    t0 = time.time()
    ok = 0
    with ThreadPoolExecutor(max_workers=12) as pool:
        for i, f in enumerate(as_completed([pool.submit(fetch, r) for r in rows]), 1):
            try:
                ok += f.result()
            except Exception:
                pass
            if i % 2000 == 0:
                log(f'{i:,}/{len(rows):,} ({i / (time.time() - t0):.0f}/s)')
    log(f'{ok:,} baixados em {time.time() - t0:.0f}s')


def palette(img):
    """Rótulos objetivos de cor a partir de uma miniatura."""
    from PIL import Image
    small = img.convert('RGB').resize((48, 72), Image.BILINEAR)
    px = np.asarray(small, dtype=np.float32).reshape(-1, 3) / 255.0
    hsv = np.array([colorsys.rgb_to_hsv(*p) for p in px])
    h, s, v = hsv[:, 0], hsv[:, 1], hsv[:, 2]
    labels = []
    if s.mean() < 0.07:
        return ['Preto e branco']
    if v.mean() < 0.30:
        labels.append('Paleta escura')
    elif v.mean() > 0.62:
        labels.append('Paleta clara')
    if s.mean() > 0.50:
        labels.append('Cores vibrantes')
    elif s.mean() < 0.22:
        labels.append('Cores dessaturadas')
    colored = s > 0.25
    if colored.sum() > len(s) * 0.15:
        hue = h[colored] * 360
        warm = ((hue < 70) | (hue > 330)).mean()
        cool = ((hue > 160) & (hue < 270)).mean()
        if warm > 0.65:
            labels.append('Tons quentes')
        elif cool > 0.65:
            labels.append('Tons frios')
    return labels


def analyze(conn):
    from PIL import Image
    from fastembed import ImageEmbedding, TextEmbedding

    files = [(r[0], os.path.join(POSTER_DIR, f'{r[0]}.jpg')) for r in targets(conn)]
    files = [(i, p) for i, p in files if os.path.exists(p)]
    log(f'{len(files):,} pôsteres para analisar')

    text_model = TextEmbedding('Qdrant/clip-ViT-B-32-text')
    image_model = ImageEmbedding('Qdrant/clip-ViT-B-32-vision')
    prompts = np.asarray(list(text_model.embed([p for p, _ in POSTER_STYLES])), dtype=np.float32)
    prompts /= np.linalg.norm(prompts, axis=1, keepdims=True)

    rows = []
    t0 = time.time()
    batch = 64
    for start in range(0, len(files), batch):
        chunk = files[start:start + batch]
        images, valid = [], []
        for iid, path in chunk:
            try:
                img = Image.open(path)
                img.load()
            except Exception:
                continue
            images.append(img.convert('RGB'))
            valid.append(iid)
            for label in palette(img):
                rows.append((iid, 'palette', label))
        if not images:
            continue
        emb = np.asarray(list(image_model.embed(images)), dtype=np.float32)
        emb /= np.linalg.norm(emb, axis=1, keepdims=True)
        logits = 100.0 * emb @ prompts.T  # escala de logits do CLIP
        probs = np.exp(logits - logits.max(axis=1, keepdims=True))
        probs /= probs.sum(axis=1, keepdims=True)
        for iid, p in zip(valid, probs):
            best = int(p.argmax())
            if p[best] >= MIN_PROB:
                rows.append((iid, 'poster', POSTER_STYLES[best][1]))
        if (start // batch) % 30 == 0:
            done = start + len(chunk)
            log(f'{done:,}/{len(files):,} ({done / (time.time() - t0):.0f}/s)')

    with conn:
        conn.execute("CREATE TABLE IF NOT EXISTS movie_style (imdb_id TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL, "
                     "PRIMARY KEY (imdb_id, kind, value))")
        conn.execute("DELETE FROM movie_style WHERE kind IN ('palette', 'poster')")
        conn.executemany('INSERT OR IGNORE INTO movie_style VALUES (?, ?, ?)', rows)
    log(f'{len(rows):,} rótulos visuais gravados em {time.time() - t0:.0f}s')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--analyze', action='store_true')
    args = parser.parse_args(argv)
    conn = connect()
    if args.download:
        download(conn)
    if args.analyze:
        analyze(conn)
    return 0


if __name__ == '__main__':
    sys.exit(main())
