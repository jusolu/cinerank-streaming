"""
Motor de recomendação do backend (/api/recommend).

Usa os vetores gerados por ingest/vectors.py (data/reco_vectors.npz):
  content (sinopse), graph (equipe/temas/estúdio/universo), collab (gosto coletivo do MovieLens),
com os pesos escolhidos na avaliação offline (data/reco_config.json).

A cada pedido (sem estado no servidor; as respostas vêm do navegador):
  1. Perfil do usuário por visão: soma dos vetores dos filmes respondidos, ponderada pela resposta.
  2. Pontuação de todos os candidatos: similaridade com o perfil + conhecimento prévio (qualidade, popularidade).
  3. Diversidade (MMR) para a lista não repetir a mesma franquia/diretor.
  4. Exploração: algumas vagas para filmes bons longe de tudo o que já foi avaliado.
  5. Explicação: o filme curtido mais parecido e o que os dois têm em comum.
"""
import json
import os
import threading
from contextlib import closing

import numpy as np

import catalog

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VECTORS = os.path.join(BASE_DIR, 'data', 'reco_vectors.npz')
CONFIG = os.path.join(BASE_DIR, 'data', 'reco_config.json')

REWARDS = {'right': 1.0, 'up': 0.8, 'down': -0.5, 'left': -1.0}
NEGATIVE_SCALE = 0.5        # respostas negativas empurram menos do que as positivas puxam
MMR_LAMBDA = 0.75           # 1 = só relevância, 0 = só diversidade
POOL = 300                  # candidatos considerados na etapa de diversidade
EXPLORE_SLOTS = (4, 9)      # posições (base 0) reservadas para exploração numa lista de 10
SHARED_GROUPS = [('Direção', 'Mesma direção'), ('Fotografia', 'Mesma fotografia'), ('Roteiro', 'Mesmo roteiro'),
                 ('Trilha sonora', 'Mesma trilha'), ('Elenco', 'Elenco em comum'), ('Franquia', 'Mesma franquia'),
                 ('Universo', 'Mesmo universo'), ('Subgênero', 'Subgênero em comum'), ('Estúdio', 'Mesmo estúdio')]

# Variantes para testes A/B (site e simulador). 'A' é o controle, o que está no ar.
VARIANTS = {
    'A': {'label': 'Controle: híbrido + ordenação aprendida + diversidade + exploração'},
    'B': {'label': 'Sem ordenação aprendida (só híbrido)', 'ranker': False},
    'C': {'label': 'Sem vagas de exploração', 'explore': ()},
    'D': {'label': 'Mais diversidade (MMR 0,6)', 'mmr': 0.6},
    'E': {'label': 'Com estilo de roteiro', 'script': 0.15},
}
SCRIPTS = os.path.join(BASE_DIR, 'data', 'script_vectors.npz')

_lock = threading.Lock()
_model = None


class Model:
    def __init__(self):
        data = np.load(VECTORS, allow_pickle=False)
        self.ids = [str(i) for i in data['ids']]
        self.row = {iid: k for k, iid in enumerate(self.ids)}
        self.parts = {name: data[name].astype(np.float32) for name in ('content', 'graph', 'collab')}
        with open(CONFIG, encoding='utf-8') as f:
            cfg = json.load(f)
        self.weights = {k: float(v) for k, v in cfg['weights'].items() if v}
        self.prior_weight = float(cfg.get('prior', 0.03))
        # Vetor combinado (para diversidade e para achar o filme respondido mais parecido).
        stacked = np.hstack([self.parts[k] * np.sqrt(self.weights.get(k, 0)) for k in ('content', 'graph', 'collab')])
        norms = np.linalg.norm(stacked, axis=1, keepdims=True)
        self.combined = stacked / np.maximum(norms, 1e-6)

        votes = np.zeros(len(self.ids))
        quality = np.zeros(len(self.ids), dtype=np.float32)
        self.genres = {}
        with closing(catalog.connect()) as conn:
            for iid, v, q in conn.execute('SELECT imdb_id, imdb_votes, cinescore FROM movies'):
                k = self.row.get(iid)
                if k is not None:
                    votes[k], quality[k] = v or 0, q or 0
            for iid, g in conn.execute('SELECT imdb_id, genre FROM movie_genres'):
                k = self.row.get(iid)
                if k is not None:
                    self.genres.setdefault(g, set()).add(k)
        self.votes = votes
        pop = np.log10(votes + 1)
        self.prior = (0.5 * (pop - pop.mean()) / pop.std() + 0.5 * (quality - quality.mean()) / (quality.std() + 1e-6)).astype(np.float32)
        # Perfil de roteiro (ingest/script_patterns.py): vetor padronizado por filme; zero onde não há roteiro.
        self.script = None
        if os.path.exists(SCRIPTS):
            sd = np.load(SCRIPTS, allow_pickle=False)
            self.script = np.zeros((len(self.ids), sd['vectors'].shape[1]), dtype=np.float32)
            for iid, v in zip(sd['ids'], sd['vectors']):
                k = self.row.get(str(iid))
                if k is not None:
                    self.script[k] = v


