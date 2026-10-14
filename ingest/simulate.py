"""
Simulador de testes A/B da recomendação, com usuários reais do MovieLens (anônimos).

Cada usuário simulado "joga" o Descobrir a partir do zero: recebe 10 filmes por rodada do mesmo
motor do site (recommender.rank), responde conforme a nota que deu de verdade e o motor se adapta.
  nota ≥ 4     → gostei          nota ≤ 2,5  → não gostei
  3 a 3,5      → mais ou menos   não avaliou → pular (não sabemos o que acharia)

Todas as variantes rodam com os MESMOS usuários (teste pareado: muito mais sensível que um A/B
com grupos separados). O gosto coletivo é retreinado sem nenhum dado desses usuários, para o
motor não "lembrar" das respostas.

Personas: as salvas por ingest/personas.py (data/personas.json); o simulador sorteia o mesmo número
de usuários de cada uma (--per-persona), e o resultado sai por persona, para ver quem cada
variante ajuda ou atrapalha. Sem o arquivo, agrupa na hora os usuários sorteados.

Uso (.venv-ml):
    .venv-ml/Scripts/python -m ingest.simulate --per-persona 20 --rounds 8 --variants A,B,C,D,E
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import recommender  # noqa: E402
from ingest.ranker import load_ratings  # noqa: E402
from ingest.vectors import collab_vectors  # noqa: E402

REPORT = os.path.join(recommender.BASE_DIR, 'data', 'raw', 'sim_report.json')
PERSONAS = os.path.join(recommender.BASE_DIR, 'data', 'personas.json')
PERSONA_USERS = os.path.join(recommender.BASE_DIR, 'data', 'raw', 'persona_users.parquet')
SEED = 11
MIN_VOTES = 20000  # mesmo corte do site


def log(msg):
    print(f'[sim] {msg}', flush=True)


def action_for(rating):
    if rating is None:
        return 'skip'
    if rating >= 4:
        return 'right'
    if rating <= 2.5:
        return 'left'
    return 'down'


def session(m, truth, variant, rounds, n=10):
    answers, shown = [], []
    likes_by_round = []
    for _ in range(rounds):
        chosen = recommender.rank(m, answers, n=n, min_votes=MIN_VOTES, variant=variant)[0]
        likes = 0
        for k in chosen:
            a = action_for(truth.get(k))
            likes += a == 'right'
            answers.append({'id': m.ids[k], 'action': a})
        shown.append(chosen)
        likes_by_round.append(likes)
    flat = [k for c in shown for k in c]
    known = sum(truth.get(k) is not None for k in flat)
    ils = np.mean([float((m.combined[c] @ m.combined[c].T)[np.triu_indices(len(c), 1)].mean()) for c in shown if len(c) > 1])
    first = next((i + 1 for i, x in enumerate(likes_by_round) if x), rounds + 1)
    return {
        'likes': sum(likes_by_round),
        'precision': sum(likes_by_round) / max(known, 1),          # gostei / filmes que ele conhecia
        'known_share': known / max(len(flat), 1),                    # quanto do que mostramos ele já viu
        'first_like_round': first,
        'late_likes': sum(likes_by_round[rounds // 2:]),             # aprendizado: curtidas na 2ª metade
        'diversity': 1 - ils,
        'novelty': float(np.mean([-np.log10(max(m.votes[k], 1)) for k in flat])),
        'items': flat,
        'answers': [(k, truth.get(k)) for k in flat],
    }


def personas(m, users, n_personas=6):
    """Agrupa os usuários pelo que curtem e dá um nome a cada grupo."""
    genres = sorted(g for g in m.genres if len(m.genres[g]) > 300)
    gidx = {g: np.isin(np.arange(len(m.ids)), list(m.genres[g])) for g in genres}
    years = np.zeros(len(m.ids))
    with recommender.closing(recommender.catalog.connect()) as conn:
        for iid, y in conn.execute('SELECT imdb_id, year FROM movies'):
            k = m.row.get(iid)
            if k is not None:
                years[k] = y or 0
    feats, uids = [], []
    for u, truth in users.items():
        liked = np.array([k for k, r in truth.items() if r >= 4])
        if not len(liked):
            continue
        g = [gidx[x][liked].mean() for x in genres]
        feats.append(g + [(np.median(years[liked]) - 2000) / 15, (np.log10(np.median(m.votes[liked]) + 1) - 5)])
        uids.append(u)
    X = np.array(feats)
    km = KMeans(n_personas, n_init=20, random_state=SEED).fit(X)
    mean = X.mean(axis=0)
    names = {}
    for c in range(n_personas):
        d = km.cluster_centers_[c] - mean
        top = [genres[i] for i in np.argsort(-d[:len(genres)])[:2]]
        era = 'clássicos' if d[-2] < -0.15 else 'recentes' if d[-2] > 0.15 else 'de todas as épocas'
        pop = 'blockbusters' if d[-1] > 0.1 else 'nichos' if d[-1] < -0.1 else ''
        names[c] = f'Fã de {" e ".join(top)}, {era}' + (f', {pop}' if pop else '')
    return {u: names[c] for u, c in zip(uids, km.labels_)}


def paired(a, b, rng, iters=5000):
    """Diferença média B − A, IC 95% por bootstrap e p-valor por troca aleatória de sinais."""
    d = np.asarray(b, float) - np.asarray(a, float)
    boots = d[rng.integers(0, len(d), (iters, len(d)))].mean(axis=1)
    flips = (d * rng.choice([-1, 1], (iters, len(d)))).mean(axis=1)
    return {'diff': float(d.mean()), 'lo': float(np.percentile(boots, 2.5)), 'hi': float(np.percentile(boots, 97.5)),
            'p': float((np.abs(flips) >= abs(d.mean())).mean()), 'sd': float(d.std())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--users', type=int, default=400, help='sem personas salvas: quantos usuários sortear')
    ap.add_argument('--per-persona', type=int, default=20)
    ap.add_argument('--only', help='só estas personas, ex.: P01,P14')
    ap.add_argument('--rounds', type=int, default=8)
    ap.add_argument('--variants', default=','.join(recommender.VARIANTS))
    args = ap.parse_args()
    variants = args.variants.split(',')
    t0 = time.time()

    m = recommender.model()
    pool = m.votes >= MIN_VOTES
    rng = np.random.default_rng(SEED)
    persona = None
    if os.path.exists(PERSONAS) and os.path.exists(PERSONA_USERS):
        with open(PERSONAS, encoding='utf-8') as f:
            pdefs = {p['id']: p['name'] for p in json.load(f)['personas']}
        pu = pd.read_parquet(PERSONA_USERS)
        pu = pu[pu['imdb_id'].isin(m.row)]
        pu['col'] = pu['imdb_id'].map(m.row)
        wanted = set(args.only.split(',')) if args.only else set(pdefs)
        persona, chosen = {}, set()
        for pid, g in pu[pu['persona'].isin(wanted)].groupby('persona'):
            ids = g['userId'].unique()
            for u in rng.choice(ids, size=min(args.per_persona, len(ids)), replace=False).tolist():
                persona[u] = f'{pid} {pdefs[pid]}'
                chosen.add(u)
        sub = pu[pu['userId'].isin(chosen)]
        log(f'personas salvas: {len(set(persona.values()))} × até {args.per_persona} usuários')
    else:
        r = load_ratings(m.row)
        r = r[pool[r['col'].to_numpy()]]
        counts = r.groupby('userId').size()
        eligible = counts[(counts >= 40) & (counts <= 1500)].index.to_numpy()
        chosen = set(rng.choice(eligible, size=min(args.users, len(eligible)), replace=False).tolist())
        sub = r[r['userId'].isin(chosen)]
    users = {u: dict(zip(g['col'].tolist(), g['rating'].tolist())) for u, g in sub.groupby('userId')}
    log(f'{len(users)} usuários simulados (mediana de {int(np.median([len(t) for t in users.values()]))} notas cada)')

    # Gosto coletivo sem nenhum dado dos usuários simulados.
    all_likes = load_ratings(m.row)
    train = all_likes[(all_likes['rating'] >= 4) & ~all_likes['userId'].isin(chosen)]
    m.parts['collab'] = collab_vectors(train, len(m.ids))
    stacked = np.hstack([m.parts[k] * np.sqrt(m.weights.get(k, 0)) for k in ('content', 'graph', 'collab')])
    m.combined = stacked / np.maximum(np.linalg.norm(stacked, axis=1, keepdims=True), 1e-6)

    persona = persona or personas(m, users)
    results = {v: {} for v in variants}
    for i, (u, truth) in enumerate(users.items(), 1):
        for v in variants:
            results[v][u] = session(m, truth, v, args.rounds)
        if i % 50 == 0:
            log(f'{i}/{len(users)} usuários ({time.time() - t0:.0f}s)')

    metrics = ['likes', 'precision', 'late_likes', 'first_like_round', 'known_share', 'diversity', 'novelty']
    uids = [u for u in users if u in persona]
    summary = {}
    print(f'\n== Média por sessão ({args.rounds} rodadas × 10 filmes, {len(uids)} usuários)')
    print(f'{"variante":10}' + ''.join(f'{x:>16}' for x in metrics) + '   cobertura')
    for v in variants:
        row = {x: float(np.mean([results[v][u][x] for u in uids])) for x in metrics}
        row['coverage'] = len({k for u in uids for k in results[v][u]['items']})
        summary[v] = row
        print(f'{v:10}' + ''.join(f'{row[x]:16.3f}' for x in metrics) + f'{row["coverage"]:12}')
    for v in variants:
        print(f'  {v}: {recommender.VARIANTS[v]["label"]}')

    tests = {}
    if 'A' in variants:
        print('\n== Teste pareado contra o controle A (curtidas por sessão)')
        for v in variants:
            if v == 'A':
                continue
            t = paired([results['A'][u]['likes'] for u in uids], [results[v][u]['likes'] for u in uids], rng)
            base = summary['A']['likes']
            # tamanho de um A/B real (grupos separados) para detectar essa diferença: 80% de poder, 5%
            sd_user = float(np.std([results['A'][u]['likes'] for u in uids]))
            n_ab = int(np.ceil(2 * (1.96 + 0.84) ** 2 * sd_user ** 2 / max(t['diff'] ** 2, 1e-9)))
            t['n_per_arm_real_ab'] = n_ab
            tests[v] = t
            sig = 'significativo' if t['p'] < 0.05 else 'não significativo'
            print(f'  {v} vs A: {t["diff"]:+.2f} curtidas ({t["diff"] / base:+.1%}) · IC95% [{t["lo"]:+.2f}, {t["hi"]:+.2f}] '
                  f'· p={t["p"]:.3f} ({sig}) · A/B real precisaria de ~{n_ab:,} pessoas por grupo')

    print('\n== Curtidas por sessão, por persona')
    by_persona = {}
    names = sorted(set(persona[u] for u in uids))
    print(f'{"persona":58}{"n":>4}' + ''.join(f'{v:>8}' for v in variants))
    for p in names:
        us = [u for u in uids if persona[u] == p]
        vals = {v: float(np.mean([results[v][u]['likes'] for u in us])) for v in variants}
        by_persona[p] = {'n': len(us), **vals}
        best = max(vals, key=vals.get)
        print(f'{p[:58]:58}{len(us):4}' + ''.join(f'{vals[v]:7.2f}' + ('*' if v == best else ' ') for v in variants))

    # resultado por usuário (ID anônimo do MovieLens), para o relatório citar quem foi simulado
    per_user = [{'user_id': int(u), 'persona': persona[u], 'ratings': len(users[u]),
                 'likes': {v: results[v][u]['likes'] for v in variants},
                 'precision': {v: round(results[v][u]['precision'], 3) for v in variants}} for u in uids]
    # ---- por faixa de conhecimento e por cluster de filmes (ingest/fame.py, ingest/film_clusters.py)
    from ingest.db import connect as db_connect
    conn = db_connect()
    groups = {}
    for name, sql in (('tier', 'SELECT imdb_id, tier FROM movie_fame'), ('cluster', 'SELECT imdb_id, name FROM movie_cluster')):
        try:
            groups[name] = {m.row[i]: g for i, g in conn.execute(sql) if i in m.row}
        except Exception:  # noqa: BLE001 — tabela ainda não gerada
            pass
    breakdown = {}
    for name, gmap in groups.items():
        breakdown[name] = {}
        print(f'\n== Por {"faixa de conhecimento" if name == "tier" else "cluster de filmes"}: '
              'exibição (% da lista) · já vistos · precisão (gostou entre os já vistos)')
        for v in variants:
            agg = {}
            for u in uids:
                for k, r in results[v][u]['answers']:
                    g = gmap.get(k, 'sem dado')
                    a = agg.setdefault(g, [0, 0, 0])
                    a[0] += 1
                    a[1] += r is not None
                    a[2] += r is not None and r >= 4
            total = sum(a[0] for a in agg.values())
            breakdown[name][v] = {g: {'shown': a[0], 'share': a[0] / total, 'known': a[1] / a[0],
                                      'precision': a[2] / a[1] if a[1] else None, 'likes': a[2]} for g, a in agg.items()}
            if name == 'tier':
                print(f'  {v}: ' + ' | '.join(f'{g}: {b["share"]:.0%} · {b["known"]:.0%} · '
                                              + (f'{b["precision"]:.0%}' if b["precision"] is not None else '—')
                                              for g, b in sorted(breakdown[name][v].items())))
        if name == 'cluster':
            for g in sorted({g for v in variants for g in breakdown[name][v]}):
                row = [breakdown[name][v].get(g) for v in variants]
                print(f'  {g[:44]:44} ' + ' '.join(f'{v}:{b["share"]:4.0%}/{(b["precision"] or 0):3.0%}' if b else f'{v}:   —   '
                                                    for v, b in zip(variants, row)))
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, 'w', encoding='utf-8') as f:
        json.dump({'users': len(uids), 'rounds': args.rounds, 'summary': summary, 'tests': tests,
                   'personas': by_persona, 'per_user': per_user, 'breakdown': breakdown, 'labels': {v: recommender.VARIANTS[v]['label'] for v in variants}},
                  f, ensure_ascii=False, indent=2)
    log(f'relatório em {REPORT} · {time.time() - t0:.0f}s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
