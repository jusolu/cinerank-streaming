"""
Sinopse e imagem a partir da Wikipédia, sem chave de API.

1. Wikidata (SPARQL, em lotes): IMDb ID → artigo na Wikipédia em português e em inglês.
2. Wikipédia (API de consulta): texto do artigo + imagem principal.
   A sinopse vem da seção de enredo ("Sinopse", "Enredo", "Plot"…). Sem ela, usa a introdução.
   Preferência: enredo em pt > enredo em en > introdução em pt > introdução em en.

Licença: textos CC BY-SA (exigem atribuição). Muitos pôsteres na Wikipédia são de uso restrito.

Uso:
    python -m ingest.wiki                  # 10 mil filmes mais votados ainda não buscados
    python -m ingest.wiki --limit 500
"""
import argparse
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

from ingest.db import connect

USER_AGENT = 'CineRank/1.0 (projeto pessoal, nao comercial; https://github.com/jusolu/cinerank-streaming)'
SPARQL = 'https://query.wikidata.org/sparql'
SPARQL_BATCH = 250
WORKERS = 8          # a Wikimedia pede uso moderado da API; o limitador global segura o ritmo
MAX_RPS = 20
SYNOPSIS_MAX = 900

PLOT_HEADINGS = {
    'pt': {'sinopse', 'enredo', 'resumo', 'trama', 'premissa', 'argumento', 'história', 'enredo e personagens'},
    'en': {'plot', 'synopsis', 'premise', 'plot summary', 'story'},
}


def log(msg):
    print(f"[wiki] {msg}", flush=True)


class RateLimiter:
    def __init__(self, rps):
        self.interval = 1.0 / rps
        self.lock = threading.Lock()
        self.next_at = time.monotonic()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            delay = self.next_at - now
            self.next_at = max(now, self.next_at) + self.interval
        if delay > 0:
            time.sleep(delay)


session = requests.Session()
session.headers['User-Agent'] = USER_AGENT
limiter = RateLimiter(MAX_RPS)


def get_json(url, params, method='GET', attempts=4):
    for attempt in range(attempts):
        limiter.wait()
        try:
            if method == 'POST':
                r = session.post(url, data=params, timeout=60)
            else:
                r = session.get(url, params=params, timeout=30)
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue
        if r.status_code in (429, 503):
            time.sleep(float(r.headers.get('Retry-After', 2 ** attempt)))
            continue
        if r.ok:
            return r.json()
        time.sleep(2 ** attempt)
    return None


def map_articles(imdb_ids):
    """IMDb ID → {'pt': título, 'en': título} via Wikidata."""
    values = ' '.join(f'"{i}"' for i in imdb_ids)
    query = f"""
        SELECT ?imdb ?ptTitle ?enTitle WHERE {{
          VALUES ?imdb {{ {values} }}
          ?item wdt:P345 ?imdb .
          OPTIONAL {{ ?pt schema:about ?item; schema:isPartOf <https://pt.wikipedia.org/>; schema:name ?ptTitle . }}
          OPTIONAL {{ ?en schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>; schema:name ?enTitle . }}
        }}"""
    data = get_json(SPARQL, {'query': query, 'format': 'json'}, method='POST')
    out = {}
    for b in (data or {}).get('results', {}).get('bindings', []):
        imdb = b['imdb']['value']
        entry = out.setdefault(imdb, {})
        if 'ptTitle' in b:
            entry['pt'] = b['ptTitle']['value']
        if 'enTitle' in b:
            entry['en'] = b['enTitle']['value']
    return out


def fetch_article(lang, title):
    """Texto completo (com marcação de seções) e imagem principal de um artigo."""
    data = get_json(f'https://{lang}.wikipedia.org/w/api.php', {
        'action': 'query', 'format': 'json', 'formatversion': 2, 'redirects': 1, 'maxlag': 5,
        'prop': 'extracts|pageimages', 'titles': title,
        'explaintext': 1, 'exsectionformat': 'wiki',
        'piprop': 'thumbnail', 'pithumbsize': 500, 'pilicense': 'any',
    })
    pages = (data or {}).get('query', {}).get('pages', [])
    if not pages or pages[0].get('missing'):
        return None, None
    page = pages[0]
    return page.get('extract') or '', (page.get('thumbnail') or {}).get('source')


def clean(text):
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) <= SYNOPSIS_MAX:
        return text
    cut = text[:SYNOPSIS_MAX]
    end = max(cut.rfind('. '), cut.rfind('! '), cut.rfind('? '))
    return (cut[:end + 1] if end > SYNOPSIS_MAX * 0.5 else cut.rstrip() + '…')