def model():
    global _model
    with _lock:
        if _model is None:
            _model = Model()
        return _model


def available():
    return os.path.exists(VECTORS) and os.path.exists(CONFIG)


def _profile(m, answered):
    """Perfil por visão: média ponderada pelas respostas (positivas puxam, negativas empurram)."""
    profiles = {}
    for name, vecs in m.parts.items():
        acc = np.zeros(vecs.shape[1], dtype=np.float32)
        for k, r in answered:
            acc += (r if r > 0 else r * NEGATIVE_SCALE) * vecs[k]
        n = np.linalg.norm(acc)
        profiles[name] = acc / n if n > 1e-6 else None
    return profiles


def _shared(a, b):
    """O que dois filmes têm em comum, a partir do pacote de atributos."""
    fa, fb = a.get('facets') or {}, b.get('facets') or {}
    for group, label in SHARED_GROUPS:
        common = [v for v in fa.get(group, []) if v in fb.get(group, [])]
        if common:
            return f'{label}: {", ".join(common[:2])}'
    return None


def rank(m, answers, n=10, genre=None, min_votes=20000, variant='A'):
    """Escolhe os filmes (índices). Devolve (escolhidos, explorados, candidatos, sinais por visão, positivos)."""
    opt = VARIANTS.get(variant, VARIANTS['A'])
    mmr = opt.get('mmr', MMR_LAMBDA)
    answered = [(m.row[a['id']], REWARDS[a['action']]) for a in answers
                if a.get('id') in m.row and a.get('action') in REWARDS]
    seen = {k for k, _ in answered}
    # "pular" (não conhece / não quer opinar): sai da fila, mas não conta como gosto
    skipped = {m.row[a['id']] for a in answers if a.get('action') == 'skip' and a.get('id') in m.row}
    positives = [k for k, r in answered if r > 0]

    mask = m.votes >= min_votes
    if genre and genre in m.genres:
        gmask = np.zeros(len(m.ids), dtype=bool)
        gmask[list(m.genres[genre])] = True
        mask &= gmask
    if seen | skipped:
        mask[list(seen | skipped)] = False
    candidates = np.flatnonzero(mask)
    if not len(candidates):
        return [], set(), candidates, {}, positives

    profiles = _profile(m, answered)
    relevance = m.prior_weight * 10 * m.prior[candidates] if not positives else m.prior_weight * m.prior[candidates]
    by_part = {}
    for name, w in m.weights.items():
        p = profiles.get(name)
        if p is not None:
            by_part[name] = m.parts[name][candidates] @ p
            relevance = relevance + w * by_part[name]

    # Diversidade (MMR) sobre os melhores candidatos.
    top = candidates[np.argsort(-relevance)[:POOL]]
    rel_top = dict(zip(candidates, relevance))
    negatives = [k for k, r in answered if r < 0]
    learned = learned_order(m, positives, negatives, top) if opt.get('ranker', True) else None
    if learned is not None:
        # Relevância passa a ser a posição na ordem aprendida (0 a 1), comparável à redundância do MMR.
        top = learned[0]
        rel_top = {k: 1 - i / len(top) for i, k in enumerate(top)}
    elif not opt.get('ranker', True):  # escala 0–1, como a ordem aprendida (o controle A fica como está no ar)
        r = np.array([rel_top[k] for k in top])
        rel_top = dict(zip(top, (r - r.min()) / max(r.max() - r.min(), 1e-9)))
    if opt.get('script') and m.script is not None and positives:
        # Bônus para quem tem estilo de roteiro parecido com o dos filmes curtidos (só onde há roteiro).
        prof = m.script[positives].sum(axis=0)
        if np.linalg.norm(prof) > 1e-6:
            prof /= np.linalg.norm(prof)
            sim = m.script[top] @ prof
            rel_top = {k: v + opt['script'] * float(sim[i]) for i, (k, v) in enumerate(zip(top, (rel_top[k] for k in top)))}
    chosen = []
    while len(chosen) < n and len(chosen) < len(top):
        best, best_score = None, -np.inf
        for k in top:
            if k in chosen:
                continue
            redundancy = max((float(m.combined[k] @ m.combined[c]) for c in chosen), default=0.0)
            score = mmr * rel_top[k] - (1 - mmr) * redundancy
            if score > best_score:
                best, best_score = k, score
        chosen.append(best)

    # Exploração: troca algumas vagas por filmes bem avaliados longe de tudo o que foi respondido.
    explored = set()
    if seen:
        answered_vecs = m.combined[list(seen)]
        distance = 1 - (m.combined[candidates] @ answered_vecs.T).max(axis=1)
        far = candidates[(distance > np.quantile(distance, 0.8))]
        far = [k for k in far[np.argsort(-m.prior[far])] if k not in chosen][:len(EXPLORE_SLOTS)]
        slots = opt.get('explore', EXPLORE_SLOTS)
        far = far[:len(slots)]
        for slot, k in zip(slots, far):
            if slot < len(chosen):
                chosen[slot] = k
                explored.add(k)

    return chosen, explored, candidates, by_part, positives


