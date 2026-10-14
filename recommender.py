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
    'F': {'label': 'Inclui menos conhecidos (2 mil votos ou mais)', 'min_votes': 2000},
    'G': {'label': 'Cota de menos conhecidos (3 vagas em 10)', 'longtail': (2, 5, 8)},
    'H': {'label': 'Exploração pelo perfil, não pela nota geral', 'explore_by': 'relevance'},
    'I': {'label': 'Exploração pelo perfil + 2 vagas de menos conhecidos premiados ou indicados', 'explore_by': 'relevance',
          'longtail': (3, 7), 'quality': True},
}
AB_ARMS = ('A', 'H', 'I')  # variantes sorteadas entre os visitantes; as demais só pelo seletor
SIGNALS = os.path.join(BASE_DIR, 'data', 'film_signals.npz')
EASE = os.path.join(BASE_DIR, 'data', 'ease.npz')
EASE_POOL = 100  # dos candidatos da ordenação, até 100 vêm do EASE (modelo versão 3)
TIER_LABEL = {0: 'Pouco conhecido', 1: 'Médio', 2: 'Muito conhecido'}
LONGTAIL_MIN_VOTES = 500  # piso da cota de menos conhecidos (todo o catálogo do site)
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
        # Sinais de conhecimento e qualidade (ingest/signals.py)
        n = len(self.ids)
        self.tier = np.full(n, -1, dtype=np.int8)
        self.wins = np.zeros(n, dtype=np.int16)
        self.noms = np.zeros(n, dtype=np.int16)
        self.mubi = np.zeros(n, dtype=bool)
        self.franchise = np.zeros(n, dtype=bool)
        if os.path.exists(SIGNALS):
            sg = np.load(SIGNALS, allow_pickle=False)
            rows = np.array([self.row.get(str(i), -1) for i in sg['ids']])
            ok = rows >= 0
            for name in ('tier', 'wins', 'noms', 'mubi', 'franchise'):
                getattr(self, name)[rows[ok]] = sg[name][ok]
        # EASE (ingest/ease.py): gosto coletivo item a item, só os 100 vizinhos mais fortes de cada filme
        self.ease = None
        if os.path.exists(EASE):
            import scipy.sparse as sp
            e = np.load(EASE, allow_pickle=False)
            rows = np.array([self.row.get(str(i), -1) for i in e['item_ids']])
            self.ease_items = rows
            self.ease_pos = np.full(len(self.ids), -1, dtype=np.int64)
            self.ease_pos[rows[rows >= 0]] = np.flatnonzero(rows >= 0)
            self.ease = sp.csr_matrix((e['data'], e['indices'], e['indptr']), shape=tuple(e['shape']))
        self.script = None
        if os.path.exists(SCRIPTS):
            sd = np.load(SCRIPTS, allow_pickle=False)
            self.script = np.zeros((len(self.ids), sd['vectors'].shape[1]), dtype=np.float32)
            for iid, v in zip(sd['ids'], sd['vectors']):
                k = self.row.get(str(iid))
                if k is not None:
                    self.script[k] = v


