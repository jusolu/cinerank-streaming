"""
Hipóteses 7 e 8, com o MovieLens Personality 2018 (Nguyen et al., Inf Syst Front 2018).

Cada pessoa respondeu um teste de personalidade (Big Five, notas de 1 a 7) e recebeu uma lista de
12 filmes montada com nível alto, médio ou baixo de UMA característica: popularidade, diversidade
ou serendipidade. Depois disse o quanto gostaria de assistir (enjoy_watching, 1 a 5).

  H7  Pessoas abertas a experiências novas gostam mais de listas pouco populares, diversas e
      surpreendentes; as demais preferem listas populares.
  H8  Quem é mais aberto já assiste mais filmes menos conhecidos (pelo histórico de notas).

O conjunto não pode ser redistribuído: fica só em data/raw. Resultados em data/raw/hypotheses_personality.json.
    .venv-ml/Scripts/python -m ingest.hypotheses_personality
"""
import json
import os
import sys

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu, spearmanr

from ingest.db import BASE_DIR, connect
from ingest.vectors import ML_DIR

P = os.path.join(BASE_DIR, 'data', 'raw', 'grouplens', 'personality-isf2018', 'personality-isf2018')
OUT = os.path.join(BASE_DIR, 'data', 'raw', 'hypotheses_personality.json')
METRIC = {'popularity': 'popularidade', 'diversity': 'diversidade', 'serendipity': 'serendipidade'}
COND = {'high': 'alta', 'medium': 'média', 'low': 'baixa'}


def main():
    p = pd.read_csv(os.path.join(P, 'personality-data.csv'), skipinitialspace=True)
    p.columns = [c.strip() for c in p.columns]
    p['metric'] = p['assigned metric'].str.strip()
    p['cond'] = p['assigned condition'].str.strip()
    p = p[p['metric'].isin(METRIC) & p['cond'].isin(COND)]
    p['aberto'] = np.where(p.openness >= p.openness.median() + 0.5, 'mais aberto',
                           np.where(p.openness <= p.openness.median() - 0.5, 'menos aberto', None))
    print(f'== {len(p):,} pessoas · abertura mediana {p.openness.median():.1f} (escala 1 a 7)')
    res = {'n': int(len(p)), 'openness_median': float(p.openness.median()), 'H7': {}}

    print('\n== H7 · Vontade de assistir à lista (1 a 5), por característica, nível e abertura')
    for m, mname in METRIC.items():
        sub = p[p.metric == m]
        tab = sub.pivot_table(index='cond', columns='aberto', values='enjoy_watching', aggfunc='mean').reindex(list(COND))
        n = sub.pivot_table(index='cond', columns='aberto', values='enjoy_watching', aggfunc='size').reindex(list(COND))
        tab.index = [COND[i] for i in tab.index]
        # interação: (alto − baixo) entre os mais abertos menos (alto − baixo) entre os menos abertos
        def eff(group):
            g = sub[sub.aberto == group]
            return g[g.cond == 'high'].enjoy_watching.mean() - g[g.cond == 'low'].enjoy_watching.mean()
        inter = eff('mais aberto') - eff('menos aberto')
        rho = spearmanr(sub.openness, sub.enjoy_watching).correlation
        hi_open = sub[(sub.cond == 'high') & (sub.aberto == 'mais aberto')].enjoy_watching
        hi_closed = sub[(sub.cond == 'high') & (sub.aberto == 'menos aberto')].enjoy_watching
        pval = mannwhitneyu(hi_open, hi_closed).pvalue if len(hi_open) > 5 and len(hi_closed) > 5 else None
        print(f'\n  {mname} ({len(sub)} pessoas)')
        print(tab.round(2).to_string())
        print(f'   efeito de "{mname} alta" em vez de baixa: mais abertos {eff("mais aberto"):+.2f}, menos abertos {eff("menos aberto"):+.2f} '
              f'(diferença {inter:+.2f}; nível alto, abertos × não abertos p={pval:.3f})' if pval else '')
        res['H7'][mname] = {'table': {str(i): {str(k): (None if pd.isna(v) else float(v)) for k, v in row.items()} for i, row in tab.iterrows()},
                            'n_table': {COND[i]: {str(k): (None if pd.isna(v) else int(v)) for k, v in row.items()} for i, row in n.iterrows()},
                            'effect_open': float(eff('mais aberto')), 'effect_closed': float(eff('menos aberto')),
                            'interaction': float(inter), 'spearman_openness_enjoy': float(rho),
                            'p_high_open_vs_closed': None if pval is None else float(pval)}

    # H8: abertura × conhecimento médio dos filmes avaliados
    r = pd.read_csv(os.path.join(P, 'ratings.csv'), skipinitialspace=True)
    r.columns = [c.strip() for c in r.columns]
    links = pd.read_csv(os.path.join(ML_DIR, 'links.csv'), dtype={'imdbId': str})
    links['imdb_id'] = 'tt' + links['imdbId'].str.zfill(7)
    fame = pd.DataFrame(connect().execute('SELECT imdb_id, fame, tier FROM movie_fame').fetchall(), columns=['imdb_id', 'fame', 'tier'])
    r = r.merge(links[['movieId', 'imdb_id']], left_on='movie_id', right_on='movieId').merge(fame, on='imdb_id')
    u = r.groupby('useri').agg(fame=('fame', 'mean'), less_known=('tier', lambda t: (t != 'Muito conhecido').mean()), n=('fame', 'size'))
    u = u[u.n >= 20].join(p.set_index('userid')[['openness', 'extraversion', 'agreeableness', 'conscientiousness', 'emotional_stability']], how='inner')
    print(f'\n== H8 · Personalidade × conhecimento dos filmes avaliados ({len(u):,} pessoas com 20+ notas no catálogo)')
    h8 = {}
    for trait in ['openness', 'extraversion', 'agreeableness', 'conscientiousness', 'emotional_stability']:
        rho = spearmanr(u[trait], u.less_known).correlation
        h8[trait] = float(rho)
        print(f'   {trait:20} Spearman com a fração de filmes menos conhecidos no histórico: {rho:+.3f}')
    q = pd.qcut(u.openness, 3, labels=['abertura baixa', 'abertura média', 'abertura alta'], duplicates='drop')
    by = u.groupby(q, observed=True).less_known.mean()
    print('   fração de menos conhecidos por terço de abertura: ' + ', '.join(f'{k} {v:.1%}' for k, v in by.items()))
    res['H8'] = {'n': int(len(u)), 'spearman': h8, 'less_known_by_openness': {str(k): float(v) for k, v in by.items()}}
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(f'\n[personalidade] resultados em {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