def recommend(answers, n=10, genre=None, min_votes=20000, variant='A'):
    m = model()
    chosen, explored, candidates, by_part, positives = rank(m, answers, n, genre, min_votes, variant)
    if not chosen:
        return []
    # Detalhes dos filmes e explicação.
    details = {mv['imdb_id']: mv for mv in catalog.movies_by_ids([m.ids[k] for k in chosen] +
                                                                  [m.ids[k] for k in positives])}
    out = []
    for k in chosen:
        movie = details.get(m.ids[k])
        if not movie:
            continue
        if k in explored:
            why = 'Explorando: um filme bem avaliado diferente de tudo o que você avaliou até agora.'
            source = 'exploracao'
        elif not positives:
            why = 'Começando pelos filmes mais conhecidos e bem avaliados. A fila se ajusta às suas respostas.'
            source = 'popular'
        else:
            sims = m.combined[positives] @ m.combined[k]
            ref = details.get(m.ids[positives[int(np.argmax(sims))]])
            idx = int(np.where(candidates == k)[0][0]) if len(by_part) else None
            dominant = max(by_part, key=lambda p: m.weights[p] * by_part[p][idx]) if idx is not None else None
            shared = _shared(ref, movie) if ref else None
            if dominant == 'collab' and ref:
                why = f'Quem gostou de {ref["title"]} também gostou deste.'
            elif ref:
                why = f'Porque você gostou de {ref["title"]}.'
            else:
                why = 'Combina com o seu perfil.'
            if shared:
                why += f' {shared}.'
            source = dominant or 'perfil'
        out.append({**movie, 'why': why, 'source': source})
    return out


# ------------------------------------------------------------------ ordenação aprendida (LightGBM)
# Treinada por ingest/ranker.py; reordena os candidatos do híbrido com ~30 sinais.
import math  # noqa: E402

FEATURES = os.path.join(BASE_DIR, 'data', 'reco_features.npz')
RANKER = os.path.join(BASE_DIR, 'data', 'ranker.txt')
TABLE_GROUPS = ['genre', 'decade', 'lang', 'tone', 'visual', 'theme', 'director', 'writer', 'cast', 'crew',
                'ptag', 'producer', 'cast_all']
_ranker = None


def _load_ranker():
    global _ranker
    if _ranker is None and os.path.exists(RANKER) and os.path.exists(FEATURES):
        import lightgbm as lgb
        import scipy.sparse as sp
        f = np.load(FEATURES, allow_pickle=False)
        tables = {g: sp.csr_matrix((f[f'tbl_{g}_data'], f[f'tbl_{g}_indices'], f[f'tbl_{g}_indptr']),
                                   shape=tuple(f[f'tbl_{g}_shape'])) for g in TABLE_GROUPS}
        _ranker = {'model': lgb.Booster(model_file=RANKER), 'tables': tables, 'numeric': f['numeric']}
    return _ranker


def learned_order(m, liked, disliked, cands):
    """Reordena candidatos com o LightGBM; mesmos sinais e mesma ordem de ingest/ranker.py."""
    r = _load_ranker()
    if r is None or not liked:
        return None
    cands = np.asarray(cands)
    cols = []
    for k in ('content', 'graph', 'collab'):
        v = m.parts[k]
        p = v[liked].mean(axis=0)
        p = p / max(np.linalg.norm(p), 1e-9)
        cols.append(v[cands] @ p)
    sim_l = m.combined[cands] @ m.combined[liked].T
    cols += [sim_l.max(axis=1), sim_l.mean(axis=1)]
    cols.append((m.combined[cands] @ m.combined[disliked].T).max(axis=1) if disliked
                else np.zeros(len(cands), dtype=np.float32))
    for g in TABLE_GROUPS:
        t = r['tables'][g]
        pg = np.asarray(t[liked].sum(axis=0)).ravel() / len(liked)
        cols.append(t[cands] @ pg)
    num = r['numeric'][cands]
    cols += [num[:, i] for i in range(7)] + [(np.abs(m.parts['collab'][cands]).sum(axis=1) > 0).astype(np.float32)]
    cols += [np.full(len(cands), math.log1p(len(liked))), np.full(len(cands), math.log1p(len(disliked)))]
    scores = r['model'].predict(np.column_stack(cols).astype(np.float32))
    return cands[np.argsort(-scores)], np.sort(scores)[::-1]
