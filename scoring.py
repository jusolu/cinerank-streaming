"""
CineScore: média ponderada das fontes disponíveis, ajustada por média bayesiana.

    raw       = Σ w_i · nota_i / Σ w_i              (só fontes com dado real, escala 0-100)
    cinescore = (v · raw + m · C) / (v + m)         (v = votos, m = prior_votes, C = prior_mean)

Títulos com poucos votos são puxados para C, o que impede que um filme com
poucos votos e nota alta lidere o ranking.
"""

SOURCE_WEIGHTS = {'tomatometer': 0.30, 'metacritic': 0.20, 'imdb': 0.30, 'tmdb': 0.20}

# Padrões para o catálogo completo (ver ingest/imdb.py, onde C é recalculado a cada carga).
PRIOR_MEAN = 58.0
PRIOR_VOTES = 10000
LOW_CONFIDENCE_VOTES = 1000


def calculate_cinescore(scores, prior_votes=PRIOR_VOTES, prior_mean=PRIOR_MEAN):
    """Retorna (cinescore, raw, votos). `scores` usa as chaves de SOURCE_WEIGHTS mais imdb_votes/tmdb_votes."""
    normalized = {
        'tomatometer': scores.get('tomatometer'),
        'metacritic': scores.get('metacritic'),
        'imdb': scores['imdb'] * 10 if scores.get('imdb') is not None else None,
        'tmdb': scores['tmdb'] * 10 if scores.get('tmdb') is not None else None,
    }
    available = {k: float(v) for k, v in normalized.items() if v is not None}
    if not available:
        return None, None, 0

    total_w = sum(SOURCE_WEIGHTS[k] for k in available)
    raw = sum(SOURCE_WEIGHTS[k] * v for k, v in available.items()) / total_w

    votes = max(scores.get('imdb_votes') or 0, scores.get('tmdb_votes') or 0)
    cinescore = (votes * raw + prior_votes * prior_mean) / (votes + prior_votes)
    return round(cinescore, 1), round(raw, 1), votes


def apply_cinescore(scores, prior_votes=PRIOR_VOTES, prior_mean=PRIOR_MEAN, low_confidence_votes=LOW_CONFIDENCE_VOTES):
    cinescore, raw, votes = calculate_cinescore(scores, prior_votes, prior_mean)
    scores['cinescore'] = cinescore
    scores['raw_score'] = raw
    scores['votes'] = votes
    scores['low_confidence'] = votes < low_confidence_votes
    return scores
