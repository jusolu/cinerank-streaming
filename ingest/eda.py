"""
Análise exploratória e hipóteses H12–H18, com correção para comparações múltiplas.

Unidade de análise: o filme (53 mil, 500+ votos no IMDb). Medida de gosto do público: nota média no
MovieLens 32M descontada a média de cada pessoa ("ml"), para filmes com 10+ notas.

Exploratória
  1. Estatística descritiva (mediana, intervalo interquartil, % ausente) das variáveis.
  2. Matriz de correlação de Spearman.
  3. Regressão linear (MQO) do gosto sobre variáveis padronizadas, com erros robustos (HC3).
Hipóteses
  H12 Retorno financeiro (bilheteria ÷ orçamento) acompanha o gosto do público.
  H13 Filmes mais longos são mais bem avaliados, mesmo comparando dentro do gênero.
  H14 Enredos que terminam melhor do que começam (arco ascendente) são mais bem avaliados.
  H15 O interesse brasileiro (Wikipédia em português ÷ inglês) é maior para filmes brasileiros e
      latino-americanos e para alguns gêneros.
  H16 Filmes antigos recebem notas maiores (só os bons "sobrevivem" para serem vistos depois).
  H17 A nota média dada pelos usuários mudou ao longo dos anos.
  H18 Filmes de franquia são menos apreciados que os originais, no mesmo nível de conhecimento.
Todos os p-valores (desta análise e das hipóteses anteriores) passam pela correção de
Benjamini-Hochberg (taxa de falsas descobertas de 5%).

    .venv-ml/Scripts/python -m ingest.eda
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import kruskal, mannwhitneyu, spearmanr
from statsmodels.stats.multitest import multipletests

from ingest.db import BASE_DIR, connect
from ingest.hypotheses_awards import film_awards
from ingest.vectors import ML_DIR

RAW = os.path.join(BASE_DIR, 'data', 'raw')
OUT = os.path.join(RAW, 'eda.json')
LATAM = {'Brasil', 'Argentina', 'México', 'Chile', 'Colômbia', 'Peru', 'Uruguai', 'Cuba', 'Venezuela', 'Portugal'}


def build(conn):
    q = lambda s: pd.DataFrame(conn.execute(s).fetchall(), columns=[d[0] for d in conn.execute(s).description])
    F = q('SELECT m.imdb_id, m.year, m.runtime, m.imdb_rating, m.imdb_votes AS votes, f.fame, f.tier, f.pageviews, f.sitelinks '
          'FROM movies m JOIN movie_fame f USING(imdb_id)').set_index('imdb_id')
    W = q('SELECT imdb_id, budget_usd, gross_usd, rt, metacritic FROM movie_wikifields').set_index('imdb_id')
    F = F.join(W)
    A = film_awards(conn)
    F = F.join(A[['wins', 'noms', 'fest_win']])
    pt = q('SELECT imdb_id, ptwiki, pt_pageviews FROM movie_ptwiki').set_index('imdb_id')
    F = F.join(pt)
    x = q('SELECT imdb_id, platform FROM movie_xref')
    F['mubi'] = F.index.isin(x[x.platform == 'mubi'].imdb_id)
    F['n_platforms'] = x.groupby('imdb_id').size().reindex(F.index).fillna(0)
    wd = q("SELECT imdb_id, prop FROM movie_wikidata WHERE prop IN ('franchise', 'based_on')")
    F['franchise'] = F.index.isin(wd[wd.prop == 'franchise'].imdb_id)
    F['based_on'] = F.index.isin(wd[wd.prop == 'based_on'].imdb_id)
    c = q('SELECT imdb_id, country FROM movie_country')
    F['brazil'] = F.index.isin(c[c.country == 'Brasil'].imdb_id)
    F['latam'] = F.index.isin(c[c.country.isin(LATAM)].imdb_id)
    P = q('SELECT imdb_id, profile FROM movie_plot_profile')
    prof = pd.DataFrame([json.loads(p) for p in P.profile], index=P.imdb_id)
    F = F.join(prof[['words', 'tone', 'end_minus_start', 'shape', 'emo_violence', 'emo_love', 'emo_fear']].rename(columns={'words': 'plot_words'}))
    g = q('SELECT imdb_id, genre FROM movie_genres')
    F['main_genre'] = g.groupby('imdb_id').genre.first().reindex(F.index)
    return F, g


def ml_film(links_path):
    links = pd.read_csv(links_path, dtype={'imdbId': str})
    links['imdb_id'] = 'tt' + links['imdbId'].str.zfill(7)
    r = pd.read_csv(os.path.join(ML_DIR, 'ratings.csv'), engine='pyarrow')
    r = r.merge(links[['movieId', 'imdb_id']], on='movieId')
    r['centered'] = r.rating - r.groupby('userId').rating.transform('mean')
    film = r.groupby('imdb_id').agg(ml=('centered', 'mean'), ml_n=('centered', 'size'))
    return r, film


def main():
    conn = connect()
    F, genres = build(conn)
    r, film = ml_film(os.path.join(ML_DIR, 'links.csv'))
    F = F.join(film)
    F.loc[F.ml_n < 10, 'ml'] = np.nan
    F['roi'] = np.log10(F.gross_usd / F.budget_usd).where((F.budget_usd >= 1e5) & (F.gross_usd > 0))
    F['pt_ratio'] = np.log10((F.pt_pageviews.fillna(0) + 1) / (F.pageviews.fillna(0) + 1)).where(F.pageviews.notna())
    res, pvals = {}, []

    # ---- 1. descritiva
    num = ['votes', 'imdb_rating', 'ml', 'year', 'runtime', 'fame', 'pageviews', 'pt_pageviews', 'sitelinks', 'n_platforms',
           'wins', 'noms', 'budget_usd', 'gross_usd', 'roi', 'metacritic', 'rt', 'plot_words', 'tone', 'end_minus_start']
    desc = pd.DataFrame({'n': F[num].notna().sum(), 'ausente': F[num].isna().mean(), 'mediana': F[num].median(),
                         'p25': F[num].quantile(.25), 'p75': F[num].quantile(.75)})
    print('== 1. Estatística descritiva (53.190 filmes)')
    print(desc.round(3).to_string())
    res['descritiva'] = desc.round(4).reset_index(names='variavel').to_dict('records')
    bools = ['franchise', 'based_on', 'mubi', 'brazil', 'latam', 'fest_win']
    res['proporcoes'] = {b: float(F[b].mean()) for b in bools}
    print('   proporções: ' + ', '.join(f'{b} {F[b].mean():.1%}' for b in bools))

    # ---- 2. correlação
    cvars = ['ml', 'imdb_rating', 'votes', 'year', 'runtime', 'fame', 'pageviews', 'pt_ratio', 'wins', 'noms', 'roi', 'metacritic',
             'plot_words', 'tone', 'end_minus_start']
    C = F[cvars].corr(method='spearman')
    print('\n== 2. Spearman com o gosto do público (ml):\n' + C['ml'].drop('ml').sort_values().round(3).to_string())
    res['corr'] = {'vars': cvars, 'matrix': C.round(3).values.tolist(),
                   'n_pairwise_ml': {v: int(F[['ml', v]].dropna().shape[0]) for v in cvars}}

    # ---- 3. regressão
    d = F[['ml', 'votes', 'year', 'runtime', 'wins', 'noms', 'mubi', 'franchise', 'based_on', 'tone', 'end_minus_start', 'plot_words', 'main_genre']].dropna()
    X = pd.DataFrame({'log_votos': np.log10(d.votes), 'ano': d.year, 'duracao': d.runtime.clip(60, 240), 'premios': np.log1p(d.wins),
                      'indicacoes': np.log1p(d.noms), 'mubi': d.mubi.astype(float), 'franquia': d.franchise.astype(float),
                      'adaptacao': d.based_on.astype(float), 'tom_enredo': d.tone, 'final_menos_inicio': d.end_minus_start,
                      'tamanho_enredo': np.log10(d.plot_words)})
    Xs = (X - X.mean()) / X.std()
    Xs = pd.concat([Xs, pd.get_dummies(d.main_genre, prefix='g', drop_first=True, dtype=float)], axis=1)
    ys = (d.ml - d.ml.mean()) / d.ml.std()
    ols = sm.OLS(ys, sm.add_constant(Xs)).fit(cov_type='HC3')
    coefs = pd.DataFrame({'beta': ols.params, 'lo': ols.conf_int()[0], 'hi': ols.conf_int()[1], 'p': ols.pvalues}).loc[X.columns]
    print(f'\n== 3. MQO do gosto (padronizado) · {len(d):,} filmes · R² {ols.rsquared:.3f} (com gênero principal como controle)')
    print(coefs.round(3).to_string())
    res['ols'] = {'n': int(len(d)), 'r2': float(ols.rsquared), 'coefs': coefs.round(4).reset_index(names='var').to_dict('records')}

    # ---- hipóteses
    def test(name, desc_, stat, p, effect, n, extra=None):
        pvals.append((name, p))
        res.setdefault('hyp', {})[name] = {'desc': desc_, 'stat': stat, 'p': p, 'effect': effect, 'n': n, **(extra or {})}
        print(f'\n== {name} · {desc_}\n   {effect} · n={n:,} · p={p:.1e}')

    s = F[['roi', 'ml']].dropna()
    rho, p = spearmanr(s.roi, s.ml)
    s2 = F[['budget_usd', 'ml']].dropna()
    rb = spearmanr(s2.budget_usd, s2.ml).correlation
    test('H12', 'Retorno (bilheteria ÷ orçamento) × gosto', float(rho), float(p),
         f'Spearman retorno {rho:+.3f}; orçamento sozinho {rb:+.3f}', len(s), {'rho_budget': float(rb)})

    s = F[['runtime', 'ml', 'main_genre']].dropna()
    s = s[(s.runtime >= 60) & (s.runtime <= 240)]
    rho, p = spearmanr(s.runtime, s.ml)
    within = s.groupby('main_genre').apply(lambda g: spearmanr(g.runtime, g.ml).correlation if len(g) > 50 else np.nan).dropna()
    test('H13', 'Duração × gosto', float(rho), float(p),
         f'Spearman geral {rho:+.3f}; dentro do gênero, mediana {within.median():+.3f} ({(within > 0).sum()} de {len(within)} gêneros positivos)',
         len(s), {'within_genre': {k: float(v) for k, v in within.round(3).items()}})

    s = F[['shape', 'ml', 'end_minus_start']].dropna()
    groups = {k: g.ml.values for k, g in s.groupby('shape') if len(g) > 30}
    H, p = kruskal(*groups.values())
    means = {k: float(np.mean(v)) for k, v in groups.items()}
    rho = spearmanr(s.end_minus_start, s.ml).correlation
    test('H14', 'Forma do arco do enredo × gosto', float(H), float(p),
         f'Kruskal-Wallis H={H:.1f}; médias {", ".join(f"{k} {v:+.3f}" for k, v in sorted(means.items(), key=lambda x: -x[1]))}; '
         f'Spearman fim−início {rho:+.3f}', len(s), {'means': means, 'n_by_shape': {k: int(len(v)) for k, v in groups.items()},
                                                     'rho_end_minus_start': float(rho)})

    s = F[F.pt_ratio.notna() & F.ptwiki.notna() & (F.pageviews > 100) & (F.pt_pageviews > 0)]  # só quem tem artigo nas duas
    a, b = s[s.brazil].pt_ratio, s[~s.latam].pt_ratio
    U, p = mannwhitneyu(a, b)
    by_genre = s.join(genres.set_index('imdb_id'), how='inner').groupby('genre').pt_ratio.median().sort_values(ascending=False)
    test('H15', 'Interesse relativo no Brasil (visualizações pt ÷ en) para filmes brasileiros', float(U), float(p),
         f'brasileiros: mediana {10 ** a.median():.2f} visualização em português por visualização em inglês; '
         f'fora da América Latina: {10 ** b.median():.3f} ({10 ** (a.median() - b.median()):.0f}×; só filmes com artigo nas duas Wikipédias)', int(len(a) + len(b)),
         {'ratio_brazil': float(10 ** a.median()), 'ratio_other': float(10 ** b.median()), 'n_brazil': int(len(a)),
          'genres_top': {k: float(10 ** v) for k, v in by_genre.head(6).items()}, 'genres_bottom': {k: float(10 ** v) for k, v in by_genre.tail(4).items()}})

    years = F.year.to_dict()
    smp = r.sample(3_000_000, random_state=0)
    smp['age'] = pd.to_datetime(smp.timestamp, unit='s').dt.year - smp.imdb_id.map(years)
    smp = smp[(smp.age >= 0) & (smp.age <= 60)]
    rho, p = spearmanr(smp.age, smp.centered)
    by_age = smp.groupby(pd.cut(smp.age, [-1, 1, 5, 10, 20, 40, 60])).centered.mean()
    test('H16', 'Idade do filme ao ser avaliado × nota (amostra de 3 milhões de notas)', float(rho), float(p),
         f'Spearman {rho:+.3f}; ' + ', '.join(f'{str(k)}: {v:+.3f}' for k, v in by_age.items()), len(smp),
         {'by_age': {str(k): float(v) for k, v in by_age.items()}})

    r['ano'] = pd.to_datetime(r.timestamp, unit='s').dt.year
    by_year = r.groupby('ano').rating.agg(['mean', 'size'])
    by_year = by_year[by_year['size'] > 10000]
    rho, p = spearmanr(by_year.index, by_year['mean'])
    disp = pd.read_csv(os.path.join(RAW, 'grouplens', 'rating-disposition-2023', 'ratings.csv'), usecols=['rating', 'tstamp'])
    disp_mean = float(disp.rating.mean())
    ml1920 = float(r[r.ano.isin([2019, 2020])].rating.mean())
    test('H17', 'Nota média por ano em que foi dada (MovieLens 32M)', float(rho), float(p),
         f'Spearman ano × média {rho:+.2f}; {int(by_year.index.min())}: {by_year["mean"].iloc[0]:.2f} → {int(by_year.index.max())}: {by_year["mean"].iloc[-1]:.2f}; '
         f'Rating Disposition 2019–2020: {disp_mean:.2f} contra {ml1920:.2f} no ML 32M nos mesmos anos', int(by_year['size'].sum()),
         {'by_year': {int(k): float(v) for k, v in by_year['mean'].items()}, 'disposition_mean': disp_mean, 'ml_2019_2020': ml1920})

    s = F[['franchise', 'ml', 'tier']].dropna()
    out = {}
    for t, g in s.groupby('tier'):
        a, b = g[g.franchise].ml, g[~g.franchise].ml
        if len(a) > 20:
            out[t] = {'franquia': float(a.mean()), 'original': float(b.mean()), 'n_franquia': int(len(a)), 'p': float(mannwhitneyu(a, b).pvalue)}
    a, b = s[s.franchise].ml, s[~s.franchise].ml
    U, p = mannwhitneyu(a, b)
    test('H18', 'Franquia × original', float(U), float(p),
         f'franquia {a.mean():+.3f} × original {b.mean():+.3f} (diferença {a.mean() - b.mean():+.3f}); por faixa: ' +
         ', '.join(f'{t} {v["franquia"] - v["original"]:+.3f}' for t, v in out.items()), len(s), {'by_tier': out})

    # ---- correção para comparações múltiplas (inclui hipóteses anteriores com p-valor)
    prev = []
    for fname, path in [('hypotheses.json', [('H3', ['H3', 'p'])]), ('hypotheses_awards.json', [('H9a', ['H9a', 0, 'p'])]),
                        ('hypotheses_platforms.json', [('H10a', ['H10a', 0, 'p'])])]:
        try:
            j = json.load(open(os.path.join(RAW, fname), encoding='utf-8'))
            for name, keys in path:
                v = j
                for k in keys:
                    v = v[k]
                prev.append((name, float(v)))
        except (OSError, KeyError, IndexError, TypeError):
            pass
    allp = pvals + prev
    reject, padj, *_ = multipletests([p for _, p in allp], alpha=0.05, method='fdr_bh')
    res['bh'] = [{'h': n, 'p': p, 'p_adj': float(q), 'sig': bool(rj)} for (n, p), q, rj in zip(allp, padj, reject)]
    print('\n== Correção de Benjamini-Hochberg (5%): ' + ', '.join(f'{x["h"]} {"✔" if x["sig"] else "✘"} (p ajustado {x["p_adj"]:.1e})' for x in res['bh']))
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=float)
    print(f'\n[eda] resultados em {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
