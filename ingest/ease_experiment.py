"""
EASE (Steck, WWW 2019) × SVD para o gosto coletivo.

EASE é um autoencoder linear com solução fechada: B = I − P·diag(1/diag(P)), com P = (XᵀX + λI)⁻¹,
diagonal de B zerada. A pontuação de um filme para a pessoa é a soma das linhas de B dos filmes que ela
curtiu. Mesmo protocolo de ingest/vectors.py: 3.000 usuários do MovieLens com as 20% curtidas mais
recentes escondidas, recall@20 sobre todo o catálogo com vetor.

Compara: populares, SVD atual (perfil médio · vetores), EASE denso com vários λ, EASE esparso (só os
100 vizinhos mais fortes de cada filme, para caber no servidor) e uma mistura SVD + EASE.
    .venv-ml/Scripts/python -m ingest.ease_experiment
"""
import json
import os
import sys
import time

import numpy as np
import scipy.sparse as sp
from sklearn.preprocessing import normalize

from ingest.db import BASE_DIR
from ingest.vectors import TOP_K, collab_vectors, load_movielens, split_eval

REPORT = os.path.join(BASE_DIR, 'data', 'raw', 'ease_experiment.json')
MAX_ITEMS = 9000  # filmes mais curtidos (matriz densa 9.000 × 9.000 cabe na memória)


def log(msg):
    print(f'[ease] {msg}', flush=True)


def main():
    t0 = time.time()
    d = np.load(os.path.join(BASE_DIR, 'data', 'reco_vectors.npz'), allow_pickle=False)
    ids = [str(i) for i in d['ids']]
    index = {iid: k for k, iid in enumerate(ids)}
    likes = load_movielens(index)
    train, profiles, hidden = split_eval(likes)
    users = list(profiles)
    log(f'{len(users):,} usuários de teste; {len(train):,} curtidas de treino')

    counts = train.col.value_counts()
    items = counts.index[:MAX_ITEMS].to_numpy()
    pos = {c: i for i, c in enumerate(items)}
    tr = train[train.col.isin(pos)]
    uc = tr.userId.astype('category')
    X = sp.csr_matrix((np.ones(len(tr), np.float32), (uc.cat.codes.to_numpy(), tr.col.map(pos).to_numpy())),
                      shape=(len(uc.cat.categories), len(items)))
    X.data[:] = 1
    G = (X.T @ X).toarray().astype(np.float64)
    log(f'matriz {len(items):,} filmes × {X.shape[0]:,} usuários ({time.time() - t0:.0f}s)')
    covered = sum(len(h & set(items.tolist())) for h in hidden.values()) / sum(len(h) for h in hidden.values())
    log(f'curtidas escondidas cobertas pelos {len(items):,} filmes: {covered:.1%}')

    # perfis dos usuários de teste na base dos filmes do EASE
    U = sp.lil_matrix((len(users), len(items)), dtype=np.float32)
    for r, u in enumerate(users):
        for c in profiles[u]:
            if c in pos:
                U[r, pos[c]] = 1
    U = U.tocsr()

    def recall(scores_fn):
        hits = []
        for start in range(0, len(users), 500):
            block = users[start:start + 500]
            S = scores_fn(start, len(block))
            for k, u in enumerate(block):
                s = S[k]
                known = [pos[c] for c in profiles[u] if c in pos]
                s[known] = -np.inf
                top = items[np.argpartition(-s, TOP_K)[:TOP_K]]
                h = hidden[u]
                hits.append(len(h.intersection(top.tolist())) / min(TOP_K, len(h)))
        return float(np.mean(hits))

    res = {}
    pop = np.log1p(counts.reindex(items).to_numpy()).astype(np.float32)
    res['populares'] = recall(lambda s, n: np.tile(pop, (n, 1)))
    log(f'populares: {res["populares"]:.4f}')

    V = collab_vectors(train, len(ids))[items]
    P_svd = normalize(np.stack([V[[pos[c] for c in profiles[u] if c in pos]].mean(axis=0) if any(c in pos for c in profiles[u])
                                else np.zeros(V.shape[1]) for u in users]))
    res['svd (atual)'] = recall(lambda s, n: P_svd[s:s + n] @ V.T)
    log(f'svd (atual): {res["svd (atual)"]:.4f} ({time.time() - t0:.0f}s)')

    best = None
    for lam in (200, 800, 3000):
        P = np.linalg.inv(G + lam * np.eye(len(items)))
        B = (-P / np.diag(P)).astype(np.float32)
        np.fill_diagonal(B, 0)
        res[f'ease λ={lam}'] = recall(lambda s, n, B=B: (U[s:s + n] @ B))
        log(f'ease λ={lam}: {res[f"ease λ={lam}"]:.4f} ({time.time() - t0:.0f}s)')
        if best is None or res[f'ease λ={lam}'] > res[f'ease λ={best[0]}']:
            best = (lam, B)
    lam, B = best
    # versão esparsa: 100 vizinhos mais fortes por filme (coluna)
    k = 100
    idx = np.argpartition(-np.abs(B), k, axis=0)[:k]
    rows = idx.ravel(order='F')
    cols = np.repeat(np.arange(B.shape[1]), k)
    Bs = sp.csr_matrix((B[idx, np.arange(B.shape[1])].ravel(order='F'), (rows, cols)), shape=B.shape)
    res[f'ease esparso top{k} (λ={lam})'] = recall(lambda s, n: (U[s:s + n] @ Bs).toarray())
    size_mb = (Bs.data.nbytes + Bs.indices.nbytes + Bs.indptr.nbytes) / 1e6
    log(f'ease esparso: {res[f"ease esparso top{k} (λ={lam})"]:.4f} · {size_mb:.0f} MB')
    for a in (0.3, 0.5, 0.7):
        def mix(s, n, a=a):
            e = np.asarray((U[s:s + n] @ B))
            e = (e - e.mean(axis=1, keepdims=True)) / (e.std(axis=1, keepdims=True) + 1e-9)
            v = P_svd[s:s + n] @ V.T
            v = (v - v.mean(axis=1, keepdims=True)) / (v.std(axis=1, keepdims=True) + 1e-9)
            return a * e + (1 - a) * v
        res[f'mistura {int(a * 100)}% ease + svd'] = recall(mix)
        log(f'mistura {a:.0%} ease: {res[f"mistura {int(a * 100)}% ease + svd"]:.4f}')
    res['_meta'] = {'items': int(len(items)), 'covered_hidden': covered, 'best_lambda': lam, 'sparse_mb': size_mb,
                    'users': len(users)}
    with open(REPORT, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    log(f'concluído em {time.time() - t0:.0f}s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
