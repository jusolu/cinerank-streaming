/**
 * CineRank — controlador do frontend.
 * Ranking paginado, ficha do filme, grafo de conexões (canvas) e triagem "Descobrir".
 * Todo conteúdo vindo da API é escapado antes de entrar no DOM.
 */
(() => {
  'use strict';

  // ==========================================================
  // Utilitários
  // ==========================================================
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ESC_MAP[c]);

  const fmtScore = (v) => (v == null ? '—' : Number(v).toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 }));
  const fmtInt = (v) => (v == null ? '—' : Number(v).toLocaleString('pt-BR'));
  const fmtCompact = (v) => (v == null ? '—' : Number(v).toLocaleString('pt-BR', { notation: 'compact', maximumFractionDigits: 1 }));
  const MONTHS = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro'];
  const fmtDate = (iso) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
    return m ? `${Number(m[3])} de ${MONTHS[Number(m[2]) - 1]} de ${m[1]}` : null;
  };
  const fmtRuntime = (min) => {
    if (!min) return null;
    const h = Math.floor(min / 60), m = min % 60;
    return h ? `${h}h${m ? String(m).padStart(2, '0') : ''}` : `${m} min`;
  };
  const plural = (n, one, many) => `${fmtInt(n)} ${n === 1 ? one : many}`;
  const LOW_VOTES = 1000;

  const storage = {
    get(key, fallback) {
      try {
        const raw = localStorage.getItem(key);
        return raw ? JSON.parse(raw) : fallback;
      } catch { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* armazenamento indisponível */ }
    }
  };

  async function api(path) {
    const res = await fetch(path);
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  function toast(msg) {
    const el = document.createElement('div');
    el.className = 'toast';
    el.textContent = msg;
    $('#toasts').appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 200); }, 2600);
  }

  // Capa do filme, ou placa neutra com a inicial quando não há imagem.
  const posterHTML = (m, cls) => (m.poster_url
    ? `<img class="poster ${cls}" src="${esc(m.poster_url)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
    : `<span class="poster poster-empty ${cls}" aria-hidden="true">${esc((m.title || '?').trim().charAt(0))}</span>`);

  // Imagem quebrada vira a placa neutra.
  document.addEventListener('error', (e) => {
    const img = e.target;
    if (img.tagName !== 'IMG' || !img.classList.contains('poster')) return;
    const span = document.createElement('span');
    span.className = `${img.className} poster-empty`;
    span.setAttribute('aria-hidden', 'true');
    img.replaceWith(span);
  }, true);

  const sourceLabel = (m) => (m.media_source === 'tmdb' ? 'TMDB' : m.media_source === 'wikipedia' ? 'Wikipédia' : null);

  // ==========================================================
  // Tradução automática das sinopses em inglês
  // Usa o tradutor embutido do Chrome (Translator API, roda no próprio computador, sem chave).
  // Sem suporte no navegador, a sinopse fica em inglês com a etiqueta.
  // ==========================================================
  const Translate = (() => {
    const OPTS = { sourceLanguage: 'en', targetLanguage: 'pt' };
    const KEY = 'cinerank.translations';
    const MAX_CACHED = 500;
    const supported = typeof self.Translator !== 'undefined';
    let cache = storage.get(KEY, {});
    let translator = null;
    let creating = null;
    let unavailable = false;

    const cached = (id) => cache[id] || null;

    function remember(id, text) {
      cache[id] = text;
      const keys = Object.keys(cache);
      if (keys.length > MAX_CACHED) keys.slice(0, keys.length - MAX_CACHED).forEach((k) => delete cache[k]);
      storage.set(KEY, cache);
    }

    function ensure() {
      if (!supported || unavailable) return Promise.resolve(null);
      if (translator) return Promise.resolve(translator);
      creating ??= (async () => {
        const status = await self.Translator.availability(OPTS);
        if (status === 'unavailable') { unavailable = true; return null; }
        translator = await self.Translator.create(OPTS);
        return translator;
      })().catch(() => { creating = null; return null; });  // tenta de novo na próxima interação
      return creating;
    }

    async function apply(root = document) {
      const pending = $$('[data-translate]', root);
      if (!pending.length) return;
      const t = await ensure();
      if (!t) return;
      for (const el of pending) {
        const id = el.dataset.translate;
        const span = $('.tr-text', el);
        if (!id || !span || !el.isConnected) continue;
        try {
          const out = await t.translate(span.textContent);
          remember(id, out);
          span.textContent = out;
          el.lang = 'pt';
          el.removeAttribute('data-translate');
          const tag = $('.lang-tag', el);
          if (tag) tag.textContent = 'tradução automática';
        } catch { /* mantém em inglês */ }
      }
    }

    // Baixar o modelo pela primeira vez exige uma interação do usuário; tenta a cada clique ou tecla.
    if (supported) {
      const onInteract = () => { if (!translator && !creating && !unavailable) ensure().then(() => apply()); };
      document.addEventListener('pointerdown', onInteract, { passive: true });
      document.addEventListener('keydown', onInteract);
    }

    return { cached, apply };
  })();

  /** Parágrafo de sinopse; texto em inglês fica marcado para tradução automática. */
  function synopsisHTML(m, cls) {
    if (!m.synopsis) return '';
    if (m.synopsis_lang !== 'en') return `<p class="${cls}">${esc(m.synopsis)}</p>`;
    const done = Translate.cached(m.imdb_id);
    if (done) return `<p class="${cls}" lang="pt">${esc(done)} <span class="lang-tag">tradução automática</span></p>`;
    return `<p class="${cls}" lang="en" data-translate="${esc(m.imdb_id)}"><span class="tr-text">${esc(m.synopsis)}</span> <span class="lang-tag">em inglês</span></p>`;
  }

  // Linha secundária padrão de um filme: ano · direção · gêneros.
  const movieSub = (m) => [m.year, m.directors, m.genres].filter(Boolean).map(esc).join(' · ');

  // ==========================================================
  // Navegação entre seções (com hash para permitir links diretos)
  // ==========================================================
  const VIEWS = { catalog: '#ranking', graph: '#conexoes', discover: '#descobrir', foryou: '#paravoce', scenarios: '#cenarios' };
  let currentView = null;

  function showView(view, { updateHash = true } = {}) {
    if (!VIEWS[view] || view === currentView) return;
    currentView = view;
    $$('.view').forEach((s) => { s.hidden = s.id !== `view-${view}`; });
    $$('.nav-link[data-view]').forEach((b) => {
      const active = b.dataset.view === view;
      b.classList.toggle('is-active', active);
      if (active) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    });
    if (updateHash) history.replaceState(null, '', view === 'catalog' ? location.pathname : VIEWS[view]);
    window.scrollTo(0, 0);

    if (view === 'graph') Graph.activate(); else Graph.deactivate();
    if (view === 'discover') Discover.activate();
    if (view === 'foryou') ForYou.activate();
    if (view === 'scenarios') Scenarios.activate();
  }

  $$('.nav-link[data-view]').forEach((b) => b.addEventListener('click', () => showView(b.dataset.view)));

  // ==========================================================
  // Diálogos
  // ==========================================================
  function openDialog(id) {
    const d = document.getElementById(id);
    if (d && !d.open) d.showModal();
  }

  document.addEventListener('click', (e) => {
    const opener = e.target.closest('[data-open]');
    if (opener) { openDialog(opener.dataset.open); return; }
    if (e.target.closest('[data-close]')) e.target.closest('dialog').close();
  });

  $$('dialog').forEach((d) => {
    d.addEventListener('click', (e) => { if (e.target === d) d.close(); });
  });

  const anyDialogOpen = () => $$('dialog').some((d) => d.open);

  // ==========================================================
  // Filtros compartilhados (anos e gêneros)
  // ==========================================================
  async function loadFacets() {
    try {
      const { years, genres } = await api('/api/facets');
      const yearOpts = years.map((y) => `<option value="${esc(y.year)}">${esc(y.year)}</option>`).join('');
      const decades = [...new Set(years.map((y) => Math.floor(y.year / 10) * 10))].sort((a, b) => b - a);
      $('#decade-group').innerHTML = decades
        .map((d) => `<option value="d${d}">Anos ${d < 2000 ? String(d).slice(2) : d}</option>`).join('');
      const genreOpts = genres.map((g) => `<option value="${esc(g.genre)}">${esc(g.label)}</option>`).join('');
      // Chips ordenados por volume: os gêneros mais comuns ficam ao alcance sem rolar.
      const chips = [...genres].sort((a, b) => b.count - a.count)
        .map((g) => `<button class="chip" data-genre="${esc(g.genre)}" aria-pressed="false">${esc(g.label)}<small>${fmtCompact(g.count)}</small></button>`).join('');
      $('#year-group').innerHTML = yearOpts;
      $('#genre-chips').insertAdjacentHTML('beforeend', chips);
      $('#discover-genre').insertAdjacentHTML('beforeend', genreOpts);
      $('#foryou-genre').insertAdjacentHTML('beforeend', genreOpts);
    } catch { /* mantém apenas "Todos" */ }
  }

  // ==========================================================
  // Ranking
  // ==========================================================
  const Ranking = (() => {
    const PAGE_SIZE = 50;
    const state = { year: '', genre: '', sort: 'cinescore', search: '', confident: false, page: 1 };
    let requestId = 0;
    let shown = 0;

    const rowsEl = $('#ranking-rows');
    const tableEl = $('#ranking-table');
    const emptyEl = $('#empty-state');
    const countEl = $('#results-count');
    const moreBtn = $('#btn-more');

    function bind() {
      const reload = () => { state.page = 1; load(); };
      $('#year-select').addEventListener('change', (e) => { state.year = e.target.value; reload(); });
      $('#genre-chips').addEventListener('click', (e) => {
        const chip = e.target.closest('.chip');
        if (!chip) return;
        state.genre = chip.dataset.genre;
        syncChips();
        reload();
      });
      $('#sort-select').addEventListener('change', (e) => { state.sort = e.target.value; reload(); });
      $('#confident-check').addEventListener('change', (e) => { state.confident = e.target.checked; reload(); });

      let timer;
      $('#search-input').addEventListener('input', (e) => {
        clearTimeout(timer);
        timer = setTimeout(() => { state.search = e.target.value.trim(); reload(); }, 250);
      });

      const resetFilters = () => {
        Object.assign(state, { year: '', genre: '', sort: 'cinescore', search: '', confident: false, page: 1 });
        $('#year-select').value = '';
        $('#sort-select').value = 'cinescore';
        $('#confident-check').checked = false;
        $('#search-input').value = '';
        syncChips();
        load();
      };
      $('#btn-reset-filters').addEventListener('click', resetFilters);
      $('#btn-clear-filters').addEventListener('click', resetFilters);

      moreBtn.addEventListener('click', () => { state.page += 1; load({ append: true }); });

      const openFromEvent = (e) => {
        const item = e.target.closest('[data-movie]');
        if (item) MovieDialog.open(item.dataset.movie);
      };
      rowsEl.addEventListener('click', openFromEvent);
      rowsEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openFromEvent(e); }
      });
    }

    function syncChips() {
      $$('#genre-chips .chip').forEach((c) => c.setAttribute('aria-pressed', String(c.dataset.genre === state.genre)));
    }

    const hasFilters = () => Boolean(state.year || state.genre || state.search || state.confident || state.sort !== 'cinescore');

    // Linhas-esqueleto: mostram a forma do resultado enquanto ele carrega.
    function skeleton(n = 8) {
      rowsEl.innerHTML = Array.from({ length: n }, () => `
        <div class="rt-row skeleton" aria-hidden="true">
          <span class="c-rank"><span class="sk" style="width:20px;margin-left:auto"></span></span>
          <span class="c-title"><span class="poster rt-thumb poster-empty"></span><span class="rt-text"><span class="sk sk-title"></span><span class="sk sk-sub"></span></span></span>
          <span class="c-num c-runtime"><span class="sk"></span></span>
          <span class="c-num"><span class="sk"></span></span>
          <span class="c-num c-votes"><span class="sk"></span></span>
          <span class="c-score"><span class="sk"></span></span>
        </div>`).join('');
    }

    async function load({ append = false } = {}) {
      const id = ++requestId;
      if (!append) {
        countEl.textContent = 'Carregando…';
        tableEl.hidden = false;
        emptyEl.hidden = true;
        skeleton();
      }
      moreBtn.disabled = true;
      const params = new URLSearchParams({ sort: state.sort, page: state.page, page_size: PAGE_SIZE });
      if (state.year.startsWith('d')) params.set('decade', state.year.slice(1));
      else if (state.year) params.set('year', state.year);
      if (state.genre) params.set('genre', state.genre);
      if (state.search) params.set('search', state.search);
      if (state.confident) params.set('min_votes', LOW_VOTES);
      try {
        const data = await api(`/api/movies?${params}`);
        if (id === requestId) render(data, append);
      } catch {
        if (id !== requestId) return;
        rowsEl.innerHTML = '<div class="loading-row">Não foi possível carregar o ranking. Tente novamente em instantes.</div>';
        countEl.textContent = '';
      } finally {
        moreBtn.disabled = false;
      }
    }

    function rowHTML(m, position) {
      const low = m.imdb_votes < LOW_VOTES;
      const original = m.original_title ? `<span class="rt-original">${esc(m.original_title)}</span>` : '';
      return `
        <div class="rt-row" role="row" tabindex="0" data-movie="${esc(m.imdb_id)}" aria-label="${esc(m.title)}, CineScore ${fmtScore(m.cinescore)}">
          <span role="cell" class="c-rank">${position}</span>
          <span role="cell" class="c-title">
            ${posterHTML(m, 'rt-thumb')}
            <span class="rt-text">
              <span class="rt-title">${esc(m.title)}${original}</span>
              <span class="rt-sub">${movieSub(m)}</span>
            </span>
          </span>
          <span role="cell" class="c-num c-runtime">${esc(fmtRuntime(m.runtime) || '—')}</span>
          <span role="cell" class="c-num">${fmtScore(m.imdb_rating)}</span>
          <span role="cell" class="c-num c-votes${low ? ' flag-low' : ''}" title="${fmtInt(m.imdb_votes)} votos">${fmtCompact(m.imdb_votes)}</span>
          <span role="cell" class="c-score">
            <span class="score-bar${low ? ' is-low' : ''}" aria-hidden="true"><span style="width:${Math.max(0, Math.min(100, m.cinescore || 0))}%"></span></span>
            <span class="score">${fmtScore(m.cinescore)}</span>
            <span class="score-sub">${hasFilters() ? `${fmtInt(m.rank)}º geral` : ''}</span>
          </span>
        </div>`;
    }

    function render(data, append) {
      const { movies, total, page, page_size: size } = data;
      const offset = (page - 1) * size;
      const html = movies.map((m, i) => rowHTML(m, offset + i + 1)).join('');
      if (append) rowsEl.insertAdjacentHTML('beforeend', html); else rowsEl.innerHTML = html;
      shown = offset + movies.length;

      countEl.innerHTML = total ? `<strong>${fmtInt(total)}</strong> ${total === 1 ? 'filme' : 'filmes'}` : '';
      $('#btn-clear-filters').hidden = !hasFilters();
      emptyEl.hidden = total > 0;
      tableEl.hidden = total === 0;
      moreBtn.hidden = shown >= total;
      moreBtn.textContent = `Mostrar mais (${fmtInt(shown)} de ${fmtInt(total)})`;
    }

    async function loadStats() {
      try {
        const { stats: s } = await api('/api/stats');
        const parts = [
          `<span><strong>${fmtInt(s.total_movies)}</strong> filmes de ${esc(s.min_year)} a ${esc(s.max_year)}</span>`,
          `<span><strong>${fmtInt(s.confident_movies)}</strong> com 1.000+ votos</span>`
        ];
        const updated = fmtDate(s.updated_at);
        if (updated) parts.push(`<span>Dados do IMDb de ${esc(updated)}</span>`);
        $('#stat-line').innerHTML = parts.join('');

        $$('[data-since]').forEach((el) => { el.textContent = s.min_year; });
        const meta = s.methodology || {};
        $$('[data-meta]').forEach((el) => {
          const v = meta[el.dataset.meta];
          if (v != null) el.textContent = el.dataset.meta === 'prior_mean' ? fmtScore(v) : fmtInt(v);
        });
      } catch { /* a linha de estatísticas é opcional */ }
    }

    function init() {
      bind();
      loadStats();
      load();
    }

    return { init };
  })();

  // ==========================================================
  // Ficha do filme
  // ==========================================================
  const MovieDialog = (() => {
    const dialog = $('#movie-dialog');
    const body = $('#movie-dialog-body');

    async function open(id) {
      let m;
      try {
        ({ movie: m } = await api(`/api/movies/${encodeURIComponent(id)}`));
      } catch {
        toast('Não foi possível abrir a ficha deste filme.');
        return;
      }
      const meta = [m.year, fmtRuntime(m.runtime), m.genres].filter(Boolean);
      let note = `<strong>CineScore</strong><br>Nota IMDb ${fmtScore(m.imdb_rating)} · ${plural(m.imdb_votes, 'voto', 'votos')}`;
      if (m.imdb_votes < LOW_VOTES) note += ' · <span class="flag-low">poucos votos, nota instável</span>';

      const credits = [
        ['Direção', (m.directors_list || []).join(', ')],
        ['Roteiro', (m.writers || []).join(', ')],
        ['Elenco', (m.cast || []).join(', ')],
      ].filter(([, v]) => v);

      const fx = m.facets || {};
      for (const group of ['Fotografia', 'Trilha sonora', 'Montagem', 'Direção de arte', 'Produção', 'Figurino']) {
        if ((fx[group] || []).length) credits.push([group, fx[group].join(', ')]);
      }
      // Atributos agrupados por categoria (origem, enredo, reconhecimento, estilo).
      const ATTR_SECTIONS = [
        ['Origem', ['Idioma original', 'País', 'Estúdio', 'Orçamento', 'Locais de filmagem']],
        ['Percepção', ['Percepção do público']],
        ['Enredo', ['Universo', 'Subgênero', 'Baseado em', 'Assunto', 'Local da história', 'Franquia', 'Tema']],
        ['Reconhecimento', ['Prêmios', 'Crítica', 'Bilheteria']],
        ['Público', ['Classificação']],
        ['Estilo', ['Paleta', 'Pôster', 'Cor']],
      ];
      const attrs = ATTR_SECTIONS.map(([title, groups]) => {
        const rows = groups.filter((g) => (fx[g] || []).length).map((g) => `
          <div class="attr-row"><span class="attr-name">${esc(g)}${g === 'Tema' ? ' <span class="lang-tag">em inglês</span>' : ''}</span>
            <span class="tags">${fx[g].map((v) => `<span class="tag">${esc(v)}</span>`).join('')}</span></div>`).join('');
        return rows ? `<div class="attr-block"><h4>${title}</h4>${rows}</div>` : '';
      }).join('');

      const trailerQuery = encodeURIComponent(`${m.original_title || m.title} ${m.year || ''} trailer`);

      const source = sourceLabel(m);
      const synopsis = m.synopsis ? `
        <section class="md-section"><h3>Sinopse</h3>
          ${synopsisHTML(m, 'md-synopsis')}
          ${source && m.source_url ? `<p class="md-source">Fonte: <a href="${esc(m.source_url)}" target="_blank" rel="noopener">${source}</a></p>` : ''}
        </section>` : '';

      body.innerHTML = `
        <div class="md-top">
          ${posterHTML(m, 'md-poster')}
          <div class="md-heading">
            <p class="eyebrow">${fmtInt(m.rank)}º no ranking geral</p>
            <h2 class="dialog-title" id="md-title">${esc(m.title)}</h2>
            ${m.original_title ? `<p class="md-original">${esc(m.original_title)}</p>` : ''}
            <p class="md-meta">${meta.map(esc).join(' · ')}</p>
            <div class="md-score">
              <span class="score">${fmtScore(m.cinescore)}</span>
              <span class="md-score-note">${note}</span>
            </div>
          </div>
        </div>
        ${synopsis}

        ${credits.length ? `
          <section class="md-section"><h3>Ficha técnica</h3>
            <dl class="credits">${credits.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join('')}</dl>
          </section>` : ''}

        ${m.trailer_id ? `<section class="md-section"><h3>Trailer</h3>
          <div class="video"><iframe src="https://www.youtube-nocookie.com/embed/${encodeURIComponent(m.trailer_id)}?rel=0"
            title="Trailer de ${esc(m.title)}" loading="lazy" allow="encrypted-media; picture-in-picture" allowfullscreen></iframe></div>
        </section>` : ''}

        ${attrs ? `<section class="md-section"><h3>Atributos</h3>${attrs}</section>` : ''}

        <div class="md-links">
          <button class="btn" id="md-graph">Ver conexões</button>
          <a class="btn btn-quiet" href="https://www.imdb.com/title/${encodeURIComponent(m.imdb_id)}/" target="_blank" rel="noopener">IMDb <svg aria-hidden="true"><use href="#i-external"/></svg></a>
          <a class="btn btn-quiet" href="https://www.youtube.com/results?search_query=${trailerQuery}" target="_blank" rel="noopener">Buscar trailer <svg aria-hidden="true"><use href="#i-external"/></svg></a>
        </div>`;

      $('#md-graph', body).addEventListener('click', () => {
        dialog.close();
        Graph.focus(m.imdb_id);
        showView('graph');
      });

      if (!dialog.open) dialog.showModal();
      Translate.apply(body);
      dialog.scrollTop = 0;
    }

    return { open };
  })();

  // ==========================================================
  // Grafo de conexões (canvas com layout por forças)
  // ==========================================================
  const Graph = (() => {
    const canvas = $('#graph-canvas');
    const stage = $('#graph-stage');
    const loader = $('#graph-loader');
    const ctx = canvas.getContext('2d');

    let nodes = [];
    let links = [];
    let centerId = null;
    let pendingId = null;
    let active = false;
    let animId = null;
    let frames = 0;
    let needsFit = false;
    let camera = { x: 0, y: 0, scale: 1 };
    let hovered = null;
    let selected = null;
    let drag = null; // { node } ou { pan: {x, y} }
    let colors = {};
    let size = { w: 0, h: 0 };

    function readColors() {
      // color-mix() não é aceito pelo canvas: resolve cada token para rgb via um elemento de teste.
      const probe = document.createElement('span');
      document.body.appendChild(probe);
      const resolve = (name) => { probe.style.color = `var(${name})`; return getComputedStyle(probe).color; };
      colors = {
        ink: resolve('--fg'), ink2: resolve('--fg-2'), muted: resolve('--muted'), ruleStrong: resolve('--line-strong'),
        paper: resolve('--bg'), primary: resolve('--primary'), onPrimary: resolve('--on-primary')
      };
      probe.remove();
    }

    function resize() {
      const rect = stage.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      size = { w: rect.width, h: rect.height };
      canvas.width = Math.round(rect.width * dpr);
      canvas.height = Math.round(rect.height * dpr);
      kick();
    }
    new ResizeObserver(() => { if (active) resize(); }).observe(stage);

    async function fetchGraph(url) {
      loader.hidden = false;
      try {
        const { graph } = await api(url);
        setData(graph);
      } catch {
        toast('Não foi possível carregar as conexões.');
      } finally {
        loader.hidden = true;
      }
    }

    const params = () => `min_sim=${$('#graph-min-sim').value}&limit=${$('#graph-max-nodes').value}`;
    const load = (id) => fetchGraph(`/api/movies/${encodeURIComponent(id)}/graph?${params()}`);
    const loadDefault = () => fetchGraph(`/api/graph/default?${params()}`);

    function setData(graph) {
      const raw = graph.nodes || [];
      const center = raw.find((n) => n.is_center) || raw[0];
      if (!center) return;
      centerId = center.id;

      const count = raw.length;
      nodes = raw.map((n, i) => {
        let x = 0, y = 0;
        if (!n.is_center) {
          const angle = ((i - 1) / Math.max(1, count - 1)) * Math.PI * 2;
          const dist = 170 + (100 - (n.similarity || 50)) * 1.4;
          x = Math.cos(angle) * dist;
          y = Math.sin(angle) * dist;
        }
        return { ...n, x, y, vx: 0, vy: 0, r: n.is_center ? 30 : (count > 35 ? 16 : 22) };
      });

      const byId = new Map(nodes.map((n) => [n.id, n]));
      links = (graph.links || [])
        .map((l) => ({ ...l, s: byId.get(l.source), t: byId.get(l.target) }))
        .filter((l) => l.s && l.t);

      camera = { x: 0, y: 0, scale: 1 };
      needsFit = true;
      select(nodes.find((n) => n.is_center));
      if (nodes.length === 1) toast(`Nenhuma conexão acima de ${$('#graph-min-sim').value}% para este filme.`);
      frames = 0;
      kick();
    }

    // --- Física ---
    function step() {
      for (const l of links) {
        const dx = l.t.x - l.s.x, dy = l.t.y - l.s.y;
        const dist = Math.hypot(dx, dy) || 1;
        const force = (dist - (260 - l.similarity * 1.2)) * 0.03;
        const fx = (dx / dist) * force, fy = (dy / dist) * force;
        if (!l.s.is_center && drag?.node !== l.s) { l.s.vx += fx; l.s.vy += fy; }
        if (!l.t.is_center && drag?.node !== l.t) { l.t.vx -= fx; l.t.vy -= fy; }
      }
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i], b = nodes[j];
          const dx = b.x - a.x, dy = b.y - a.y;
          const d2 = dx * dx + dy * dy + 100;
          const d = Math.sqrt(d2);
          const f = 14000 / d2;
          const fx = (dx / d) * f, fy = (dy / d) * f;
          if (!a.is_center && drag?.node !== a) { a.vx -= fx; a.vy -= fy; }
          if (!b.is_center && drag?.node !== b) { b.vx += fx; b.vy += fy; }
        }
      }
      for (const n of nodes) {
        const k = n.is_center ? 0.1 : 0.004;
        if (drag?.node !== n) { n.vx -= n.x * k; n.vy -= n.y * k; }
        n.vx *= 0.85; n.vy *= 0.85;
        n.x += n.vx; n.y += n.vy;
      }
    }

    // Ajusta a câmera para enquadrar todos os nós (com margem para os rótulos).
    function fitView() {
      if (!nodes.length || !size.w) return;
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const n of nodes) {
        minX = Math.min(minX, n.x - n.r - 60); maxX = Math.max(maxX, n.x + n.r + 60);
        minY = Math.min(minY, n.y - n.r - 10); maxY = Math.max(maxY, n.y + n.r + 40);
      }
      const scale = Math.max(0.4, Math.min(1.3, size.w / (maxX - minX), size.h / (maxY - minY)));
      camera = { scale, x: -((minX + maxX) / 2) * scale, y: -((minY + maxY) / 2) * scale };
    }

    // --- Desenho ---
    const truncate = (text, max) => (text.length > max ? `${text.slice(0, max - 1)}…` : text);

    function draw() {
      const dpr = window.devicePixelRatio || 1;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.translate(size.w / 2 + camera.x, size.h / 2 + camera.y);
      ctx.scale(camera.scale, camera.scale);

      const focus = hovered || selected;
      const isFocusLink = (l) => focus && (l.s === focus || l.t === focus);

      for (const l of links) {
        const hi = isFocusLink(l);
        ctx.beginPath();
        ctx.moveTo(l.s.x, l.s.y);
        ctx.lineTo(l.t.x, l.t.y);
        ctx.strokeStyle = hi ? colors.primary : colors.ruleStrong;
        ctx.globalAlpha = hi ? 1 : Math.min(0.9, 0.35 + l.similarity / 150);
        ctx.lineWidth = hi ? 2 : Math.max(0.8, (l.similarity / 100) * 3);
        ctx.stroke();
        ctx.globalAlpha = 1;

        // Percentual só ao focar um filme relacionado; no centro poluiria o rótulo principal.
        if (hi && !focus.is_center) {
          const mx = (l.s.x + l.t.x) / 2, my = (l.s.y + l.t.y) / 2;
          const txt = `${l.similarity}%`;
          ctx.font = '700 11px Figtree, "Segoe UI", sans-serif';
          const tw = ctx.measureText(txt).width;
          ctx.fillStyle = colors.primary;
          ctx.beginPath();
          ctx.roundRect(mx - tw / 2 - 6, my - 10, tw + 12, 20, 10);
          ctx.fill();
          ctx.fillStyle = colors.onPrimary;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(txt, mx, my);
        }
      }

      for (const n of nodes) {
        const isSel = selected === n, isHov = hovered === n;
        ctx.save();
        ctx.translate(n.x, n.y);

        if (isSel || isHov) {
          ctx.beginPath();
          ctx.arc(0, 0, n.r + 5, 0, Math.PI * 2);
          ctx.strokeStyle = colors.primary;
          ctx.lineWidth = 2;
          ctx.stroke();
        }

        ctx.beginPath();
        ctx.arc(0, 0, n.r, 0, Math.PI * 2);
        ctx.fillStyle = n.is_center ? colors.primary : colors.paper;
        ctx.fill();
        ctx.strokeStyle = n.is_center ? colors.primary : colors.ink2;
        ctx.lineWidth = 1.5;
        ctx.stroke();

        // CineScore dentro do círculo.
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = n.is_center ? colors.onPrimary : colors.ink;
        ctx.font = `700 ${n.is_center ? 15 : 13}px "Bricolage Grotesque", "Segoe UI", sans-serif`;
        ctx.fillText(n.cinescore != null ? Math.round(n.cinescore) : '—', 0, 1);

        ctx.textBaseline = 'top';
        ctx.fillStyle = colors.ink;
        ctx.font = `${n.is_center ? '700 13px' : '600 12px'} Figtree, "Segoe UI", sans-serif`;
        ctx.fillText(truncate(n.title || '', (n.is_center || isSel || isHov) ? 32 : 20), 0, n.r + 6);
        if (n.is_center || isSel || isHov) {
          ctx.fillStyle = colors.muted;
          ctx.font = '11px Figtree, "Segoe UI", sans-serif';
          ctx.fillText([n.year, n.directors].filter(Boolean).join(' · '), 0, n.r + 22);
        }

        ctx.restore();
      }
    }

    function loop() {
      if (!active) { animId = null; return; }
      step();
      if (needsFit && frames >= 120) { fitView(); needsFit = false; }
      draw();
      frames++;
      animId = (frames < 300 || drag) ? requestAnimationFrame(loop) : null;
    }

    function kick() {
      if (!active || animId) return;
      if (frames >= 300 && !drag) { draw(); return; }
      animId = requestAnimationFrame(loop);
    }

    function reheat() {
      frames = Math.min(frames, 240);
      if (!animId && active) animId = requestAnimationFrame(loop);
    }

    // --- Interação ---
    function toWorld(e) {
      const rect = canvas.getBoundingClientRect();
      return {
        x: (e.clientX - rect.left - (size.w / 2 + camera.x)) / camera.scale,
        y: (e.clientY - rect.top - (size.h / 2 + camera.y)) / camera.scale
      };
    }

    function hit(p) {
      for (let i = nodes.length - 1; i >= 0; i--) {
        const n = nodes[i];
        if ((p.x - n.x) ** 2 + (p.y - n.y) ** 2 <= (n.r + 6) ** 2) return n;
      }
      return null;
    }

    canvas.addEventListener('pointerdown', (e) => {
      canvas.setPointerCapture(e.pointerId);
      const node = hit(toWorld(e));
      if (node) {
        drag = { node };
        select(node);
        reheat();
      } else {
        drag = { pan: { x: e.clientX - camera.x, y: e.clientY - camera.y } };
      }
    });

    canvas.addEventListener('pointermove', (e) => {
      if (drag?.node) {
        const p = toWorld(e);
        Object.assign(drag.node, { x: p.x, y: p.y, vx: 0, vy: 0 });
        reheat();
      } else if (drag?.pan) {
        camera.x = e.clientX - drag.pan.x;
        camera.y = e.clientY - drag.pan.y;
        kick();
      } else {
        const node = hit(toWorld(e));
        if (node !== hovered) {
          hovered = node;
          canvas.style.cursor = node ? 'pointer' : 'grab';
          kick();
        }
      }
    });

    const endDrag = () => { drag = null; kick(); };
    canvas.addEventListener('pointerup', endDrag);
    canvas.addEventListener('pointercancel', endDrag);
    canvas.addEventListener('pointerleave', () => { if (hovered) { hovered = null; kick(); } });

    canvas.addEventListener('dblclick', (e) => {
      const node = hit(toWorld(e));
      if (node && !node.is_center) load(node.id);
    });

    canvas.addEventListener('wheel', (e) => {
      e.preventDefault();
      camera.scale = Math.min(2.5, Math.max(0.4, camera.scale * (e.deltaY < 0 ? 1.1 : 0.9)));
      kick();
    }, { passive: false });

    $('#btn-recenter-graph').addEventListener('click', () => { fitView(); kick(); });
    $('#graph-min-sim').addEventListener('change', () => centerId && load(centerId));
    $('#graph-max-nodes').addEventListener('change', () => centerId && load(centerId));

    // --- Busca (no servidor) ---
    const searchInput = $('#graph-search');
    const results = $('#graph-search-results');
    let searchTimer;
    let searchSeq = 0;

    function closeResults() {
      results.hidden = true;
      searchInput.setAttribute('aria-expanded', 'false');
    }

    searchInput.addEventListener('input', () => {
      clearTimeout(searchTimer);
      const q = searchInput.value.trim();
      if (q.length < 2) { closeResults(); return; }
      searchTimer = setTimeout(async () => {
        const seq = ++searchSeq;
        let matches = [];
        try {
          ({ movies: matches } = await api(`/api/movies?search=${encodeURIComponent(q)}&page_size=8`));
        } catch { /* sem sugestões */ }
        if (seq !== searchSeq) return;
        results.innerHTML = matches.length
          ? matches.map((m) => `<li role="option" data-id="${esc(m.imdb_id)}"><span>${esc(m.title)}</span><small>${esc(m.year)}</small></li>`).join('')
          : '<li class="muted">Nenhum título encontrado</li>';
        results.hidden = false;
        searchInput.setAttribute('aria-expanded', 'true');
      }, 250);
    });

    searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeResults();
      if (e.key === 'Enter') {
        const first = $('li[data-id]', results);
        if (first) first.click();
      }
    });

    results.addEventListener('click', (e) => {
      const li = e.target.closest('li[data-id]');
      if (!li) return;
      searchInput.value = '';
      closeResults();
      load(li.dataset.id);
    });

    document.addEventListener('click', (e) => { if (!e.target.closest('.graph-search')) closeResults(); });

    // --- Inspetor ---
    function select(node) {
      selected = node;
      if (!node) return;
      $('#insp-tag').textContent = node.is_center ? 'Filme em foco' : 'Filme relacionado';
      $('#insp-title').textContent = node.title;

      // Todas as ligações deste filme na teia, da mais forte para a mais fraca, com os motivos.
      const myLinks = links.filter((l) => l.s === node || l.t === node)
        .map((l) => ({ other: l.s === node ? l.t : l.s, sim: l.similarity, reasons: l.reasons || [] }))
        .sort((x, y) => y.sim - x.sim);
      const affinity = myLinks.length ? `
        <section class="insp-section">
          <h3>Por que está ligado a ${plural(myLinks.length, 'filme', 'filmes')}</h3>
          <ul class="link-list">${myLinks.map((l) => `
            <li>
              <button class="link-head" data-select="${esc(l.other.id)}">
                <span>${esc(l.other.title)}${l.other.is_center ? ' <span class="tag">em foco</span>' : ''}</span>
                <strong>${Number(l.sim) || 0}%</strong>
              </button>
              <div class="bar"><span style="width:${Number(l.sim) || 0}%"></span></div>
              ${l.reasons.length ? `<ul class="reasons">${l.reasons.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}
            </li>`).join('')}
          </ul>
        </section>` : '';

      const tags = (list) => list.map((t) => `<span class="tag">${esc(t)}</span>`).join('');

      $('#insp-body').innerHTML = `
        <div class="insp-top">
          ${posterHTML(node, 'insp-poster')}
          <div class="insp-meta">
            <span>${esc([node.year, fmtRuntime(node.runtime)].filter(Boolean).join(' · '))}</span>
            ${node.directors ? `<span>Direção: ${esc(node.directors)}</span>` : ''}
            <span class="score">${fmtScore(node.cinescore)}</span>
            <span>CineScore · IMDb ${fmtScore(node.imdb_rating)} · ${fmtCompact(node.imdb_votes)} votos</span>
          </div>
        </div>
        ${node.synopsis ? `<section class="insp-section"><h3>Sinopse</h3>${synopsisHTML({ ...node, imdb_id: node.id }, 'clamp-6')}</section>` : ''}
        ${affinity}
        ${(node.genres || []).length ? `<section class="insp-section"><h3>Gêneros</h3><div class="tags">${tags(node.genres)}</div></section>` : ''}
        ${(node.writers || []).length ? `<section class="insp-section"><h3>Roteiro</h3><p>${esc(node.writers.join(', '))}</p></section>` : ''}
        ${(node.cast || []).length ? `<section class="insp-section"><h3>Elenco</h3><p>${esc(node.cast.join(', '))}</p></section>` : ''}
        <div class="insp-actions">
          ${node.is_center ? '' : '<button class="btn" data-act="center">Centralizar este filme</button>'}
          <button class="btn btn-quiet" data-act="details">Abrir ficha</button>
        </div>`;

      Translate.apply($('#insp-body'));
      $('#insp-body').onclick = (e) => {
        const pick = e.target.closest('[data-select]');
        if (pick) {
          const target = nodes.find((x) => x.id === pick.dataset.select);
          if (target) { select(target); return; }
        }
        const act = e.target.closest('[data-act]')?.dataset.act;
        if (act === 'center') load(node.id);
        if (act === 'details') MovieDialog.open(node.id);
      };
      kick();
    }

    // Centraliza um filme: imediatamente se o grafo estiver visível, senão ao ativar.
    function focus(id) {
      if (active) load(id); else pendingId = id;
    }

    function activate() {
      active = true;
      readColors();
      resize();
      if (pendingId) { load(pendingId); pendingId = null; }
      else if (!centerId) loadDefault();
      else reheat();
    }

    function deactivate() {
      active = false;
      if (animId) cancelAnimationFrame(animId);
      animId = null;
    }

    function refreshColors() {
      readColors();
      if (active) { draw(); }
    }

    return { activate, deactivate, focus, refreshColors };
  })();

  // ==========================================================
  // Descobrir — triagem de um título por vez
  // ==========================================================
  // ==========================================================
  // Perfis: vários conjuntos de respostas no mesmo navegador.
  // As chaves de trabalho (respostas, modelo e lista) são do perfil ativo; ao trocar,
  // o perfil atual é guardado na sua "gaveta" e o escolhido é carregado.
  // ==========================================================
  const Profiles = (() => {
    const INDEX = 'cinerank.v6.profiles';
    const SLOT = (id) => `cinerank.v6.slot.${id}`;
    const WORK = { decisions: 'cinerank.v2.discover.decisions', watchlist: 'cinerank.v2.watchlist', model: 'cinerank.v3.reco' };
    const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    const emptyModel = () => (window.CineReco ? window.CineReco.createState() : null);

    let index = storage.get(INDEX, null);
    if (!index || !Array.isArray(index.list) || !index.list.length) {
      const id = newId();
      index = { active: id, list: [{ id, name: 'Meu perfil', created: Date.now() }] };
      storage.set(INDEX, index);
    }
    const active = () => index.list.find((p) => p.id === index.active) || index.list[0];

    const readWork = () => Object.fromEntries(Object.entries(WORK).map(([k, key]) => [k, storage.get(key, null)]));
    function writeWork(d) {
      storage.set(WORK.decisions, d.decisions || {});
      storage.set(WORK.watchlist, d.watchlist || []);
      storage.set(WORK.model, d.model || emptyModel());
    }
    function save() { storage.set(INDEX, index); }
    function stash() { storage.set(SLOT(index.active), readWork()); }
    function go(id, data) {
      index.active = id;
      save();
      writeWork(data || storage.get(SLOT(id), null) || {});
      storage.set('cinerank.v5.persona', '');
      storage.set('cinerank.v5.own', null);
      location.reload();
    }

    function create(name, decisions = {}, from = null) {
      stash();
      const id = newId();
      index.list.push({ id, name: name.trim().slice(0, 40) || `Perfil ${index.list.length + 1}`, created: Date.now(), from });
      go(id, { decisions, watchlist: [], model: emptyModel() });
    }
    function use(id) { if (id !== index.active) { stash(); go(id); } }
    function rename(id) {
      const p = index.list.find((x) => x.id === id);
      const name = p && prompt('Novo nome do perfil', p.name);
      if (name && name.trim()) { p.name = name.trim().slice(0, 40); save(); render(); }
    }
    function remove(id) {
      const p = index.list.find((x) => x.id === id);
      if (!p || !confirm(`Apagar o perfil “${p.name}” e todas as respostas dele?`)) return;
      try { localStorage.removeItem(SLOT(id)); } catch { /* indisponível */ }
      index.list = index.list.filter((x) => x.id !== id);
      if (!index.list.length) {
        const nid = newId();
        index.list.push({ id: nid, name: 'Meu perfil', created: Date.now() });
      }
      if (id === index.active) go(index.list[0].id); else { save(); render(); }
    }
    function clearCurrent() {
      const n = Object.keys(storage.get(WORK.decisions, {}) || {}).length;
      if (!confirm(`Apagar as ${n} respostas e a lista “Quero ver” do perfil “${active().name}”? O perfil continua existindo, limpo.`)) return;
      go(index.active, { decisions: {}, watchlist: [], model: emptyModel() });
    }
    function clearAll() {
      if (!confirm(`Apagar os ${index.list.length} perfis e todas as respostas? Fica um perfil novo e limpo.`)) return;
      index.list.forEach((p) => { try { localStorage.removeItem(SLOT(p.id)); } catch { /* indisponível */ } });
      const id = newId();
      index = { active: id, list: [{ id, name: 'Meu perfil', created: Date.now() }] };
      go(id, { decisions: {}, watchlist: [], model: emptyModel() });
    }

    function render() {
      $('#profile-name').textContent = active().name;
      const cur = Object.keys(storage.get(WORK.decisions, {}) || {}).length;
      $('#pf-list').innerHTML = index.list.map((p) => {
        const isActive = p.id === index.active;
        const n = isActive ? cur : Object.keys((storage.get(SLOT(p.id), null) || {}).decisions || {}).length;
        return `<li class="pf-item${isActive ? ' is-active' : ''}">
          <div class="pf-info"><strong>${esc(p.name)}</strong>
            <span class="muted">${n} resposta${n === 1 ? '' : 's'}${p.from ? ` · a partir de ${esc(p.from)}` : ''}${isActive ? ' · em uso' : ''}</span></div>
          <div class="pf-actions">
            ${isActive ? '' : `<button class="btn btn-quiet" data-pf-use="${p.id}">Usar</button>`}
            <button class="btn btn-quiet" data-pf-rename="${p.id}">Renomear</button>
            <button class="btn btn-quiet" data-pf-remove="${p.id}" aria-label="Apagar ${esc(p.name)}">Apagar</button>
          </div></li>`;
      }).join('');
    }

    let personas = {};
    async function loadPersonas() {
      if (Object.keys(personas).length) return;
      try {
        const d = await api('/api/personas');
        personas = Object.fromEntries(d.personas.map((p) => [p.id, p]));
        $('#pf-persona-select').innerHTML = d.personas.map((p) => `<option value="${esc(p.id)}">${esc(p.id)} · ${esc(p.name)}</option>`).join('');
      } catch { $('#pf-persona').hidden = true; }
    }

    $('#pf-list').addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      if (b.dataset.pfUse) use(b.dataset.pfUse);
      if (b.dataset.pfRename) rename(b.dataset.pfRename);
      if (b.dataset.pfRemove) remove(b.dataset.pfRemove);
    });
    $('#pf-new').addEventListener('submit', (e) => { e.preventDefault(); create($('#pf-new-name').value); });
    $('#pf-persona').addEventListener('submit', (e) => {
      e.preventDefault();
      const p = personas[$('#pf-persona-select').value];
      if (p) create(p.name, Object.fromEntries(p.seed_likes.map((id) => [id, 'right'])), p.id);
    });
    $('#pf-clear').addEventListener('click', clearCurrent);
    $('#pf-clear-all').addEventListener('click', clearAll);
    $('.profile-btn').addEventListener('click', () => { render(); loadPersonas(); });
    render();
    return { name: () => active().name };
  })();

  // ==========================================================
  // Teste A/B: cada navegador cai numa variante da recomendação (ou escolhe em "Variante").
  // Eventos anônimos (ID aleatório, variante, filme, resposta) vão para /api/event.
  // ==========================================================
  const AB = (() => {
    const VARIANTS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I'];
    const ARMS = ['A', 'H', 'I'];  // sorteadas entre os visitantes; as demais só pelo seletor
    let sid = storage.get('cinerank.ab.sid', null);
    if (!sid) {
      sid = crypto.randomUUID ? crypto.randomUUID()
        : Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, '0')).join('');
      storage.set('cinerank.ab.sid', sid);
    }
    const forced = (new URLSearchParams(location.search).get('variante') || '').toUpperCase();
    let variant = VARIANTS.includes(forced) ? forced : storage.get('cinerank.ab.variant', null);
    if (!VARIANTS.includes(variant)) variant = ARMS[Math.floor(Math.random() * ARMS.length)];
    storage.set('cinerank.ab.variant', variant);

    let pending = [];
    const seen = new Set();
    function flush() {
      if (!pending.length) return;
      const body = JSON.stringify({ sid, variant, events: pending.splice(0) });
      const sent = navigator.sendBeacon && navigator.sendBeacon('/api/event', new Blob([body], { type: 'application/json' }));
      if (!sent) fetch('/api/event', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body, keepalive: true }).catch(() => {});
    }
    setInterval(flush, 5000);
    addEventListener('pagehide', flush);

    function track(type, data = {}) {
      if (type === 'impression') {
        const key = `${variant}|${data.surface}|${data.id}`;
        if (seen.has(key)) return;
        seen.add(key);
      }
      pending.push({ type, ...data });
      if (pending.length >= 20) flush();
    }
    function set(v) {
      if (!VARIANTS.includes(v) || v === variant) return;
      flush();
      variant = v;
      storage.set('cinerank.ab.variant', v);
      track('session');
    }
    track('session');
    return { get variant() { return variant; }, track, set };
  })();

  const Discover = (() => {
    const POOL_PAGES = 3;   // 3 × 500 = os 1.500 filmes mais votados (títulos que a pessoa provavelmente reconhece)
    const deck = $('#deck');
    const emptyEl = $('#deck-empty');
    const genreSelect = $('#discover-genre');
    const algoSelect = $('#discover-algo');
    const Reco = window.CineReco;

    const ACTIONS = {
      right: { label: 'Vi e gostei', weight: 1.0 },
      left: { label: 'Vi e não gostei', weight: -1.0 },
      up: { label: 'Quero ver', weight: 0.8 },
      down: { label: 'Sem interesse', weight: -0.5 }
    };
    const EXIT = {
      right: 'translate(140%, 0) rotate(18deg)',
      left: 'translate(-140%, 0) rotate(-18deg)',
      up: 'translate(0, -130%)',
      down: 'translate(0, 130%)'
    };

    // Respostas e lista usam IDs do IMDb (v2); o estado dos modelos de recomendação fica em v3.
    let decisions = storage.get('cinerank.v2.discover.decisions', {});
    let watchlist = storage.get('cinerank.v2.watchlist', []);
    let model = storage.get('cinerank.v3.reco', null);
    if (!Reco.isValidState(model)) {
      // Modelo de versão antiga: recomeça junto com as respostas, para os dois ficarem coerentes.
      model = Reco.createState();
      decisions = {};
    }
    // 'hybrid' = recomendação no backend (vetores de conteúdo + grafo + gosto coletivo); os demais rodam aqui.
    let algo = storage.get('cinerank.v4.algo', 'hybrid');
    algoSelect.value = algo;

    let pool = [];
    let poolKey = null;
    let queue = [];
    let topCard = null;
    let busy = false;

    const persist = () => {
      storage.set('cinerank.v2.discover.decisions', decisions);
      storage.set('cinerank.v2.watchlist', watchlist);
      storage.set('cinerank.v3.reco', model);
      storage.set('cinerank.v4.algo', algo);
      $('#watchlist-count').textContent = watchlist.length;
    };

    // Reordena a fila pelo algoritmo escolhido e atualiza o resumo do gosto aprendido.
    function rerank() {
      if (algo !== 'hybrid') queue = Reco.rank(model, algo, pool.filter((m) => !decisions[m.imdb_id]));
      renderProfile();
      renderTree();
    }

    // Fila vinda do backend. Mantém o cartão que já está na tela para não trocá-lo sob os olhos do usuário.
    let hybridSeq = 0;
    async function fetchHybrid({ keepTop = false } = {}) {
      const seq = ++hybridSeq;
      const answers = Object.entries(decisions).map(([id, action]) => ({ id, action }));
      let movies;
      try {
        const res = await fetch('/api/recommend', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ answers, n: 12, genre: genreSelect.value || null, variant: AB.variant }),
        });
        const data = await res.json();
        if (!res.ok || !data.success) throw new Error(data.error);
        movies = data.movies;
      } catch {
        toast('Recomendação do servidor indisponível. Usando o algoritmo local (LinUCB).');
        algo = 'linucb';
        algoSelect.value = 'linucb';
        await buildQueue();
        return;
      }
      if (seq !== hybridSeq) return;
      const fresh = movies.filter((m) => !decisions[m.imdb_id])
        .map((m) => ({ movie: m, why: m.why, source: m.source }));
      const top = keepTop && queue[0] && !decisions[queue[0].movie.imdb_id] ? queue[0] : null;
      queue = top ? [top, ...fresh.filter((it) => it.movie.imdb_id !== top.movie.imdb_id)] : fresh;
      renderProfile();
      renderTree();
      if (top) renderNext(); else render();
    }

    // Árvore de decisão: desenhada da esquerda (raiz) para a direita (folhas).
    function renderTree() {
      const el = $('#tree-view');
      const root = Reco.tree(model);
      const answered = model.memory.length;
      if (!root) {
        el.innerHTML = `<p class="tree-empty">${answered < 3
          ? `Avalie pelo menos 3 filmes, com algum de que você gostou e algum de que não gostou. <span class="muted">(${plural(answered, 'resposta', 'respostas')} até agora)</span>`
          : 'Ainda não dá para separar: até aqui você gostou de todos ou de nenhum. Avalie um filme com a resposta oposta.'}</p>`;
        return;
      }
      const current = queue[0]?.movie;
      const path = current ? Reco.treePath(root, current) : [];
      const answerAt = new Map(path.map((p) => [p.node, p.answer]));

      const leafHTML = (node) => {
        const active = answerAt.has(node);
        const pct = node.n ? Math.round((node.pos / node.n) * 100) : 0;
        const titles = node.samples.slice(0, 3)
          .map((t) => `<li class="${t.liked ? 'is-liked' : 'is-disliked'}">${esc(t.title)}</li>`).join('');
        const more = node.samples.length > 3 ? `<li class="more">+${node.samples.length - 3}</li>` : '';
        return `
          <div class="dt-leaf${active ? ' is-active' : ''}">
            <div class="dt-leaf-head"><strong>${pct}%</strong> gostou <span>${node.pos} de ${node.n}</span></div>
            <div class="dt-bar" aria-hidden="true"><span style="width:${pct}%"></span></div>
            <ul class="dt-titles">${titles}${more}</ul>
            ${active && current ? `<p class="dt-here">“${esc(current.title)}” cai aqui</p>` : ''}
          </div>`;
      };

      const branchHTML = (node) => {
        if (!node.question) return leafHTML(node);
        const answer = answerAt.get(node);
        return `
          <div class="dt-branch">
            <div class="dt-node${answerAt.has(node) ? ' is-active' : ''}">${node.category ? `<span class="dt-cat">${esc(node.category)}</span>` : ''}${esc(node.question)}?<small>${plural(node.n, 'filme', 'filmes')}</small></div>
            <div class="dt-children">
              <div class="dt-edge${answer === 'yes' ? ' is-active' : ''}"><span class="dt-label">Sim</span>${branchHTML(node.yes)}</div>
              <div class="dt-edge${answer === 'no' ? ' is-active' : ''}"><span class="dt-label">Não</span>${branchHTML(node.no)}</div>
            </div>
          </div>`;
      };

      el.innerHTML = root.question
        ? branchHTML(root)
        : `<p class="tree-empty">Nenhuma pergunta separa bem suas respostas ainda. Continue avaliando.</p>${leafHTML(root)}`;
    }

    function renderProfile() {
      const el = $('#taste-summary');
      const n = Object.keys(decisions).length;
      const { likes, dislikes } = Reco.profile(model, algo);
      if (algo === 'baseline') {
        el.innerHTML = 'Sem personalização: a fila segue qualidade e popularidade.';
      } else if (!likes.length && !dislikes.length) {
        el.innerHTML = n ? `${plural(n, 'resposta', 'respostas')}. Continue para o algoritmo aprender seu gosto.` : 'Responda alguns filmes para o algoritmo aprender seu gosto.';
      } else {
        const parts = [];
        if (likes.length) parts.push(`<strong>Curte:</strong> ${likes.map(esc).join(', ')}`);
        if (dislikes.length) parts.push(`<strong>Evita:</strong> ${dislikes.map(esc).join(', ')}`);
        el.innerHTML = `${parts.join(' · ')} <span class="muted">(${plural(n, 'resposta', 'respostas')})</span>`;
      }
    }

    async function loadPool() {
      const genre = genreSelect.value;
      if (poolKey === genre && pool.length) return;
      const pages = await Promise.all(Array.from({ length: POOL_PAGES }, (_, i) => {
        const params = new URLSearchParams({ sort: 'votes', page_size: 500, page: i + 1, facets: 1 });
        if (genre) params.set('genre', genre);
        return api(`/api/movies?${params}`).then((d) => d.movies);
      }));
      pool = pages.flat();
      poolKey = genre;
    }

    async function buildQueue() {
      if (algo === 'hybrid') {
        await fetchHybrid();
        return;
      }
      try {
        await loadPool();
      } catch {
        toast('Não foi possível carregar os filmes.');
        return;
      }
      rerank();
      render();
    }

    function cardHTML(item) {
      const m = item.movie;
      const meta = [m.year, fmtRuntime(m.runtime)].filter(Boolean).map(esc).join(' · ');
      return `
        <span class="deck-label" aria-hidden="true"></span>
        <div class="deck-head">
          ${posterHTML(m, 'deck-poster')}
          <div class="deck-head-text">
            <p class="eyebrow">${esc(m.genres || '')}</p>
            <h2 class="deck-title">${esc(m.title)}</h2>
            ${m.original_title ? `<p class="deck-original">${esc(m.original_title)}</p>` : ''}
            <p class="deck-meta">${meta}</p>
            <p class="deck-score"><span class="score">${fmtScore(m.cinescore)}</span>
              <span class="deck-meta">CineScore · IMDb ${fmtScore(m.imdb_rating)}</span></p>
          </div>
        </div>
        <p class="deck-why">${esc(algo === 'hybrid' && item.why ? item.why : Reco.explain(model, algo, item))}</p>
        <div class="deck-text">
          ${m.synopsis ? synopsisHTML(m, 'deck-synopsis') : '<p class="muted-text">Sinopse ainda não disponível para este filme.</p>'}
          ${m.cast ? `<p><span class="label">Elenco</span>${esc(m.cast)}</p>` : ''}
          ${m.directors ? `<p><span class="label">Direção</span>${esc(m.directors)}</p>` : ''}
        </div>`;
    }

    function renderNext() {
      const next = $('.deck-card.is-next', deck);
      if (next) next.remove();
      if (queue.length < 2 || !topCard) { if (!topCard) render(); return; }
      const card = document.createElement('article');
      card.className = 'deck-card is-next';
      card.innerHTML = cardHTML(queue[1]);
      deck.insertBefore(card, topCard);
      Translate.apply(card);
    }

    function render() {
      $$('.deck-card', deck).forEach((c) => c.remove());
      topCard = null;
      emptyEl.hidden = queue.length > 0;
      if (!queue.length) return;

      queue.slice(0, 2).reverse().forEach((item, idx, arr) => {
        const isTop = idx === arr.length - 1;
        const card = document.createElement('article');
        card.className = `deck-card${isTop ? '' : ' is-next'}`;
        card.innerHTML = cardHTML(item);
        if (isTop) {
          card.setAttribute('aria-label', item.movie.title);
          if (algo === 'hybrid') AB.track('impression', { id: item.movie.imdb_id, surface: 'discover' });
          attachDrag(card);
          topCard = card;
        }
        deck.appendChild(card);
      });
      Translate.apply(deck);
    }

    // Arraste com Pointer Events; os listeners vivem no próprio cartão e somem com ele.
    function attachDrag(card) {
      const label = $('.deck-label', card);
      let start = null;
      let delta = { x: 0, y: 0 };

      const dirOf = ({ x, y }) => {
        if (Math.max(Math.abs(x), Math.abs(y)) < 30) return null;
        return Math.abs(x) > Math.abs(y) ? (x > 0 ? 'right' : 'left') : (y < 0 ? 'up' : 'down');
      };

      card.addEventListener('pointerdown', (e) => {
        if (busy) return;
        start = { x: e.clientX, y: e.clientY };
        delta = { x: 0, y: 0 };
        card.setPointerCapture(e.pointerId);
        card.style.transition = 'none';
      });

      card.addEventListener('pointermove', (e) => {
        if (!start) return;
        delta = { x: e.clientX - start.x, y: e.clientY - start.y };
        card.style.transform = `translate(${delta.x}px, ${delta.y}px) rotate(${delta.x * 0.04}deg)`;
        const dir = dirOf(delta);
        label.textContent = dir ? ACTIONS[dir].label : '';
        label.style.opacity = dir ? Math.min(1, (Math.max(Math.abs(delta.x), Math.abs(delta.y)) - 30) / 70) : 0;
      });

      const release = () => {
        if (!start) return;
        start = null;
        const dir = Math.max(Math.abs(delta.x), Math.abs(delta.y)) > 90 ? dirOf(delta) : null;
        if (dir) {
          decide(dir);
        } else {
          card.style.transition = 'transform 0.2s ease';
          card.style.transform = '';
          label.style.opacity = 0;
        }
      };
      card.addEventListener('pointerup', release);
      card.addEventListener('pointercancel', release);
    }

    function decide(dir) {
      if (busy || !topCard || !queue.length) return;
      busy = true;
      const m = queue[0].movie;
      const action = ACTIONS[dir];

      decisions[m.imdb_id] = dir;
      Reco.update(model, m, dir);
      if (algo === 'hybrid') AB.track('answer', { id: m.imdb_id, action: dir, surface: 'discover' });
      if (dir === 'up' && !watchlist.some((w) => w.id === m.imdb_id)) {
        watchlist.unshift({ id: m.imdb_id, title: m.title, year: m.year, directors: m.directors });
        toast(`“${m.title}” adicionado à sua lista`);
      }
      persist();

      const label = $('.deck-label', topCard);
      label.textContent = action.label;
      label.style.opacity = 1;
      topCard.style.transition = 'transform 0.3s ease, opacity 0.3s ease';
      topCard.style.transform = EXIT[dir];
      topCard.style.opacity = '0';

      setTimeout(() => {
        if (algo === 'hybrid') {
          // Mostra o próximo cartão já carregado e pede ao servidor a fila atualizada em seguida.
          queue.shift();
          renderProfile();
          renderTree();
          render();
          busy = false;
          fetchHybrid({ keepTop: true });
          return;
        }
        rerank();
        render();
        busy = false;
      }, 260);
    }

    $$('.deck-btn').forEach((b) => b.addEventListener('click', () => decide(b.dataset.swipe)));

    const KEYS = { ArrowRight: 'right', ArrowLeft: 'left', ArrowUp: 'up', ArrowDown: 'down' };
    document.addEventListener('keydown', (e) => {
      if (currentView !== 'discover' || anyDialogOpen() || !KEYS[e.key]) return;
      if (e.target.closest?.('input, select, textarea')) return;
      e.preventDefault();
      decide(KEYS[e.key]);
    });

    genreSelect.addEventListener('change', buildQueue);
    const abSelect = $('#ab-variant');
    abSelect.value = AB.variant;
    abSelect.addEventListener('change', () => {
      AB.set(abSelect.value);
      if (algo === 'hybrid') fetchHybrid();
    });
    algoSelect.addEventListener('change', async () => {
      algo = algoSelect.value;
      persist();
      if (algo === 'hybrid') { await fetchHybrid(); return; }
      if (!pool.length) { await buildQueue(); return; }
      rerank();
      render();
    });

    $('#btn-clear-answers').addEventListener('click', () => {
      const n = Object.keys(decisions).length;
      if (!n || !confirm(`Apagar suas ${n} respostas e o que o algoritmo aprendeu? Sua lista "Quero ver" é mantida.`)) return;
      decisions = {};
      model = Reco.createState();
      persist();
      queue = [];
      buildQueue();
      toast('Respostas apagadas. A fila recomeçou do zero.');
    });

    $('#btn-reset-discover').addEventListener('click', () => {
      decisions = {};
      model = Reco.createState();
      persist();
      buildQueue();
    });

    // --- Minha lista ---
    const listEl = $('#watchlist-items');

    function renderWatchlist() {
      listEl.innerHTML = watchlist.length
        ? watchlist.map((m) => `
            <li>
              <div>
                <button class="wl-title" data-open-movie="${esc(m.id)}">${esc(m.title)}</button>
                <span class="wl-sub">${esc([m.year, m.directors].filter(Boolean).join(' · '))}</span>
              </div>
              <button class="btn btn-quiet" data-remove="${esc(m.id)}">Remover</button>
            </li>`).join('')
        : '<li class="wl-empty">Sua lista está vazia. Marque títulos como “Quero ver” para guardá-los aqui.</li>';
    }

    listEl.addEventListener('click', (e) => {
      const open = e.target.closest('[data-open-movie]');
      if (open) { $('#watchlist-dialog').close(); MovieDialog.open(open.dataset.openMovie); return; }
      const rm = e.target.closest('[data-remove]');
      if (rm) {
        watchlist = watchlist.filter((w) => w.id !== rm.dataset.remove);
        persist();
        renderWatchlist();
      }
    });

    $('#btn-open-watchlist').addEventListener('click', () => { renderWatchlist(); openDialog('watchlist-dialog'); });

    $('#btn-clear-watchlist').addEventListener('click', () => {
      if (!watchlist.length || !confirm('Remover todos os títulos da sua lista?')) return;
      watchlist = [];
      persist();
      renderWatchlist();
    });

    function activate() {
      if (!queue.length) buildQueue();
    }

    persist();
    const answers = () => Object.entries(decisions).map(([id, action]) => ({ id, action }));
    return { activate, answers, profile: () => Reco.profile(model, 'linucb') };
  })();

  // ==========================================================
  // Para você — lista de recomendações com o motivo de cada uma
  // ==========================================================
  const ForYou = (() => {
    const list = $('#foryou-list');
    const LABEL = { collab: 'Gosto parecido', graph: 'Mesma equipe ou temas', content: 'História parecida',
                    exploracao: 'Para explorar', menos_conhecido: 'Menos conhecido', popular: 'Popular e bem avaliado', perfil: 'Seu perfil' };

    async function load() {
      const answers = Discover.answers();
      const liked = answers.filter((a) => a.action === 'right' || a.action === 'up').length;
      const { likes, dislikes } = Discover.profile();
      $('#foryou-summary').innerHTML = answers.length
        ? `<strong>Perfil:</strong> ${esc(Profiles.name())} · ${plural(answers.length, 'resposta', 'respostas')}${likes.length ? ` · <strong>Curte:</strong> ${likes.map(esc).join(', ')}` : ''}${dislikes.length ? ` · <strong>Evita:</strong> ${dislikes.map(esc).join(', ')}` : ''}`
        : '';
      if (!liked) {
        list.innerHTML = `<div class="empty-state"><h2>Ainda não sabemos do que você gosta</h2>
          <p>Marque alguns filmes como “Vi e gostei” ou “Quero ver” no Descobrir.</p>
          <button class="btn" data-goto="discover">Ir para o Descobrir</button></div>`;
        return;
      }
      list.innerHTML = '<p class="loading-row">Calculando recomendações…</p>';
      try {
        const res = await fetch('/api/recommend', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ answers, n: 24, genre: $('#foryou-genre').value || null, variant: AB.variant }) });
        const data = await res.json();
        if (!res.ok || !data.success) throw new Error();
        data.movies.forEach((m, i) => AB.track('impression', { id: m.imdb_id, pos: i, surface: 'foryou' }));
        list.innerHTML = data.movies.map((m, i) => `
          <article class="fy-card">
            ${posterHTML(m, 'fy-poster')}
            <div class="fy-body">
              <p class="eyebrow">${i + 1}º · ${esc(LABEL[m.source] || 'Recomendado')}</p>
              <h2 class="fy-title"><button class="wl-title" data-open-movie="${esc(m.imdb_id)}">${esc(m.title)}</button></h2>
              <p class="deck-meta">${esc([m.year, fmtRuntime(m.runtime), m.genres].filter(Boolean).join(' · '))} · <strong class="fy-score">${fmtScore(m.cinescore)}</strong> CineScore</p>
              <p class="fy-why">${esc(m.why)}</p>
              ${m.synopsis ? synopsisHTML(m, 'fy-synopsis') : ''}
            </div>
          </article>`).join('');
        Translate.apply(list);
      } catch {
        list.innerHTML = '<p class="loading-row">Não foi possível calcular as recomendações agora.</p>';
      }
    }

    list.addEventListener('click', (e) => {
      const open = e.target.closest('[data-open-movie]');
      if (open) {
        AB.track('open', { id: open.dataset.openMovie, surface: 'foryou' });
        MovieDialog.open(open.dataset.openMovie);
      }
      if (e.target.closest('[data-goto]')) showView('discover');
    });
    $('#btn-foryou-refresh').addEventListener('click', load);
    $('#foryou-genre').addEventListener('change', load);
    return { activate: load };
  })();

  // ==========================================================
  // Cenários: recomendações de cada variante para uma persona de teste
  // ==========================================================
  const Scenarios = (() => {
    const sel = $('#scenario-persona');
    const chips = $('#scenario-variants');
    const head = $('#scenario-head');
    const grid = $('#scenario-grid');
    const SOURCE = { collab: 'Gosto parecido', graph: 'Mesma equipe ou temas', content: 'História parecida', exploracao: 'Exploração',
                     menos_conhecido: 'Menos conhecido', popular: 'Popular', perfil: 'Perfil' };
    const TIER = { 'Pouco conhecido': 'tier-low', 'Médio': 'tier-mid', 'Muito conhecido': 'tier-high' };
    let loaded = false;
    let labels = {};
    let picked = storage.get('cinerank.v5.scenario.variants', ['A', 'H', 'I']);

    async function init() {
      const [p, v] = await Promise.all([api('/api/personas'), api('/api/variants')]);
      labels = v.variants;
      sel.innerHTML = p.personas.map((x) => `<option value="${esc(x.id)}">${esc(x.id)} · ${esc(x.name)}</option>`).join('');
      sel.value = storage.get('cinerank.v5.scenario.persona', p.personas[0].id);
      chips.innerHTML = Object.entries(labels).map(([k, label]) => `<label class="chip" title="${esc(label)}">
        <input type="checkbox" value="${k}" ${picked.includes(k) ? 'checked' : ''}> ${k}${v.arms.includes(k) ? ' <small>A/B</small>' : ''}</label>`).join('');
      loaded = true;
    }

    function card(m, i, base) {
      const fresh = base && !base.has(m.imdb_id);
      return `<article class="sc-card${fresh ? ' is-new' : ''}">
        ${posterHTML(m, 'sc-poster')}
        <div class="sc-body">
          <p class="eyebrow">${i + 1}º · ${esc(SOURCE[m.source] || 'Recomendado')}${fresh ? ' · <span class="sc-new">diferente de A</span>' : ''}</p>
          <h3 class="sc-title"><button class="wl-title" data-open-movie="${esc(m.imdb_id)}">${esc(m.title)}</button></h3>
          <p class="sc-meta">${esc([m.year, m.genres].filter(Boolean).join(' · '))}</p>
          <p class="sc-badges">${m.tier ? `<span class="tier ${TIER[m.tier] || ''}">${esc(m.tier)}</span>` : ''}${m.awards ? `<span class="tier">${m.awards} prêmio${m.awards > 1 ? 's' : ''}</span>` : ''}${m.nominations ? `<span class="tier">${m.nominations} indicaç${m.nominations > 1 ? 'ões' : 'ão'}</span>` : ''}<span class="tier">CineScore ${fmtScore(m.cinescore)}</span></p>
          <p class="sc-why">${esc(m.why)}</p>
        </div>
      </article>`;
    }

    function summary(list) {
      const n = list.length || 1;
      const less = list.filter((m) => m.tier && m.tier !== 'Muito conhecido').length;
      const awarded = list.filter((m) => m.awards || m.nominations).length;
      const score = list.reduce((a, m) => a + (m.cinescore || 0), 0) / n;
      return `${less} menos conhecido${less === 1 ? '' : 's'} · ${awarded} com prêmio ou indicação · CineScore médio ${fmtScore(score)}`;
    }

    async function load() {
      if (!loaded) await init();
      picked = $$('input', chips).filter((c) => c.checked).map((c) => c.value).slice(0, 4);
      if (!picked.length) picked = ['A'];
      storage.set('cinerank.v5.scenario.variants', picked);
      storage.set('cinerank.v5.scenario.persona', sel.value);
      grid.innerHTML = '<p class="loading-row">Calculando as recomendações de cada variante…</p>';
      let d;
      try {
        d = await api(`/api/scenario?persona=${encodeURIComponent(sel.value)}&variants=${picked.join(',')}&n=10`);
      } catch {
        grid.innerHTML = '<p class="loading-row">Não foi possível calcular os cenários agora.</p>';
        return;
      }
      const p = d.persona;
      head.innerHTML = `<div class="sc-persona">
        <div><p class="eyebrow">${esc(p.id)} · ${p.users.toLocaleString('pt-BR')} usuários reais no grupo</p>
          <h2 class="sc-persona-name">${esc(p.name)}</h2>
          ${(p.traits.length + p.moods.length) ? `<p class="sc-badges">${[...p.traits, ...p.moods].map((t) => `<span class="tier">${esc(t)}</span>`).join('')}</p>` : ''}</div>
        <div><p class="field-label">Parte destes filmes, marcados como “gostei”</p>
          <p class="sc-seeds">${d.seeds.map((m) => `<button class="wl-title" data-open-movie="${esc(m.imdb_id)}">${esc(m.title)}</button>`).join(' · ')}</p></div>
      </div>`;
      const base = d.variants.A ? new Set(d.variants.A.movies.map((m) => m.imdb_id)) : null;
      grid.style.setProperty('--cols', Object.keys(d.variants).length);
      grid.innerHTML = Object.entries(d.variants).map(([v, x]) => `
        <section class="sc-col" aria-label="Variante ${v}">
          <header class="sc-col-head"><span class="sc-chip">${v}</span><div><h3>${esc(x.label)}</h3><p>${summary(x.movies)}</p></div></header>
          ${x.movies.map((m, i) => card(m, i, v === 'A' ? null : base)).join('')}
        </section>`).join('');
      Translate.apply(grid);
    }

    sel.addEventListener('change', load);
    chips.addEventListener('change', load);
    $('#btn-scenario-random').addEventListener('click', () => {
      const opts = $$('option', sel);
      sel.value = opts[Math.floor(Math.random() * opts.length)].value;
      load();
    });
    $('#view-scenarios').addEventListener('click', (e) => {
      const open = e.target.closest('[data-open-movie]');
      if (open) MovieDialog.open(open.dataset.openMovie);
    });
    return { activate: load };
  })();

  // ==========================================================
  // Inicialização
  // ==========================================================
  // ==========================================================
  // Cor primária (escolha do usuário, salva neste navegador)
  // ==========================================================
  const Theme = (() => {
    const KEY = 'cinerank.primary';
    const DEFAULT = '#c8102e';
    const PRESETS = [
      { name: 'Carmim', hex: '#c8102e' },
      { name: 'Cobalto', hex: '#1f4fd8' },
      { name: 'Esmeralda', hex: '#0b7a4b' },
      { name: 'Violeta', hex: '#6d28d9' },
      { name: 'Cobre', hex: '#b4500f' },
      { name: 'Petróleo', hex: '#0e6e7e' },
    ];
    const root = document.documentElement.style;
    const swatchesEl = $('#theme-swatches');
    const customEl = $('#theme-custom');

    // Contraste WCAG entre a cor e o branco do fundo.
    function luminance(hex) {
      const n = parseInt(hex.slice(1), 16);
      const [r, g, b] = [n >> 16, (n >> 8) & 255, n & 255].map((v) => {
        v /= 255;
        return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    }
    const contrastWithWhite = (hex) => 1.05 / (luminance(hex) + 0.05);

    function current() {
      return storage.get(KEY, null) || DEFAULT;
    }

    function apply(hex, { save = true } = {}) {
      hex = hex.toLowerCase();
      const ratio = contrastWithWhite(hex);
      root.setProperty('--primary', hex);
      // Texto sobre a primária: branco quando legível, senão preto.
      root.setProperty('--on-primary', ratio >= 4.5 ? '#ffffff' : '#0b0b0c');
      if (save) {
        try { localStorage.setItem(KEY, hex); } catch { /* indisponível */ }
      }
      customEl.value = hex;
      $('#theme-hex').textContent = hex.toUpperCase();
      $$('.swatch', swatchesEl).forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.hex === hex)));

      const r = ratio.toLocaleString('pt-BR', { maximumFractionDigits: 1 });
      $('#theme-contrast').innerHTML = ratio >= 4.5
        ? `Contraste com o fundo: <strong>${r}:1</strong>. Legível como texto (mínimo recomendado: 4,5:1).`
        : `Contraste com o fundo: <strong>${r}:1</strong>. Abaixo de 4,5:1, links e números na cor primária ficam difíceis de ler. Prefira um tom mais escuro.`;
      Graph.refreshColors();
    }

    function init() {
      swatchesEl.innerHTML = PRESETS.map((p) => `
        <button class="swatch" data-hex="${p.hex}" aria-pressed="false"><i style="background:${p.hex}"></i>${esc(p.name)}</button>`).join('');
      swatchesEl.addEventListener('click', (e) => {
        const b = e.target.closest('.swatch');
        if (b) apply(b.dataset.hex);
      });
      customEl.addEventListener('input', (e) => apply(e.target.value));
      $('#theme-reset').addEventListener('click', () => {
        try { localStorage.removeItem(KEY); } catch { /* indisponível */ }
        apply(DEFAULT, { save: false });
      });
      apply(current(), { save: false });
    }

    return { init };
  })();

  Theme.init();
  loadFacets();
  Ranking.init();

  const fromHash = Object.keys(VIEWS).find((k) => VIEWS[k] === location.hash) || 'catalog';
  showView(fromHash, { updateHash: false });
})();