def split_sections(extract):
    """Retorna (introdução, {título da seção em minúsculas: texto})."""
    parts = re.split(r'\n(={2,})\s*(.+?)\s*\1\s*\n', '\n' + extract)
    lead = parts[0].strip()
    sections = {}
    for i in range(1, len(parts) - 2, 3):
        sections[parts[i + 1].strip().lower()] = parts[i + 2].strip()
    return lead, sections


def synopsis_from(lang, extract):
    lead, sections = split_sections(extract)
    for heading, body in sections.items():
        if heading in PLOT_HEADINGS[lang] and len(body) > 80:
            return clean(body), 'plot'
    first_para = lead.split('\n')[0] if lead else ''
    return (clean(first_para), 'lead') if len(first_para) > 60 else (None, None)


def enrich(imdb_id, titles):
    """Busca pt e, se faltar enredo ou imagem, en."""
    found = {}
    for lang in ('pt', 'en'):
        if lang not in titles:
            continue
        extract, image = fetch_article(lang, titles[lang])
        if extract is None:
            continue
        text, kind = synopsis_from(lang, extract)
        found[lang] = {'title': titles[lang], 'text': text, 'kind': kind, 'image': image}
        if lang == 'pt' and kind == 'plot' and image:
            break  # pt completo; não precisa do inglês

    pick = None
    for lang, kind in (('pt', 'plot'), ('en', 'plot'), ('pt', 'lead'), ('en', 'lead')):
        if found.get(lang, {}).get('kind') == kind:
            pick = (lang, found[lang])
            break
    image = (found.get('pt') or {}).get('image') or (found.get('en') or {}).get('image')
    wiki_lang = 'pt' if 'pt' in found else ('en' if 'en' in found else None)
    return {
        'imdb_id': imdb_id,
        'wiki_lang': wiki_lang,
        'wiki_title': found[wiki_lang]['title'] if wiki_lang else None,
        'synopsis': pick[1]['text'] if pick else None,
        'synopsis_lang': pick[0] if pick else None,
        'synopsis_kind': pick[1]['kind'] if pick else None,
        'image_url': image,
    }


def save(conn, rows):
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    with conn:
        conn.executemany("""
            INSERT INTO movie_wiki (imdb_id, wiki_lang, wiki_title, synopsis, synopsis_lang, synopsis_kind, image_url, fetched_at)
            VALUES (:imdb_id, :wiki_lang, :wiki_title, :synopsis, :synopsis_lang, :synopsis_kind, :image_url, :fetched_at)
            ON CONFLICT(imdb_id) DO UPDATE SET
                wiki_lang = excluded.wiki_lang, wiki_title = excluded.wiki_title, synopsis = excluded.synopsis,
                synopsis_lang = excluded.synopsis_lang, synopsis_kind = excluded.synopsis_kind,
                image_url = excluded.image_url, fetched_at = excluded.fetched_at
        """, [{**r, 'fetched_at': now} for r in rows])


def main(argv=None):
    parser = argparse.ArgumentParser(description='Sinopse e imagem via Wikipédia/Wikidata (sem chave).')
    parser.add_argument('--limit', type=int, default=10000, help='Máximo de filmes nesta execução')
    parser.add_argument('--min-votes', type=int, default=500)
    args = parser.parse_args(argv)

    conn = connect()
    pending = [r[0] for r in conn.execute("""
        SELECT m.imdb_id FROM movies m LEFT JOIN movie_wiki w USING (imdb_id)
        WHERE m.imdb_votes >= ? AND w.imdb_id IS NULL
        ORDER BY m.imdb_votes DESC LIMIT ?
    """, (args.min_votes, args.limit))]
    log(f'{len(pending):,} filmes a buscar')
    t0 = time.time()
    done = with_text = with_image = 0

    for start in range(0, len(pending), SPARQL_BATCH):
        batch = pending[start:start + SPARQL_BATCH]
        mapping = map_articles(batch)
        rows = []
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(enrich, iid, mapping[iid]): iid for iid in batch if iid in mapping}
            for f in as_completed(futures):
                try:
                    rows.append(f.result())
                except Exception as e:
                    log(f'falha em {futures[f]}: {e}')
        # Sem artigo em pt/en: registra para não buscar de novo.
        mapped = {r['imdb_id'] for r in rows}
        rows += [{'imdb_id': i, 'wiki_lang': None, 'wiki_title': None, 'synopsis': None,
                  'synopsis_lang': None, 'synopsis_kind': None, 'image_url': None}
                 for i in batch if i not in mapped]
        save(conn, rows)

        done += len(batch)
        with_text += sum(1 for r in rows if r['synopsis'])
        with_image += sum(1 for r in rows if r['image_url'])
        rate = done / (time.time() - t0)
        log(f'{done:,}/{len(pending):,} · sinopse {with_text:,} · imagem {with_image:,} · {rate:.1f} filmes/s')

    log(f'concluído em {time.time() - t0:.0f}s')
    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
