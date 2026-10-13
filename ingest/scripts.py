"""
Roteiros do IMSDb: download local (somente para análise — têm direitos autorais, ficam em data/raw,
fora do git e nunca são publicados) e "perfil de roteiro" por filme, gravado em movie_script.

Perfil: nº de cenas e palavras por cena, proporção de diálogo, personagens falantes, peso do
protagonista, interno × externo, dia × noite, palavrões e arco emocional (VADER em 10 trechos).

O IMSDb marca cabeçalhos de cena e nomes de personagem em <b>; quando não há negrito, usa o recuo.

Uso (ambiente .venv-ml, que tem o vaderSentiment):
    .venv-ml/Scripts/python -m ingest.scripts --limit 100     # baixa os primeiros 100
    .venv-ml/Scripts/python -m ingest.scripts --analyze-only
"""
import argparse
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter

from ingest.db import connect

BASE = 'https://imsdb.com'
RAW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'raw', 'scripts')
UA = 'CineRank/1.0 (projeto pessoal de estudo; download lento)'
DELAY = 1.5
SCENE = re.compile(r'^\s*(\d+[A-Z]?\s*\.?\s*)?(INT\.?|EXT\.?|INT\./EXT\.?|EXT\./INT\.?|I/E\.?)[\s./-]', re.I)
SWEAR = re.compile(r"\b(fuck\w*|shit\w*|bitch\w*|asshole|goddamn\w*|bastard|cunt|motherfuck\w*|dick)\b", re.I)