def ease_scores(m, liked):
    """Pontuação EASE para todo o catálogo (média das linhas dos filmes curtidos; zero fora do EASE)."""
    out = np.zeros(len(m.ids), dtype=np.float32)
    if m.ease is None or not liked:
        return out
    r = m.ease_pos[np.asarray(liked, dtype=np.int64)]
    r = r[r >= 0]
    if len(r):
        vals = np.asarray(m.ease[r].sum(axis=0)).ravel() / len(r)
        ok = m.ease_items >= 0
        out[m.ease_items[ok]] = vals[ok]
    return out


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
    min_votes = opt.get('min_votes', min_votes)
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
    ease = None
    r_cfg = _load_ranker()
    if positives and opt.get('ranker', True) and r_cfg is not None and r_cfg['version'] >= 3 and m.ease is not None:
        # parte dos candidatos vem do EASE, como no treino (ingest/ranker.py)
        ease = ease_scores(m, positives)
        ev = ease[candidates]
        base_keep = list(top[:POOL - EASE_POOL])
        kept = set(base_keep)
        extra = [k for k in candidates[np.argsort(-ev)[:EASE_POOL * 2]] if ease[k] > 0 and k not in kept][:EASE_POOL]
        top = np.array(base_keep + extra + [k for k in top[POOL - EASE_POOL:] if k not in set(extra)][:EASE_POOL - len(extra)])
    negatives = [k for k, r in answered if r < 0]
    learned = learned_order(m, positives, negatives, top, ease) if opt.get('ranker', True) else None
    info = {'cands': learned[0], 'X': learned[2]} if learned is not None else None
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
        is_far = distance > np.quantile(distance, 0.8)
        far = candidates[is_far]
        # padrão: o mais bem avaliado entre os distantes; 'relevance': o que mais combina com o perfil entre eles
        key = relevance[is_far] if opt.get('explore_by') == 'relevance' else m.prior[far]
        far = [k for k in far[np.argsort(-key)] if k not in chosen][:len(EXPLORE_SLOTS)]
        slots = opt.get('explore', EXPLORE_SLOTS)
        far = far[:len(slots)]
        for slot, k in zip(slots, far):
            if slot < len(chosen):
                chosen[slot] = k
                explored.add(k)

    # Cota de menos conhecidos: vagas fixas para filmes abaixo do corte de votos, pela afinidade com o perfil
    # (sem o conhecimento prévio de popularidade, que os empurraria para o fim da fila).
    if opt.get('longtail') and positives:
        lmask = (m.votes >= LONGTAIL_MIN_VOTES) & (m.votes < min_votes)
        if opt.get('quality'):  # pouco ou médio conhecidos, premiados ou indicados, originais (hipóteses H9 e H18)
            lmask &= (m.tier >= 0) & (m.tier < 2) & ((m.wins + m.noms) > 0) & ~m.franchise
        if seen | skipped:
            lmask[list(seen | skipped)] = False
        lc = np.flatnonzero(lmask)
        rel = np.zeros(len(lc), dtype=np.float32)
        for name, w in m.weights.items():
            if profiles.get(name) is not None:
                rel += w * (m.parts[name][lc] @ profiles[name])
        picks = [k for k in lc[np.argsort(-rel)] if k not in chosen][:len(opt['longtail'])]
        for slot, k in zip(opt['longtail'], picks):
            if slot < len(chosen):
                chosen[slot] = k
                explored.add(k)

    return chosen, explored, candidates, by_part, positives, info


def recommend(answers, n=10, genre=None, min_votes=20000, variant='A'):
    m = model()
    chosen, explored, candidates, by_part, positives, info = rank(m, answers, n, genre, min_votes, variant)
    strength = explain(info, chosen)
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
        if k in explored and m.votes[k] < min_votes:
            honors = []
            if m.wins[k]:
                honors.append(f'{m.wins[k]} prêmio{"s" if m.wins[k] > 1 else ""}')
            if m.noms[k]:
                honors.append(f'{m.noms[k]} indicaç{"ões" if m.noms[k] > 1 else "ão"}')
            why = ('Menos conhecido: pouca gente viu, mas combina com o que você curtiu'
                   + (f' e tem {", ".join(honors)}.' if honors else '.'))
            source = 'menos_conhecido'
        elif k in explored:
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
        out.append({**movie, 'why': why, 'source': source, **strength.get(k, {'strength': None, 'signals': []}),
                    'tier': TIER_LABEL.get(int(m.tier[k])),
                    'awards': int(m.wins[k]), 'nominations': int(m.noms[k]), 'mubi': bool(m.mubi[k])})
    return out


# ------------------------------------------------------------------ ordenação aprendida (LightGBM)
# Treinada por ingest/ranker.py; reordena os candidatos do híbrido com ~30 sinais.
import math  # noqa: E402

FEATURES = os.path.join(BASE_DIR, 'data', 'reco_features.npz')
RANKER = os.path.join(BASE_DIR, 'data', 'ranker.txt')
NEG_GROUPS = ['genre', 'director', 'cast', 'theme', 'ptag']
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
        with open(CONFIG, encoding='utf-8') as fh:
            version = int((json.load(fh).get('ranker') or {}).get('feature_version', 1))
        _ranker = {'model': lgb.Booster(model_file=RANKER), 'tables': tables, 'numeric': f['numeric'], 'version': version}
    return _ranker


