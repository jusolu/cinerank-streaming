/**
 * CineReco — recomendação no navegador para o modo Descobrir.
 *
 * Três estratégias, todas atualizadas a cada resposta (para comparação justa):
 *  - linucb:   bandit contextual LinUCB. Aprende um peso por característica (θ) e soma um
 *              bônus de incerteza que favorece o que você ainda não avaliou (exploração).
 *  - ema:      perfil de gosto por média móvel exponencial; respostas recentes pesam mais.
 *  - baseline: sem personalização (qualidade + popularidade).
 *
 * Recompensas: vi e gostei +1 · quero ver +0,8 · sem interesse −0,5 · vi e não gostei −1.
 * Funciona em navegador (window.CineReco) e em Node (module.exports), para testes.
 */
(function (global) {
  'use strict';

  const GENRES = ['Ação', 'Aventura', 'Animação', 'Biografia', 'Comédia', 'Crime', 'Documentário', 'Drama',
    'Família', 'Fantasia', 'Noir', 'Game show', 'História', 'Terror', 'Música', 'Musical', 'Mistério',
    'Jornalismo', 'Reality', 'Romance', 'Ficção científica', 'Esporte', 'Talk show', 'Suspense', 'Guerra', 'Faroeste'];
  const DECADES = [1990, 2000, 2010, 2020];
  const DECADE_NAMES = ['Anos 90', 'Anos 2000', 'Anos 2010', 'Anos 2020'];
  const NUMERIC_NAMES = ['Filmes muito bem avaliados', 'Filmes populares', 'Filmes longos'];

  const NAMES = ['Base', ...GENRES, ...DECADE_NAMES, ...NUMERIC_NAMES];
  const D = NAMES.length;
  const GENRE_OFFSET = 1;
  const DECADE_OFFSET = GENRE_OFFSET + GENRES.length;
  const NUMERIC_OFFSET = DECADE_OFFSET + DECADES.length;

  const REWARDS = { right: 1.0, up: 0.8, down: -0.5, left: -1.0 };

  // Exploração baixa: com 0,5 o bônus de incerteza dominava as primeiras respostas e a fila
  // pulava de gênero em gênero mesmo com rejeições seguidas.
  const ALPHA = 0.15;
  // Vizinhança: filmes parecidos com os que você avaliou (gênero, direção, elenco) sobem ou descem.
  const NEIGHBOR_WEIGHT = 1.2;
  const MEMORY_SIZE = 60;
  const LAMBDA = 0.85; // memória do EMA
  const EMA_SCALE = 2.5;
  // Conhecimento prévio somado em todos os modelos: filmes bem avaliados e conhecidos têm vantagem.
  // A qualidade pesa mais que a popularidade para não sugerir sucessos de bilheteria mal avaliados.
  const PRIOR_QUALITY = 0.35;
  const PRIOR_POPULARITY = 0.1;

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  /** Vetor de características de um filme da API (/api/movies). */
  function features(m) {
    const x = new Float64Array(D);
    x[0] = 1;
    for (const g of String(m.genres || '').split(', ')) {
      const i = GENRES.indexOf(g);
      if (i >= 0) x[GENRE_OFFSET + i] = 1;
    }
    const d = DECADES.indexOf(Math.floor((m.year || 0) / 10) * 10);
    if (d >= 0) x[DECADE_OFFSET + d] = 1;
    x[NUMERIC_OFFSET] = clamp(((m.cinescore ?? 60) - 65) / 10, -2, 2);
    x[NUMERIC_OFFSET + 1] = clamp((Math.log10(Math.max(1, m.imdb_votes || 1)) - 4.5), -2, 2);
    x[NUMERIC_OFFSET + 2] = clamp(((m.runtime || 110) - 115) / 30, -2, 2);
    return x;
  }

  const dot = (a, b) => { let s = 0; for (let i = 0; i < a.length; i++) s += a[i] * b[i]; return s; };
  const norm = (a) => Math.sqrt(dot(a, a));

  // Partida a frio: antes de qualquer resposta, filmes bons e conhecidos vêm primeiro.
  const prior = (x) => PRIOR_QUALITY * x[NUMERIC_OFFSET] + PRIOR_POPULARITY * x[NUMERIC_OFFSET + 1];

  // ---------------- LinUCB ----------------
  function newLinUCB() {
    const ainv = new Array(D * D).fill(0);
    for (let i = 0; i < D; i++) ainv[i * D + i] = 1; // A = I (regularização)
    return { ainv, b: new Array(D).fill(0), n: 0 };
  }

  function ainvTimes(s, x) {
    const out = new Float64Array(D);
    for (let i = 0; i < D; i++) {
      let v = 0;
      const row = i * D;
      for (let j = 0; j < D; j++) v += s.ainv[row + j] * x[j];
      out[i] = v;
    }
    return out;
  }

  const thetaOf = (s) => ainvTimes(s, s.b);

  function linucbScore(s, x, theta) {
    const mean = dot(theta, x);
    const bonus = ALPHA * Math.sqrt(Math.max(0, dot(x, ainvTimes(s, x))));
    return { mean, bonus, total: mean + bonus + prior(x) };
  }

  // Sherman–Morrison: atualiza A⁻¹ após A ← A + x xᵀ em O(d²), sem inverter matriz.
  function linucbUpdate(s, x, r) {
    const ax = ainvTimes(s, x);
    const denom = 1 + dot(x, ax);
    for (let i = 0; i < D; i++) {
      for (let j = 0; j < D; j++) s.ainv[i * D + j] -= (ax[i] * ax[j]) / denom;
    }
    for (let i = 0; i < D; i++) s.b[i] += r * x[i];
    s.n += 1;
  }

  // ---------------- EMA ----------------
  const newEMA = () => ({ u: new Array(D).fill(0), n: 0 });

  function emaScore(s, x) {
    const nu = norm(s.u);
    const sim = nu ? dot(s.u, x) / (nu * norm(x)) : 0;
    // A similaridade de cosseno vai de −1 a 1; ampliada para pesar como o θ·x do LinUCB.
    return { mean: sim, bonus: 0, total: EMA_SCALE * sim + prior(x) };
  }

  function emaUpdate(s, x, r) {
    for (let i = 0; i < D; i++) s.u[i] = LAMBDA * s.u[i] + (1 - LAMBDA) * r * x[i];
    s.n += 1;
  }

  // ---------------- Vizinhança (filme a filme) ----------------
  const splitList = (v) => String(v || '').split(', ').filter(Boolean);

  function profileOf(movie) {
    return {
      id: movie.imdb_id,
      title: movie.title,
      genres: splitList(movie.genres),
      directors: splitList(movie.directors),
      cast: Array.isArray(movie.cast) ? movie.cast.slice(0, 4) : splitList(movie.cast).slice(0, 4),
      year: movie.year || null,
      cinescore: movie.cinescore ?? null,
      votes: movie.imdb_votes || 0,
      runtime: movie.runtime || null,
      facets: movie.facets || {},
    };
  }

  // Grupo de atributo → categoria exibida na árvore (espelha ingest/facets.py).
  const FACET_CATEGORY = {
    'Elenco': 'Equipe', 'Roteiro': 'Equipe', 'Fotografia': 'Equipe', 'Trilha sonora': 'Equipe',
    'Montagem': 'Equipe', 'Direção de arte': 'Equipe',
    'País': 'Origem', 'Região': 'Origem', 'Idioma original': 'Origem', 'Estúdio': 'Origem', 'Orçamento': 'Origem',
    'Subgênero': 'Enredo', 'Tema': 'Enredo', 'Assunto': 'Enredo', 'Baseado em': 'Enredo',
    'Local da história': 'Enredo', 'Franquia': 'Enredo',
    'Prêmios': 'Reconhecimento', 'Crítica': 'Reconhecimento', 'Bilheteria': 'Reconhecimento',
    'Classificação': 'Público', 'Locais de filmagem': 'Origem', 'Cor': 'Estilo',
    'Percepção do público': 'Estilo', 'Produção': 'Equipe', 'Figurino': 'Equipe', 'Universo': 'Enredo', 'Paleta': 'Estilo', 'Pôster': 'Estilo',
  };
  const facetValues = (m, group) => (m.facets && m.facets[group]) || [];

  // ---------------- Árvore de decisão (CART, Gini) ----------------
  // Treinada a cada resposta com o que você avaliou: positivo = gostei / quero ver.
  const TREE_MAX_DEPTH = 4;
  const TREE_MIN_SAMPLES = 4;      // nó com menos respostas que isso vira folha
  const TREE_MIN_GAIN = 0.02;      // com centenas de perguntas possíveis, exige ganho real para dividir
  const FACET_MIN_SUPPORT = 2;     // um atributo só vira pergunta se aparece em 2+ filmes avaliados

  const decadeOf = (year) => (year ? Math.floor(year / 10) * 10 : null);
  const decadeName = (d) => DECADE_NAMES[DECADES.indexOf(d)] || `Anos ${d}`;

  /** Perguntas candidatas, geradas a partir do que aparece nas respostas. */
  function candidateTests(samples) {
    const tests = [];
    const count = (key) => {
      const c = new Map();
      for (const s of samples) for (const v of s[key]) c.set(v, (c.get(v) || 0) + 1);
      return c;
    };
    for (const [g] of count('genres')) tests.push({ id: `g:${g}`, label: g, category: 'Gênero', test: (m) => m.genres.includes(g) });
    for (const [d, n] of count('directors')) {
      if (n >= FACET_MIN_SUPPORT) tests.push({ id: `d:${d}`, label: `Direção: ${d}`, category: 'Equipe', test: (m) => m.directors.includes(d) });
    }
    for (const d of new Set(samples.map((s) => decadeOf(s.year)).filter(Boolean))) {
      tests.push({ id: `y:${d}`, label: decadeName(d), category: 'Época', test: (m) => decadeOf(m.year) === d });
    }
    tests.push({ id: 'q:80', label: 'CineScore 80+', category: 'Perfil', test: (m) => (m.cinescore ?? 0) >= 80 });
    tests.push({ id: 'q:70', label: 'CineScore 70+', category: 'Perfil', test: (m) => (m.cinescore ?? 0) >= 70 });
    tests.push({ id: 'v:500k', label: 'Muito popular (500 mil+ votos)', category: 'Perfil', test: (m) => m.votes >= 500000 });
    tests.push({ id: 'r:140', label: 'Longo (2h20+)', category: 'Perfil', test: (m) => (m.runtime || 0) >= 140 });

    // Atributos ricos: equipe técnica, origem, enredo, prêmios. Só valores que se repetem nas respostas.
    const facetCount = new Map();
    for (const s of samples) {
      for (const group of Object.keys(FACET_CATEGORY)) {
        for (const v of facetValues(s, group)) {
          const key = `${group}\u0000${v}`;
          facetCount.set(key, (facetCount.get(key) || 0) + 1);
        }
      }
    }
    for (const [key, n] of facetCount) {
      if (n < FACET_MIN_SUPPORT) continue;
      const [group, value] = key.split('\u0000');
      tests.push({
        id: `f:${key}`,
        label: `${group}: ${value}`,
        category: FACET_CATEGORY[group],
        test: (m) => facetValues(m, group).includes(value),
      });
    }
    return tests;
  }

  const gini = (pos, n) => (n ? 1 - (pos / n) ** 2 - ((n - pos) / n) ** 2 : 0);
  const isPositive = (s) => s.r > 0;

  function grow(samples, depth, used) {
    const pos = samples.filter(isPositive).length;
    const node = { n: samples.length, pos, samples: samples.map((s) => ({ id: s.id, title: s.title, liked: isPositive(s) })) };
    if (depth >= TREE_MAX_DEPTH || samples.length < TREE_MIN_SAMPLES || pos === 0 || pos === samples.length) return node;

    const parent = gini(pos, samples.length);
    let best = null;
    for (const t of candidateTests(samples)) {
      if (used.has(t.id)) continue;
      const yes = samples.filter(t.test);
      const no = samples.filter((s) => !t.test(s));
      if (!yes.length || !no.length) continue;
      const yp = yes.filter(isPositive).length, np = no.filter(isPositive).length;
      const gain = parent - (yes.length / samples.length) * gini(yp, yes.length) - (no.length / samples.length) * gini(np, no.length);
      // Desempate: prefere perguntas com divisão mais equilibrada.
      const balance = Math.min(yes.length, no.length) / samples.length;
      if (gain > TREE_MIN_GAIN && (!best || gain + balance * 1e-3 > best.score)) best = { t, yes, no, score: gain + balance * 1e-3 };
    }
    if (!best) return node;
    const nextUsed = new Set(used).add(best.t.id);
    return {
      ...node,
      question: best.t.label,
      category: best.t.category,
      testId: best.t.id,
      test: best.t.test,
      yes: grow(best.yes, depth + 1, nextUsed),
      no: grow(best.no, depth + 1, nextUsed),
    };
  }

  /** Árvore atual, ou null se ainda não há respostas suficientes (precisa de gostei e não gostei). */
  function tree(state) {
    const samples = state.memory.filter((m) => m.genres);
    const pos = samples.filter(isPositive).length;
    if (samples.length < 3 || pos === 0 || pos === samples.length) return null;
    return grow(samples, 0, new Set());
  }

  /** Caminho de um filme na árvore: [{ node, answer: 'yes'|'no'|null }], terminando na folha. */
  function treePath(root, movie) {
    const p = profileOf(movie);
    const path = [];
    let node = root;
    while (node) {
      if (!node.test) { path.push({ node, answer: null }); break; }
      const answer = node.test(p) ? 'yes' : 'no';
      path.push({ node, answer });
      node = node[answer];
    }
    return path;
  }

  // Probabilidade de gostar na folha, com suavização de Laplace (evita 0% e 100% com poucas respostas).
  const leafProb = (leaf) => (leaf.pos + 1) / (leaf.n + 2);

  /** Semelhança 0-1: gêneros (Jaccard) 50%, mesma direção 30%, elenco em comum 20%. */
  const jaccard = (x, y) => {
    if (!x.length && !y.length) return 0;
    const sx = new Set(x), sy = new Set(y);
    let inter = 0;
    for (const v of sx) if (sy.has(v)) inter += 1;
    return inter / new Set([...sx, ...sy]).size;
  };
  const shares = (x, y) => x.some((v) => y.includes(v));

  /** Semelhança 0-1: gênero 25%, direção 20%, temas/subgênero 15%, elenco 15%, universo 10%, equipe técnica 10%, origem 5%. */
  function similarity(a, b) {
    const genre = jaccard(a.genres, b.genres);
    const director = shares(a.directors, b.directors) ? 1 : 0;
    const sharedCast = a.cast.filter((c) => b.cast.includes(c)).length;
    const themes = (m) => [...facetValues(m, 'Tema'), ...facetValues(m, 'Subgênero'), ...facetValues(m, 'Assunto')];
    const theme = Math.min(1, jaccard(themes(a), themes(b)) * 2.5);
    const crew = ['Fotografia', 'Trilha sonora', 'Roteiro'].some((g) => shares(facetValues(a, g), facetValues(b, g))) ? 1 : 0;
    const origin = shares(facetValues(a, 'Idioma original'), facetValues(b, 'Idioma original'))
      && !facetValues(a, 'Idioma original').includes('Inglês') ? 1 : 0;
    const tone = shares(facetValues(a, 'Universo'), facetValues(b, 'Universo')) ? 1 : 0;
    return 0.25 * genre + 0.2 * director + 0.15 * theme + 0.15 * Math.min(1, sharedCast / 2)
      + 0.1 * tone + 0.1 * crew + 0.05 * origin;
  }

  /** Puxa para cima filmes parecidos com os que você curtiu e para baixo os parecidos com os rejeitados. */
  function neighborScore(state, movie) {
    if (!state.memory.length) return { value: 0, because: null };
    const p = profileOf(movie);
    let bestPos = 0, bestNeg = 0, because = null;
    for (const mem of state.memory) {
      const sim = similarity(p, mem);
      if (mem.r > 0 && sim * mem.r > bestPos) { bestPos = sim * mem.r; because = mem; }
      if (mem.r < 0 && sim * -mem.r > bestNeg) bestNeg = sim * -mem.r;
    }
    return { value: NEIGHBOR_WEIGHT * (bestPos - 0.8 * bestNeg), because: bestPos >= 0.45 ? because : null };
  }

  // ---------------- Interface pública ----------------
  function createState() {
    return { version: 2, linucb: newLinUCB(), ema: newEMA(), memory: [] };
  }

  function isValidState(s) {
    return s && s.version === 2 && s.linucb && s.linucb.ainv && s.linucb.ainv.length === D * D
      && s.ema && s.ema.u && s.ema.u.length === D && Array.isArray(s.memory);
  }

  /** Registra uma resposta em todos os modelos. */
  function update(state, movie, action) {
    const r = REWARDS[action];
    if (r === undefined) return;
    const x = features(movie);
    linucbUpdate(state.linucb, x, r);
    emaUpdate(state.ema, x, r);
    state.memory.unshift({ ...profileOf(movie), r });
    state.memory.length = Math.min(state.memory.length, MEMORY_SIZE);
  }

  /** Ordena `movies` pelo algoritmo escolhido. Retorna [{ movie, total, mean, bonus, neighbor, because, x }]. */
  function rank(state, algo, movies) {
    const theta = algo === 'linucb' ? thetaOf(state.linucb) : null;
    const root = algo === 'tree' ? tree(state) : null;
    return movies.map((movie) => {
      const x = features(movie);
      let s;
      if (algo === 'linucb') s = linucbScore(state.linucb, x, theta);
      else if (algo === 'ema') s = emaScore(state.ema, x);
      else if (algo === 'tree' && root) {
        const path = treePath(root, movie);
        const prob = leafProb(path[path.length - 1].node);
        s = { mean: prob, bonus: 0, total: 2 * (prob - 0.5) + prior(x), path };
      } else s = { mean: 0, bonus: 0, total: prior(x) };
      let neighbor = 0, because = null;
      if (algo !== 'baseline') {
        ({ value: neighbor, because } = neighborScore(state, movie));
        s.total += neighbor;
      }
      return { movie, x, neighbor, because, ...s };
    }).sort((a, b) => b.total - a.total);
  }

  /** Pesos aprendidos por característica (θ no LinUCB, u no EMA). */
  function weights(state, algo) {
    if (algo === 'linucb' || algo === 'tree' || algo === 'hybrid') return thetaOf(state.linucb);
    if (algo === 'ema') return Float64Array.from(state.ema.u);
    return new Float64Array(D);
  }

  const answers = (state) => state.linucb.n;

  /** Frase curta explicando por que o filme apareceu agora. */
  function explain(state, algo, item) {
    if (algo === 'baseline') return 'Entre os filmes mais conhecidos e bem avaliados.';
    if (answers(state) === 0) return 'Começando pelos mais conhecidos. As próximas sugestões se ajustam às suas respostas.';
    const w = weights(state, algo);
    const contrib = [];
    for (let i = 1; i < D; i++) {
      const c = w[i] * item.x[i];
      if (c > 0.02) contrib.push([NAMES[i], c]);
    }
    contrib.sort((a, b) => b[1] - a[1]);
    if (algo === 'tree') {
      if (!item.path) return 'Avalie filmes que você gostou e que não gostou para a árvore começar a decidir.';
      const rules = item.path.filter((p) => p.answer).map((p) => `${p.node.question}: ${p.answer === 'yes' ? 'sim' : 'não'}`);
      const leaf = item.path[item.path.length - 1].node;
      return `${rules.length ? rules.join(' → ') + '. ' : ''}Nesse ramo você gostou de ${leaf.pos} de ${leaf.n}.`;
    }
    if (item.because) return `Porque você gostou de ${item.because.title}.`;
    if (algo === 'linucb' && item.bonus > Math.abs(item.mean) + Math.max(0, item.neighbor) && item.bonus > 0.25) {
      const genres = String(item.movie.genres || '').split(', ').filter(Boolean).slice(0, 2);
      return `Explorando: você ainda avaliou poucos filmes assim${genres.length ? ` (${genres.join(', ')})` : ''}.`;
    }
    if (contrib.length) return `Combina com o que você curtiu: ${contrib.slice(0, 2).map((c) => c[0]).join(', ')}.`;
    return 'Sugestão para variar a fila.';
  }

  /** Resumo do gosto aprendido: características com maior e menor peso. */
  function profile(state, algo, limit = 3) {
    if (algo === 'baseline' || answers(state) === 0) return { likes: [], dislikes: [] };
    const w = weights(state, algo);
    const items = [];
    for (let i = 1; i < D; i++) items.push([NAMES[i], w[i]]);
    const scale = Math.max(...items.map((it) => Math.abs(it[1]))) || 1;
    const likes = items.filter((it) => it[1] / scale > 0.25).sort((a, b) => b[1] - a[1]).slice(0, limit).map((it) => it[0]);
    const dislikes = items.filter((it) => it[1] / scale < -0.25).sort((a, b) => a[1] - b[1]).slice(0, limit).map((it) => it[0]);
    return { likes, dislikes };
  }

  const api = { createState, isValidState, update, rank, explain, profile, features, tree, treePath, leafProb, NAMES, REWARDS };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.CineReco = api;
})(typeof window !== 'undefined' ? window : globalThis);