def get(url):
    req = urllib.request.Request(urllib.parse.quote(url, safe=':/,?=&'), headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode('latin-1')


def norm(t):
    t = unicodedata.normalize('NFKD', html.unescape(t)).encode('ascii', 'ignore').decode().lower()
    m = re.match(r'^(.*), (the|a|an)$', t.strip())
    if m:
        t = f'{m.group(2)} {m.group(1)}'
    return re.sub(r'[^a-z0-9]+', ' ', t).strip()


def listing():
    page = get(f'{BASE}/all-scripts.html')
    return [(html.unescape(t), html.unescape(h)) for h, t in
            re.findall(r'<a href="/Movie Scripts/([^"]+)" title="[^"]*">([^<]+)</a>', page)]


def match(conn, items):
    """Título do IMSDb → imdb_id do catálogo (o mais votado entre os homônimos)."""
    best = {}
    for iid, title, orig, votes in conn.execute('SELECT imdb_id, title, original_title, imdb_votes FROM movies'):
        for t in {title, orig} - {None}:
            k = norm(t)
            if k not in best or (votes or 0) > best[k][1]:
                best[k] = (iid, votes or 0)
    out = []
    for title, page in items:
        hit = best.get(norm(title))
        if hit:
            out.append((hit[0], hit[1], title, page))
    return sorted(out, key=lambda x: -x[1])


def download(iid, title, page):
    path = os.path.join(RAW, f'{iid}.txt')
    if os.path.exists(path):
        return 'já tinha'
    info = get(f'{BASE}/Movie Scripts/{page}')
    m = re.search(r'href="(/scripts/[^"]+\.html)"', info)
    if not m:
        return 'sem roteiro'
    doc = get(BASE + m.group(1))
    pre = re.search(r'<pre>(.*)</pre>', doc, re.S)
    if not pre or len(pre.group(1)) < 20000:
        return 'curto/vazio'
    with open(path, 'w', encoding='utf-8') as f:
        f.write(pre.group(1).replace('\r\n', '\n').replace('\r', '\n'))  # mantém o <b> para o analisador
    return 'ok'


# ---------------------------------------------------------------- análise
def lines_of(raw):
    """[(texto, recuo, negrito)] a partir do HTML do <pre>."""
    out, bold = [], False
    if raw.count('\n\n') > 0.45 * raw.count('\n'):  # arquivos antigos gravados com \r\r\n
        raw = raw.replace('\n\n', '\n')
    for line in raw.split('\n'):
        # "</b>   Hello?" fecha o negrito antes do texto: a fala não é negrito
        b = '<b>' in line or (bold and not line.lstrip().startswith('</b>'))
        tags = re.findall(r'</?b>', line)
        if tags:
            bold = tags[-1] == '<b>'
        text = html.unescape(re.sub(r'<[^>]+>', '', line)).expandtabs(8).rstrip()
        out.append((text.strip(), len(text) - len(text.lstrip()), b and bool(text.strip())))
    return out


def is_speaker(t):
    t = re.sub(r'\(.*?\)', '', t).strip()
    return 1 <= len(t) <= 30 and t.upper() == t and re.search(r'[A-Z]', t) and not SCENE.match(t) \
        and not t.endswith(':') and len(t.split()) <= 4


def parse(raw):
    ls = lines_of(raw)
    has_bold = sum(b for _, _, b in ls) > 50
    # recuo típico dos nomes: o mais comum entre linhas em maiúsculas candidatas
    ind = Counter(i for t, i, b in ls if is_speaker(t) and (b or not has_bold))
    sp_ind = ind.most_common(1)[0][0] if ind else 30
    scenes, cur, speaker = [], None, None
    for t, i, b in ls:
        if not t:
            speaker = None
            continue
        if SCENE.match(t):
            cur = {'head': t.upper(), 'action': [], 'dialog': [], 'speakers': []}
            scenes.append(cur)
            speaker = None
            continue
        if cur is None:
            cur = {'head': '', 'action': [], 'dialog': [], 'speakers': []}
            scenes.append(cur)
        if is_speaker(t) and (b if has_bold else abs(i - sp_ind) <= 6) and i >= 10:
            speaker = re.sub(r'\(.*?\)|\bCONT\'?D\b', '', t).strip(' .')
            cur['speakers'].append(speaker)
            cur['vo'] = cur.get('vo', 0) + bool(re.search(r'\((V\.?\s*O|O\.?\s*S|O\.?\s*C)', t))
            continue
        if speaker and not b and i >= 8 and not (t.startswith('(') and t.endswith(')')):
            cur['dialog'].append(t)
        elif not t.startswith('('):
            speaker = None
            cur['action'].append(t)
    return [s for s in scenes if s['head'] or s['action'] or s['dialog']]


def profile(scenes, vader):
    words = lambda xs: sum(len(x.split()) for x in xs)
    dw = sum(words(s['dialog']) for s in scenes)
    aw = sum(words(s['action']) for s in scenes)
    total = dw + aw
    heads = [s['head'] for s in scenes if s['head']]
    lines_by = Counter(sp for s in scenes for sp in s['speakers'])
    talkers = [c for c, n in lines_by.items() if n >= 3]
    n_lines = sum(lines_by.values()) or 1
    top = lines_by.most_common(5)
    ext = sum(bool(re.match(r'^\s*(\d+\S*\s*)?EXT', h)) for h in heads)
    night = sum(bool(re.search(r'\bNIGHT\b|\bDUSK\b|\bEVENING\b', h)) for h in heads)
    day = sum(bool(re.search(r'\bDAY\b|\bMORNING\b|\bDAWN\b|\bAFTERNOON\b', h)) for h in heads)
    text = ' '.join(' '.join(s['action'] + s['dialog']) for s in scenes)
    # arco emocional: 10 trechos de mesmo tamanho, média do compound de VADER por frase
    tokens = text.split()
    arc = []
    for k in range(10):
        chunk = ' '.join(tokens[k * len(tokens) // 10:(k + 1) * len(tokens) // 10])
        sents = [x for x in re.split(r'(?<=[.!?])\s+', chunk) if len(x) > 3]
        arc.append(round(sum(vader.polarity_scores(x)['compound'] for x in sents) / max(len(sents), 1), 3))
    return {
        'scenes': len(heads), 'words': total,
        'words_per_scene': round(total / max(len(heads), 1), 1),
        'dialog_share': round(dw / max(total, 1), 3),
        'speakers': len(talkers),
        'lead_share': round(top[0][1] / n_lines, 3) if top else 0,
        'top_speakers': [c for c, _ in top],
        'ext_share': round(ext / max(len(heads), 1), 3),
        'night_share': round(night / max(day + night, 1), 3),
        'swears_per_1k': round(1000 * len(SWEAR.findall(text)) / max(total, 1), 2),
        'arc': arc,
        'arc_shape': shape(arc),
        **extra(scenes, heads, text, arc),
    }


# léxicos curtos de emoção/tema (inglês, contagem por 1k palavras)
LEXICON = {
    'fear': r'scream\w*|terrif\w*|afraid|fear\w*|panic\w*|horror|dread|trembl\w*|shiver\w*|creep\w*',
    'anger': r'furious|rage|angry|anger|hate\w*|yell\w*|shout\w*|slam\w*|curse\w*',
    'joy': r'laugh\w*|smil\w*|happy|happi\w*|joy\w*|grin\w*|delight\w*|celebrat\w*',
    'sadness': r'cry|cries|crying|tears?|sob\w*|grief|mourn\w*|funeral|lonely|sad\w*',
    'love': r'love[sd]?|loving|kiss\w*|embrac\w*|darling|honey|marry|wedding',
    'violence': r'gun\w*|shoot\w*|shot|blood\w*|kill\w*|dead|corpse|stab\w*|explo\w*|punch\w*',
    'tech': r'computer|screen|monitor|phone|signal|ship|engine|laser|robot|radar',
    'family': r'mom|mother|dad|father|son|daughter|brother|sister|family|kids?',
    'money': r'money|cash|dollars?|bank|rich|deal|business|million\w*',
}
LEX = {k: re.compile(r'\b(' + v + r')\b', re.I) for k, v in LEXICON.items()}
DEVICES = {'flashback': r'FLASHBACK|FLASH BACK', 'montage': r'MONTAGE|SERIES OF SHOTS', 'intercut': r'INTERCUT',
           'slowmo': r'SLOW MOTION|SLOW-MO', 'pov': r'\bPOV\b', 'super': r'\bSUPER(IMPOSE)?\s*:|TITLE CARD'}


def extra(scenes, heads, text, arc):
    """Metadados adicionais: ritmo por ato, estilo de fala, recursos narrativos, emoções e léxico."""
    n = max(len(scenes), 1)
    sw = [sum(len(x.split()) for x in s['action'] + s['dialog']) for s in scenes]
    q = len(sw) // 4 or 1
    mean = lambda xs: sum(xs) / max(len(xs), 1)
    speeches = sum(len(s['speakers']) for s in scenes) or 1
    dlines = [x for s in scenes for x in s['dialog']]
    dtext = ' '.join(dlines)
    sents = [x for x in re.split(r'(?<=[.!?])\s+', dtext) if x]
    acts = ' '.join(x for s in scenes for x in s['action'])
    asents = [x for x in re.split(r'(?<=[.!?])\s+', acts) if len(x) > 3]
    total = max(len(text.split()), 1)
    tokens = re.findall(r"[a-z']+", text.lower())[:20000]
    locs = {re.sub(r'\s*[-–]\s*(DAY|NIGHT|MORNING|EVENING|DUSK|DAWN|LATER|CONTINUOUS|SAME|MOMENTS LATER).*$', '',
                   re.sub(r'^\s*\d+\S*\s*', '', h)).strip(' .') for h in heads}
    pairs = set()
    for s in scenes:
        sp = sorted(set(s['speakers']))
        pairs |= {(a, b) for i, a in enumerate(sp) for b in sp[i + 1:]}
    full = '\n'.join(s['head'] for s in scenes) + '\n' + text
    return {
        # ritmo: palavras por cena no início, no meio e no fim (atos aproximados) e aceleração final
        'pace_act1': round(mean(sw[:q]), 1), 'pace_act2': round(mean(sw[q:-q]), 1), 'pace_act3': round(mean(sw[-q:]), 1),
        'final_speedup': round(mean(sw) / max(mean(sw[-q:]), 1), 2),
        'words_per_speech': round(sum(len(x.split()) for x in dlines) / speeches, 1),
        'question_share': round(sum(x.endswith('?') for x in sents) / max(len(sents), 1), 3),
        'exclaim_share': round(sum(x.endswith('!') for x in sents) / max(len(sents), 1), 3),
        'voiceover_share': round(sum(s.get('vo', 0) for s in scenes) / speeches, 3),
        'speakers_per_scene': round(mean([len(set(s['speakers'])) for s in scenes]), 2),
        'interaction_pairs': len(pairs),
        'locations': len(locs), 'location_reuse': round(len(heads) / max(len(locs), 1), 2),
        'action_sentence_len': round(mean([len(x.split()) for x in asents]), 1),
        'lexical_richness': round(len(set(tokens)) / max(len(tokens), 1), 3),
        'low_point': arc.index(min(arc)) / 9, 'high_point': arc.index(max(arc)) / 9,
        **{f'dev_{k}': round(100 * len(re.findall(v, full)) / n, 2) for k, v in DEVICES.items()},
        **{f'emo_{k}': round(1000 * len(r.findall(text)) / total, 2) for k, r in LEX.items()},
    }


def shape(arc):
    """Forma do arco (Reagan et al., 2016, simplificada): compara início, meio e fim."""
    # limites relativos à oscilação do próprio filme (o VADER varia pouco em texto de roteiro)
    mean = sum(arc) / len(arc)
    sd = (sum((x - mean) ** 2 for x in arc) / len(arc)) ** .5 or 1
    a, m, z = ((sum(p) / len(p) - mean) / sd for p in (arc[:3], arc[3:7], arc[7:]))
    if m < min(a, z) - .5:
        return 'Queda e recuperação'
    if m > max(a, z) + .5:
        return 'Ascensão e queda'
    if z > a + .6:
        return 'Ascendente'
    if z < a - .6:
        return 'Descendente'
    return 'Estável'


def analyze(conn):
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    vader = SentimentIntensityAnalyzer()
    conn.execute('CREATE TABLE IF NOT EXISTS movie_script (imdb_id TEXT PRIMARY KEY, profile TEXT NOT NULL)')
    rows = []
    for name in sorted(os.listdir(RAW)):
        if not re.match(r'^tt\d+\.txt$', name):
            continue
        with open(os.path.join(RAW, name), encoding='utf-8') as f:
            p = profile(parse(f.read()), vader)
        if p['scenes'] >= 20 and p['words'] >= 8000:  # descarta textos que não são roteiro formatado
            rows.append((name[:-4], json.dumps(p, ensure_ascii=False)))
    with conn:
        conn.execute('DELETE FROM movie_script')
        conn.executemany('INSERT INTO movie_script VALUES (?, ?)', rows)
    print(f'[roteiros] {len(rows)} perfis gravados', flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='baixa no máximo N roteiros (0 = todos)')
    ap.add_argument('--analyze-only', action='store_true')
    args = ap.parse_args()
    os.makedirs(RAW, exist_ok=True)
    conn = connect()
    if not args.analyze_only:
        items = match(conn, listing())
        print(f'[roteiros] {len(items)} títulos do IMSDb casam com o catálogo', flush=True)
        stats = Counter()
        for n, (iid, _, title, page) in enumerate(items[:args.limit or None], 1):
            try:
                r = download(iid, title, page)
            except Exception as e:  # noqa: BLE001 — um roteiro com problema não para o lote
                r = f'erro {type(e).__name__}'
            stats[r.split()[0]] += 1
            if r != 'já tinha':
                time.sleep(DELAY)
            if n % 25 == 0:
                print(f'  {n}: {dict(stats)}', flush=True)
        print(f'[roteiros] {dict(stats)}', flush=True)
    analyze(conn)
    return 0


if __name__ == '__main__':
    sys.exit(main())