def learned_order(m, liked, disliked, cands, ease=None):
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
    if r['version'] >= 2:
        # "não gostei" (N1) e curtidas recentes (N3); mesma conta de ingest/ranker.py (Featurizer)
        for k in ('content', 'graph', 'collab'):
            if disliked:
                p = m.parts[k][disliked].mean(axis=0)
                cols.append(m.parts[k][cands] @ (p / max(np.linalg.norm(p), 1e-9)))
            else:
                cols.append(np.zeros(len(cands), dtype=np.float32))
        for g in NEG_GROUPS:
            t = r['tables'][g]
            cols.append(t[cands] @ (np.asarray(t[disliked].sum(axis=0)).ravel() / len(disliked)) if disliked
                        else np.zeros(len(cands), dtype=np.float32))
        w = np.exp(np.linspace(-2, 0, len(liked))).astype(np.float32)  # respostas em ordem de tempo
        for k in ('content', 'graph', 'collab'):
            p = (m.parts[k][liked] * w[:, None]).sum(axis=0)
            cols.append(m.parts[k][cands] @ (p / max(np.linalg.norm(p), 1e-9)))
    if r['version'] >= 3:
        e = ease if ease is not None else ease_scores(m, liked)
        cols += [e[cands], (e[cands] != 0).astype(np.float32)]
    X = np.column_stack(cols).astype(np.float32)
    scores = r['model'].predict(X)
    order = np.argsort(-scores)
    return cands[order], scores[order], X[order]


# ------------------------------------------------------------------ força e sinais de cada recomendação
# Mesma ordem dos sinais de ingest/ranker.py (FEATURE_NAMES). None = não mostrar (constante por pessoa).
SIGNAL_LABELS = (
    ['História parecida com o que você curtiu', 'Mesma equipe, temas ou estúdio', 'Quem tem gosto parecido curtiu',
     'Muito parecido com um filme que você curtiu', 'Parecido com vários filmes que você curtiu',
     'Parecido com algo que você não gostou']
    + ['Gêneros que você curte', 'Época que você curte', 'Idioma que você vê', 'Universo e tom', 'Estilo visual',
       'Temas e palavras-chave', 'Direção', 'Roteiro', 'Elenco principal', 'Fotografia, trilha e montagem',
       'Percepção do público', 'Produção', 'Elenco']
    + ['Popularidade', 'Bem avaliado (CineScore)', 'Ano do filme', 'Duração', 'Crítica', 'Oscar', 'Bilheteria',
       'Dados de gosto coletivo']
    + [None, None]
    + ['Lembra o que você não gostou (história)', 'Lembra o que você não gostou (equipe e temas)',
       'Lembra o que você não gostou (gosto coletivo)', 'Gêneros que você evita', 'Direção que você evita',
       'Elenco que você evita', 'Temas que você evita', 'Percepção que você evita']
    + ['Combina com suas curtidas recentes (história)', 'Combina com suas curtidas recentes (equipe e temas)',
       'Quem curtiu o que você curtiu por último também curtiu']
    + ['Quem curtiu os mesmos filmes que você também curtiu este (EASE)', None])


def explain(info, chosen):
    """Força (0–100: posição entre os candidatos ordenados) e os sinais que mais pesaram, pela contribuição
    de cada sinal na pontuação do LightGBM (pred_contrib, valores SHAP do próprio modelo)."""
    if info is None:
        return {}
    pos = {k: i for i, k in enumerate(info['cands'])}
    rows = [pos[k] for k in chosen if k in pos]
    if not rows:
        return {}
    contrib = _load_ranker()['model'].predict(info['X'][rows], pred_contrib=True)[:, :-1]
    n = len(info['cands'])
    out = {}
    for k, c in zip([k for k in chosen if k in pos], contrib):
        total = np.abs(c).sum() or 1.0
        items = [(SIGNAL_LABELS[j], float(c[j]) / total) for j in range(len(c)) if SIGNAL_LABELS[j]]
        plus = [x for x in sorted(items, key=lambda x: -x[1]) if x[1] > 0.04][:4]
        minus = [x for x in sorted(items, key=lambda x: x[1]) if x[1] < -0.06][:1]
        out[k] = {'strength': int(round(100 * (1 - pos[k] / max(n - 1, 1)))),
                  'signals': [{'label': l, 'weight': round(float(w), 3)} for l, w in plus + minus]}
    return out
