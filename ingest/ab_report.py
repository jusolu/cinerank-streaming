"""
Relatório do teste A/B real (visitantes do site), a partir dos eventos de /api/event.

Os eventos ficam nos logs do Cloud Run (jsonPayload.ab_event). Este script os lê com o gcloud,
ou de um arquivo JSONL (útil para testar com o servidor local), e compara cada variante com o
controle A:
  taxa de curtida   curtidas / respostas no Descobrir (por sessão, depois a média)
  engajamento       respostas por sessão
  cliques           aberturas de filme / exibições no Para você

Uso:
    python -m ingest.ab_report                 # últimos 30 dias do Cloud Run
    python -m ingest.ab_report --days 7
    python -m ingest.ab_report --file eventos.jsonl
"""
import argparse
import json
import subprocess
import sys
from collections import defaultdict

import numpy as np

PROJECT = 'cinerank-jusolu'
LIKES = {'right', 'up'}


def from_gcloud(days):
    cmd = ['gcloud', 'logging', 'read', 'jsonPayload.ab_event:*', f'--project={PROJECT}',
           f'--freshness={days}d', '--format=json', '--limit=200000']
    out = subprocess.run(cmd, capture_output=True, text=True, check=True, shell=sys.platform == 'win32').stdout
    return [e['jsonPayload'] for e in json.loads(out or '[]')]


def from_file(path):
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line.startswith('{') and 'ab_event' in line:
                rows.append(json.loads(line))
    return rows


def bootstrap_diff(a, b, rng, iters=5000):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 2 or len(b) < 2:
        return None
    da = a[rng.integers(0, len(a), (iters, len(a)))].mean(axis=1)
    db = b[rng.integers(0, len(b), (iters, len(b)))].mean(axis=1)
    d = db - da
    return {'diff': float(b.mean() - a.mean()), 'lo': float(np.percentile(d, 2.5)), 'hi': float(np.percentile(d, 97.5)),
            'p': float(min(1.0, 2 * min((d < 0).mean(), (d > 0).mean())) if b.mean() != a.mean() else 1.0)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--file')
    args = ap.parse_args()
    events = from_file(args.file) if args.file else from_gcloud(args.days)
    if not events:
        print('Nenhum evento ainda.')
        return 0

    s = defaultdict(lambda: {'variant': None, 'answers': 0, 'likes': 0, 'imp_fy': 0, 'open_fy': 0, 'imp_d': 0})
    for e in events:
        x = s[(e['sid'], e['variant'])]  # quem trocou de variante conta como sessões separadas
        x['variant'] = e['variant']
        t = e['ab_event']
        if t == 'answer':
            x['answers'] += 1
            x['likes'] += e.get('action') in LIKES
        elif t == 'impression':
            x['imp_fy' if e.get('surface') == 'foryou' else 'imp_d'] += 1
        elif t == 'open':
            x['open_fy'] += 1

    by_v = defaultdict(list)
    for x in s.values():
        by_v[x['variant']].append(x)
    print(f'== {len(events):,} eventos, {len(s):,} sessões\n')
    print(f'{"var":4}{"sessões":>9}{"c/ resposta":>13}{"respostas":>11}{"taxa curtida":>14}{"resp/sessão":>13}{"cliques PV":>12}')
    metric = {}
    for v in sorted(by_v):
        xs = by_v[v]
        active = [x for x in xs if x['answers']]
        rate = [x['likes'] / x['answers'] for x in active]
        clicks = sum(x['open_fy'] for x in xs) / max(sum(x['imp_fy'] for x in xs), 1)
        metric[v] = {'rate': rate, 'answers': [x['answers'] for x in xs]}
        print(f'{v:4}{len(xs):9}{len(active):13}{sum(x["answers"] for x in xs):11}'
              f'{np.mean(rate) if rate else 0:14.1%}{np.mean(metric[v]["answers"]):13.1f}{clicks:12.1%}')

    if 'A' in metric:
        rng = np.random.default_rng(0)
        print('\n== Contra o controle A (bootstrap por sessão)')
        for v in sorted(metric):
            if v == 'A':
                continue
            for name, key, fmt in (('taxa de curtida', 'rate', '{:+.1%}'), ('respostas/sessão', 'answers', '{:+.1f}')):
                t = bootstrap_diff(metric['A'][key], metric[v][key], rng)
                if not t:
                    print(f'  {v} · {name}: dados insuficientes')
                    continue
                verdict = 'significativo' if t['p'] < 0.05 else 'ainda inconclusivo'
                print(f'  {v} · {name}: {fmt.format(t["diff"])} · IC95% [{fmt.format(t["lo"])}, {fmt.format(t["hi"])}] '
                      f'· p={t["p"]:.3f} ({verdict})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
