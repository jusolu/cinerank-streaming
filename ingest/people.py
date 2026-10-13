"""
Junta aos créditos as pessoas da produção vindas do Wikidata (elenco completo, produção, roteiro,
fotografia, montagem, trilha, direção de arte, figurino), além dos ~10 principais do IMDb.

As pessoas já vêm com o ID do IMDb (nm…); os nomes saem de name.basics, já baixado.

Uso (depois de `python -m ingest.wikidata --props people_cast,...`):
    python -m ingest.people
"""
import os
import sys

from ingest.db import connect
from ingest.imdb import RAW_DIR, read_tsv

ROLE = {'people_cast': 'cast', 'people_producer': 'producer', 'people_exec_producer': 'exec_producer',
        'people_screenwriter': 'writer', 'people_photography': 'photography', 'people_editing': 'editing',
        'people_music': 'music', 'people_design': 'design', 'people_costume': 'costume'}


def main():
    conn = connect()
    marks = ','.join('?' * len(ROLE))
    found = conn.execute(f'SELECT imdb_id, prop, value FROM movie_wikidata WHERE prop IN ({marks})', tuple(ROLE)).fetchall()
    existing = set(conn.execute('SELECT imdb_id, person_id, role FROM movie_people').fetchall())
    next_ord = {}
    for iid, role, ord_ in conn.execute('SELECT imdb_id, role, MAX(ord) FROM movie_people GROUP BY imdb_id, role'):
        next_ord[(iid, role)] = ord_ + 1

    new = []
    for iid, prop, pid in sorted(found):
        role = ROLE[prop]
        if not pid.startswith('nm') or (iid, pid, role) in existing:
            continue
        key = (iid, role)
        o = next_ord.get(key, 0)
        next_ord[key] = o + 1
        new.append((iid, pid, role, o))
        existing.add((iid, pid, role))

    known = {r[0] for r in conn.execute('SELECT person_id FROM people')}
    missing = {p for _, p, _, _ in new} - known
    names = [(n, name) for n, name, *_ in read_tsv(os.path.join(RAW_DIR, 'name.basics.tsv.gz')) if n in missing and name]
    valid = known | {n for n, _ in names}
    rows = [r for r in new if r[1] in valid]
    with conn:
        conn.executemany('INSERT OR IGNORE INTO people (person_id, name) VALUES (?, ?)', names)
        conn.executemany('INSERT OR IGNORE INTO movie_people (imdb_id, person_id, role, ord) VALUES (?, ?, ?, ?)', rows)
    by_role = {}
    for _, _, role, _ in rows:
        by_role[role] = by_role.get(role, 0) + 1
    print(f'[pessoas] {len(rows):,} créditos novos ({len(names):,} pessoas novas): '
          + ', '.join(f'{r} {n:,}' for r, n in sorted(by_role.items(), key=lambda x: -x[1])), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
