"""
EASE (Steck, WWW 2019): gosto coletivo item a item com solução fechada.

  B = I − P·diag(1/diag(P)),  P = (XᵀX + λI)⁻¹,  diagonal de B zerada.
  pontuação(filme) = soma das linhas de B dos filmes que a pessoa curtiu.

Usado em dois lugares: ingest/ranker.py (treino, sem as curtidas escondidas) e o site, que lê
data/ease.npz (só os 100 vizinhos mais fortes de cada filme; ~7 MB). Avaliação em ingest/ease_experiment.py.

    .venv-ml/Scripts/python -m ingest.ease        # gera data/ease.npz com todas as curtidas do MovieLens
"""
import os
import sys
import time

import numpy as np
import scipy.sparse as sp

from ingest.db import BASE_DIR

OUT = os.path.join(BASE_DIR, 'data', 'ease.npz')
MAX_ITEMS = 9000
LAMBDA = 800
NEIGHBORS = 100


def build(likes, n_items, max_items=MAX_ITEMS, lam=LAMBDA, k=NEIGHBORS):
    """likes: DataFrame com userId e col (índice do filme no catálogo). Devolve (cols dos filmes, B esparsa k vizinhos)."""
    counts = likes.col.value_counts()
    items = counts.index[:max_items].to_numpy()
    pos = np.full(n_items, -1, dtype=np.int64)
    pos[items] = np.arange(len(items))
    tr = likes[pos[likes.col.to_numpy()] >= 0]
    uc = tr.userId.astype('category')
    X = sp.csr_matrix((np.ones(len(tr), np.float32), (uc.cat.codes.to_numpy(), pos[tr.col.to_numpy()])),
                      shape=(len(uc.cat.categories), len(items)))
    X.data[:] = 1
    G = (X.T @ X).toarray().astype(np.float64)
    P = np.linalg.inv(G + lam * np.eye(len(items)))
    B = (-P / np.diag(P)).astype(np.float32)
    np.fill_diagonal(B, 0)
    idx = np.argpartition(-np.abs(B), k, axis=0)[:k]
    Bs = sp.csr_matrix((B[idx, np.arange(B.shape[1])].ravel(order='F'),
                        (idx.ravel(order='F'), np.repeat(np.arange(B.shape[1]), k))), shape=B.shape)
    return items, Bs


class Scorer:
    """Pontuação EASE para todos os filmes do catálogo (zero onde o filme não está no EASE)."""

    def __init__(self, items, B, n_items):
        self.items = np.asarray(items)
        self.B = B.tocsr()
        self.pos = np.full(n_items, -1, dtype=np.int64)
        self.pos[self.items] = np.arange(len(self.items))
        self.n = n_items

    def __call__(self, liked):
        rows = self.pos[np.asarray(liked, dtype=np.int64)]
        rows = rows[rows >= 0]
        out = np.zeros(self.n, dtype=np.float32)
        if len(rows):
            out[self.items] = np.asarray(self.B[rows].sum(axis=0)).ravel() / len(rows)
        return out


def main():
    from ingest.vectors import load_movielens
    t0 = time.time()
    d = np.load(os.path.join(BASE_DIR, 'data', 'reco_vectors.npz'), allow_pickle=False)
    ids = np.array([str(i) for i in d['ids']])
    index = {iid: k for k, iid in enumerate(ids)}
    items, B = build(load_movielens(index), len(ids))
    np.savez_compressed(OUT, item_ids=ids[items], data=B.data, indices=B.indices, indptr=B.indptr, shape=np.array(B.shape))
    print(f'[ease] {len(items):,} filmes · {B.nnz:,} vizinhos · {os.path.getsize(OUT) / 1e6:.1f} MB em {OUT} ({time.time() - t0:.0f}s)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
