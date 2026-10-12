/**
 * CineRank Streaming - Frontend Controller & Graph Engine
 * Strict 3-color palette (White, Black, Blue shades) & Standard Sans-serif
 */

document.addEventListener('DOMContentLoaded', () => {
  // Estado da Aplicação
  let currentService = 'all';
  let currentTimeframe = '60';
  let currentSort = 'cinescore';
  let searchQuery = '';
  let viewMode = 'cards';
  let currentModalMovieId = null;
  let allCatalogMovies = [];

  // Elementos do DOM: Catálogo
  const catalogSection = document.getElementById('catalog-view-section');
  const graphSection = document.getElementById('graph-view-section');
  const tabBtnCatalog = document.getElementById('tab-btn-catalog');
  const tabBtnGraph = document.getElementById('tab-btn-graph');

  const cardsContainer = document.getElementById('movies-cards-container');
  const tableContainer = document.getElementById('movies-table-container');
  const tableBody = document.getElementById('movies-table-body');
  const emptyResultsCard = document.getElementById('empty-results-card');
  const resultsCountText = document.getElementById('results-count-text');

  const servicePills = document.getElementById('service-pills');
  const searchInput = document.getElementById('search-input');
  const btnClearSearch = document.getElementById('btn-clear-search');
  const timeframeSelect = document.getElementById('timeframe-select');
  const sortSelect = document.getElementById('sort-select');
  const btnResetFilters = document.getElementById('btn-reset-filters');
  const btnSyncApi = document.getElementById('btn-sync-api');

  const btnViewCards = document.getElementById('btn-view-cards');
  const btnViewTable = document.getElementById('btn-view-table');

  // Indicadores de Métricas
  const metricTotalMovies = document.getElementById('metric-total-movies');
  const metricTopMovie = document.getElementById('metric-top-movie');
  const metricTopPlatform = document.getElementById('metric-top-platform');
  const metricAvgScore = document.getElementById('metric-avg-score');
  const metricTotalReviews = document.getElementById('metric-total-reviews');

  // Modal
  const movieModal = document.getElementById('movie-modal');
  const modalCloseBtn = document.getElementById('modal-close-btn');
  const modalProviderBadge = document.getElementById('modal-provider-badge');
  const modalDateTag = document.getElementById('modal-date-tag');
  const modalCinescoreVal = document.getElementById('modal-cinescore-val');
  const modalMovieTitle = document.getElementById('modal-movie-title');
  const modalPoster = document.getElementById('modal-poster');
  const modalTagsRow = document.getElementById('modal-tags-row');
  const modalSynopsisText = document.getElementById('modal-synopsis-text');
  const modalDirector = document.getElementById('modal-director');
  const modalCast = document.getElementById('modal-cast');
  const modalRuntime = document.getElementById('modal-runtime');
  const modalRtVal = document.getElementById('modal-rt-val');
  const modalPopcornVal = document.getElementById('modal-popcorn-val');
  const modalImdbVal = document.getElementById('modal-imdb-val');
  const modalCommVal = document.getElementById('modal-comm-val');
  const modalTrailerBox = document.getElementById('modal-trailer-box');
  const modalTrailerIframe = document.getElementById('modal-trailer-iframe');
  const modalReviewsCountPill = document.getElementById('modal-reviews-count-pill');
  const modalReviewsFeed = document.getElementById('modal-reviews-feed');
  const reviewSubmissionForm = document.getElementById('review-submission-form');
  const formMovieId = document.getElementById('form-movie-id');
  const btnModalOpenGraph = document.getElementById('btn-modal-open-graph');
  const toastStack = document.getElementById('toast-stack');

  // Elementos do Grafo
  const graphCanvas = document.getElementById('graph-canvas');
  const graphCanvasContainer = document.getElementById('graph-canvas-container');
  const graphLoader = document.getElementById('graph-loader');
  const graphMinSimSelect = document.getElementById('graph-min-sim');
  const graphMaxNodesSelect = document.getElementById('graph-max-nodes');
  const btnRecenterGraph = document.getElementById('btn-recenter-graph');
  const graphMovieSearch = document.getElementById('graph-movie-search');
  const graphSearchDropdown = document.getElementById('graph-search-dropdown');
  const inspTag = document.getElementById('insp-tag');
  const inspTitle = document.getElementById('insp-title');
  const inspBody = document.getElementById('insp-body');

  // Estado do Grafo
  let currentGraphMovieId = null;
  let graphNodes = [];
  let graphLinks = [];
  let graphSimulationActive = false;
  let simAnimId = null;
  let isGraphViewActive = false;

  let camera = { x: 0, y: 0, scale: 1 };
  let isDraggingNode = false;
  let isPanningCanvas = false;
  let dragTarget = null;
  let hoveredTarget = null;
  let selectedTarget = null;
  let panOrigin = { x: 0, y: 0 };
  const posterImgCache = {};

  // Carga Inicial
  loadStats();
  loadMovies();

  // ==========================================
  // NAVEGAÇÃO DE ABAS: CATÁLOGO vs GRAFO vs CINESWIPE
  // ==========================================
  tabBtnCatalog.addEventListener('click', () => {
    switchViewTab('catalog');
  });

  tabBtnGraph.addEventListener('click', () => {
    switchViewTab('graph');
  });

  const tabBtnCineswipe = document.getElementById('tab-btn-cineswipe');
  if (tabBtnCineswipe) {
    tabBtnCineswipe.addEventListener('click', () => {
      switchViewTab('cineswipe');
    });
  }

  function switchViewTab(mode) {
    tabBtnCatalog.classList.remove('active');
    tabBtnGraph.classList.remove('active');
    if (tabBtnCineswipe) tabBtnCineswipe.classList.remove('active');

    catalogSection.style.display = 'none';
    graphSection.style.display = 'none';
    const cineswipeSec = document.getElementById('cineswipe-view-section');
    if (cineswipeSec) cineswipeSec.style.display = 'none';

    isGraphViewActive = false;
    isCineswipeActive = false;
    if (simAnimId) cancelAnimationFrame(simAnimId);

    if (mode === 'catalog') {
      tabBtnCatalog.classList.add('active');
      catalogSection.style.display = 'block';
    } else if (mode === 'graph') {
      isGraphViewActive = true;
      tabBtnGraph.classList.add('active');
      graphSection.style.display = 'block';

      // Redimensionar canvas e inicializar
      initCanvasSize();
      if (!currentGraphMovieId) {
        loadDefaultGraph();
      } else {
        startSimulation();
      }
    } else if (mode === 'cineswipe') {
      isCineswipeActive = true;
      if (tabBtnCineswipe) tabBtnCineswipe.classList.add('active');
      if (cineswipeSec) cineswipeSec.style.display = 'flex';
      initCineswipeDeck();
    }
  }

  // ==========================================
  // EVENTOS DO CATÁLOGO
  // ==========================================
  servicePills.addEventListener('click', (e) => {
    const pill = e.target.closest('.service-pill');
    if (!pill) return;
    document.querySelectorAll('.service-pill').forEach(p => p.classList.remove('active'));
    pill.classList.add('active');
    currentService = pill.dataset.service;
    loadMovies();
  });

  timeframeSelect.addEventListener('change', () => {
    currentTimeframe = timeframeSelect.value;
    loadMovies();
  });

  sortSelect.addEventListener('change', () => {
    currentSort = sortSelect.value;
    loadMovies();
  });

  let searchTimer = null;
  searchInput.addEventListener('input', () => {
    const val = searchInput.value.trim();
    btnClearSearch.style.display = val ? 'block' : 'none';
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      searchQuery = val;
      loadMovies();
    }, 200);
  });

  btnClearSearch.addEventListener('click', () => {
    searchInput.value = '';
    btnClearSearch.style.display = 'none';
    searchQuery = '';
    loadMovies();
  });

  btnResetFilters.addEventListener('click', () => {
    searchInput.value = '';
    btnClearSearch.style.display = 'none';
    searchQuery = '';
    currentService = 'all';
    currentTimeframe = '60';
    currentSort = 'cinescore';
    timeframeSelect.value = '60';
    sortSelect.value = 'cinescore';
    document.querySelectorAll('.service-pill').forEach(p => p.classList.remove('active'));
    document.querySelector('.service-pill[data-service="all"]').classList.add('active');
    loadMovies();
  });

  btnViewCards.addEventListener('click', () => {
    viewMode = 'cards';
    btnViewCards.classList.add('active');
    btnViewTable.classList.remove('active');
    cardsContainer.style.display = 'grid';
    tableContainer.style.display = 'none';
  });

  btnViewTable.addEventListener('click', () => {
    viewMode = 'table';
    btnViewTable.classList.add('active');
    btnViewCards.classList.remove('active');
    cardsContainer.style.display = 'none';
    tableContainer.style.display = 'block';
  });

  btnSyncApi.addEventListener('click', async () => {
    btnSyncApi.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Atualizando...';
    btnSyncApi.disabled = true;
    try {
      const res = await fetch('/api/sync', { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        showToast(`Catálogo atualizado com ${data.count} títulos via TMDB e OMDb.`);
        await loadStats();
        await loadMovies();
      } else {
        showToast(data.error || 'Erro na sincronização.', 'error');
      }
    } catch (err) {
      showToast('Erro de conexão ao sincronizar com as APIs.', 'error');
    } finally {
      btnSyncApi.disabled = false;
      btnSyncApi.innerHTML = '<i class="fa-solid fa-rotate"></i> Atualizar Catálogos';
    }
  });

  // Modal Fechamento
  modalCloseBtn.addEventListener('click', closeModal);
  movieModal.addEventListener('click', (e) => {
    if (e.target === movieModal) closeModal();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && movieModal.style.display === 'flex') closeModal();
  });

  if (btnModalOpenGraph) {
    btnModalOpenGraph.addEventListener('click', () => {
      if (currentModalMovieId) {
        closeModal();
        window.switchToGraph(currentModalMovieId);
      }
    });
  }

  // Avaliações
  reviewSubmissionForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const mid = formMovieId.value;
    const author = document.getElementById('form-author').value.trim();
    const rating = parseFloat(document.getElementById('form-rating').value);
    const text = document.getElementById('form-text').value.trim();

    if (!text) return;

    const btnSubmit = document.getElementById('btn-submit-review');
    btnSubmit.disabled = true;
    btnSubmit.textContent = 'Enviando...';

    try {
      const res = await fetch(`/api/movies/${mid}/reviews`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ author, rating, text })
      });
      const data = await res.json();
      if (data.success) {
        showToast('Avaliação registrada com sucesso!');
        document.getElementById('form-text').value = '';
        window.openModal(mid);
        loadStats();
      } else {
        showToast(data.error || 'Erro ao registrar avaliação.', 'error');
      }
    } catch (err) {
      showToast('Erro de conexão ao enviar avaliação.', 'error');
    } finally {
      btnSubmit.disabled = false;
      btnSubmit.innerHTML = 'Publicar Avaliação';
    }
  });

  // ==========================================
  // CARREGAR DADOS DO CATÁLOGO
  // ==========================================
  async function loadStats() {
    try {
      const res = await fetch('/api/stats');
      const data = await res.json();
      if (data.success && data.stats) {
        const s = data.stats;
        metricTotalMovies.textContent = s.total_movies || 0;
        if (s.top_movie) {
          metricTopMovie.textContent = `${s.top_movie.title} (${s.top_movie.score})`;
          metricTopPlatform.textContent = s.top_movie.streaming || 'Streaming';
        }
        metricAvgScore.textContent = s.avg_score ? `${s.avg_score}/100` : '--';
        metricTotalReviews.textContent = `${s.total_reviews} avaliações`;
      }
    } catch (err) {
      console.error('Stats error:', err);
    }
  }

  async function loadMovies() {
    cardsContainer.innerHTML = `
      <div class="loading-state">
        <div class="loading-spin"></div>
        <p>Consultando catálogo e calculando posições...</p>
      </div>
    `;
    emptyResultsCard.style.display = 'none';

    try {
      const params = new URLSearchParams({
        service: currentService,
        timeframe: currentTimeframe,
        sort: currentSort,
        search: searchQuery
      });
      const res = await fetch(`/api/movies?${params.toString()}`);
      const data = await res.json();

      if (data.success) {
        allCatalogMovies = data.movies || [];
        renderMovies(allCatalogMovies);
      } else {
        cardsContainer.innerHTML = `<div class="empty-state-box">Erro ao carregar dados.</div>`;
      }
    } catch (err) {
      console.error('Movies fetch error:', err);
      cardsContainer.innerHTML = `<div class="empty-state-box">Falha de conexão com o servidor.</div>`;
    }
  }

  function getScoreClass(score) {
    if (score >= 80) return 'score-dark';
    if (score >= 65) return 'score-mid';
    return 'score-light';
  }

  function renderMovies(movies) {
    resultsCountText.textContent = `Exibindo ${movies.length} título${movies.length !== 1 ? 's' : ''} no ranking atual`;

    if (!movies || movies.length === 0) {
      cardsContainer.innerHTML = '';
      tableBody.innerHTML = '';
      emptyResultsCard.style.display = 'block';
      return;
    }
    emptyResultsCard.style.display = 'none';

    // 1. Grade de Cartões
    cardsContainer.innerHTML = movies.map((m, idx) => {
      const rank = idx + 1;
      const sc = m.scores || {};
      const cinescore = sc.cinescore !== undefined ? sc.cinescore : 70;
      const scoreCls = getScoreClass(cinescore);

      const rtVal = sc.tomatometer !== undefined && sc.tomatometer !== null ? `${sc.tomatometer}%` : 'N/D';
      const popVal = sc.popcornmeter !== undefined && sc.popcornmeter !== null ? `${sc.popcornmeter}%` : 'N/D';
      const imdbVal = sc.imdb ? `${sc.imdb}` : 'N/D';

      return `
        <article class="movie-card-editorial" data-id="${m.id}">
          <div class="poster-col">
            <span class="rank-badge">#${rank}</span>
            <img class="poster-img" src="${m.poster_url}" alt="${m.title}" loading="lazy" onerror="this.src='https://placehold.co/300x450/eff6ff/1e3a8a?text=Sem+Foto'">
          </div>

          <div class="info-col">
            <div class="meta-header-row">
              <span class="service-pill-tag">${m.streaming_name}</span>
              <span class="release-date-tag">${m.release_date || '2026'}</span>
            </div>

            <h3 class="movie-title-text">${m.title}</h3>
            <p class="genres-text">${m.genres ? m.genres.slice(0, 3).join(' • ') : 'Filme'}</p>

            <div class="scores-row-strip">
              <span class="score-cell" title="Rotten Tomatoes"><i class="fa-solid fa-apple-whole"></i> ${rtVal}</span>
              <span class="score-cell" title="Popcornmeter Público"><i class="fa-solid fa-bucket"></i> ${popVal}</span>
              <span class="score-cell" title="IMDb"><i class="fa-solid fa-star"></i> ${imdbVal}</span>
              <span class="cinescore-badge ${scoreCls}" title="CineScore">${cinescore}</span>
            </div>

            <p class="synopsis-snip">${m.synopsis || 'Sem descrição.'}</p>

            <div class="actions-row">
              <button class="btn btn-primary btn-sm" onclick="window.openModal('${m.id}')">
                Ficha & Resenhas
              </button>
              <button class="btn btn-outline btn-sm" onclick="window.switchToGraph('${m.id}')" title="Ver no Grafo de Relações">
                <i class="fa-solid fa-circle-nodes"></i> Grafo
              </button>
              ${m.trailer_youtube_id ? `
                <button class="btn btn-outline btn-sm" onclick="window.openTrailerDirect('${m.id}')" title="Assistir Trailer">
                  <i class="fa-solid fa-play"></i> Trailer
                </button>
              ` : ''}
            </div>
          </div>
        </article>
      `;
    }).join('');

    // 2. Tabela Detalhada
    tableBody.innerHTML = movies.map((m, idx) => {
      const rank = idx + 1;
      const sc = m.scores || {};
      const cinescore = sc.cinescore !== undefined ? sc.cinescore : 70;
      const scoreCls = getScoreClass(cinescore);

      return `
        <tr>
          <td style="font-weight: 800; color: var(--blue-800);">#${rank}</td>
          <td>
            <img class="table-poster-thumb" src="${m.poster_url}" alt="${m.title}" loading="lazy">
          </td>
          <td>
            <span class="table-title-bold">${m.title}</span><br>
            <span class="table-info-sub">${m.director || 'Direção'} • ${m.runtime || '105 min'}</span>
          </td>
          <td>
            <span class="service-pill-tag">${m.streaming_name}</span>
          </td>
          <td style="font-size: 12px; color: var(--text-muted);">${m.release_date || '2026'}</td>
          <td style="text-align: center; font-weight: 700;">
            ${sc.tomatometer !== undefined && sc.tomatometer !== null ? `${sc.tomatometer}%` : 'N/D'}
          </td>
          <td style="text-align: center; font-weight: 700;">
            ${sc.popcornmeter !== undefined && sc.popcornmeter !== null ? `${sc.popcornmeter}%` : 'N/D'}
          </td>
          <td style="text-align: center; font-weight: 700;">
            ${sc.imdb ? `${sc.imdb}` : 'N/D'}
          </td>
          <td style="text-align: center;">
            <span class="cinescore-badge ${scoreCls}">${cinescore}</span>
          </td>
          <td style="text-align: right; white-space: nowrap;">
            <button class="btn btn-primary btn-sm" onclick="window.openModal('${m.id}')" style="padding: 4px 8px; font-size: 11.5px;">
              Ver
            </button>
            <button class="btn btn-outline btn-sm" onclick="window.switchToGraph('${m.id}')" style="padding: 4px 8px; font-size: 11.5px;" title="Ver no Grafo">
              <i class="fa-solid fa-circle-nodes"></i>
            </button>
          </td>
        </tr>
      `;
    }).join('');
  }

  // ==========================================
  // MODAL DE DETALHES
  // ==========================================
  window.openModal = async (movieId) => {
    currentModalMovieId = movieId;
    formMovieId.value = movieId;

    try {
      const res = await fetch(`/api/movies/${movieId}`);
      const data = await res.json();
      if (!data.success || !data.movie) return;

      const m = data.movie;
      modalProviderBadge.textContent = m.streaming_name;
      modalDateTag.textContent = m.release_display || m.release_date || '2026';
      modalMovieTitle.textContent = m.title;
      modalPoster.src = m.poster_url;
      modalSynopsisText.textContent = m.synopsis || 'Sem sinopse cadastrada.';
      modalDirector.textContent = m.director || 'Não informado';
      modalCast.textContent = (m.cast && m.cast.length > 0) ? m.cast.join(', ') : 'Não informado';
      modalRuntime.textContent = m.runtime || '105 min';

      const sc = m.scores || {};
      modalCinescoreVal.textContent = sc.cinescore !== undefined ? sc.cinescore : 70;
      modalRtVal.textContent = (sc.tomatometer !== null && sc.tomatometer !== undefined) ? `${sc.tomatometer}%` : 'N/D';
      modalPopcornVal.textContent = (sc.popcornmeter !== null && sc.popcornmeter !== undefined) ? `${sc.popcornmeter}%` : 'N/D';
      modalImdbVal.textContent = sc.imdb ? `${sc.imdb}/10` : 'N/D';
      modalCommVal.textContent = sc.tmdb ? `${sc.tmdb}/10` : 'N/D';

      modalTagsRow.innerHTML = (m.genres || ['Filme']).map(g => `<span class="genre-pill">${g}</span>`).join('');

      if (m.trailer_youtube_id) {
        modalTrailerBox.style.display = 'block';
        modalTrailerIframe.src = `https://www.youtube-nocookie.com/embed/${m.trailer_youtube_id}?rel=0`;
      } else {
        modalTrailerBox.style.display = 'none';
        modalTrailerIframe.src = '';
      }

      const reviews = m.reviews || [];
      modalReviewsCountPill.textContent = `${reviews.length} avaliaç${reviews.length !== 1 ? 'ões' : 'ão'}`;

      if (reviews.length === 0) {
        modalReviewsFeed.innerHTML = `<p style="font-size: 12.5px; color: var(--text-muted);">Nenhum comentário registrado ainda.</p>`;
      } else {
        modalReviewsFeed.innerHTML = reviews.map(r => `
          <div class="comment-card">
            <div class="comment-top">
              <span class="comment-author">${r.author} <small style="color: var(--blue-700); font-weight: normal;">(${r.source || 'Comunidade'})</small></span>
              <span class="comment-meta">Nota: <strong>${r.rating}/10</strong> • ${r.date || 'Recente'}</span>
            </div>
            <p class="comment-text">${r.text}</p>
          </div>
        `).join('');
      }

      movieModal.style.display = 'flex';
      document.body.style.overflow = 'hidden';
    } catch (err) {
      console.error('Error opening modal:', err);
    }
  };

  window.openTrailerDirect = (movieId) => {
    window.openModal(movieId);
    setTimeout(() => {
      document.getElementById('modal-trailer-box').scrollIntoView({ behavior: 'smooth' });
    }, 120);
  };

  function closeModal() {
    movieModal.style.display = 'none';
    modalTrailerIframe.src = '';
    document.body.style.overflow = 'auto';
  }

  // ==========================================
  // MOTOR DO GRAFO INTERATIVO DE RELAÇÕES
  // ==========================================
  window.switchToGraph = (movieId) => {
    switchViewTab('graph');
    if (movieId) {
      loadGraphForMovie(movieId);
    }
  };

  async function loadDefaultGraph() {
    graphLoader.style.display = 'flex';
    try {
      const minSim = graphMinSimSelect.value || 25;
      const limit = graphMaxNodesSelect.value || 12;
      const res = await fetch(`/api/graph/default?min_sim=${minSim}&limit=${limit}`);
      const data = await res.json();
      if (data.success && data.graph) {
        setupGraphData(data.graph);
      }
    } catch (err) {
      console.error('Default graph load error:', err);
    } finally {
      graphLoader.style.display = 'none';
    }
  }

  async function loadGraphForMovie(movieId) {
    graphLoader.style.display = 'flex';
    currentGraphMovieId = movieId;
    try {
      const minSim = graphMinSimSelect.value || 25;
      const limit = graphMaxNodesSelect.value || 12;
      const res = await fetch(`/api/movies/${movieId}/graph?min_sim=${minSim}&limit=${limit}`);
      const data = await res.json();
      if (data.success && data.graph) {
        setupGraphData(data.graph);
      }
    } catch (err) {
      console.error('Movie graph load error:', err);
    } finally {
      graphLoader.style.display = 'none';
    }
  }

  function setupGraphData(graphData) {
    const rawNodes = graphData.nodes || [];
    const rawLinks = graphData.links || [];
    const centerNode = rawNodes.find(n => n.is_center) || rawNodes[0];

    if (!centerNode) return;
    currentGraphMovieId = centerNode.id;

    // Posicionamento inicial inteligente
    const count = rawNodes.length;
    graphNodes = rawNodes.map((n, i) => {
      let x = 0;
      let y = 0;
      if (n.is_center) {
        x = 0;
        y = 0;
      } else {
        const angle = ((i - 1) / Math.max(1, count - 1)) * 2 * Math.PI;
        // Distância proporcional inversa à similaridade
        const dist = 180 + (100 - (n.similarity || 50)) * 1.5;
        x = Math.cos(angle) * dist + (Math.random() - 0.5) * 20;
        y = Math.sin(angle) * dist + (Math.random() - 0.5) * 20;
      }

      // Pré-carregar imagem do pôster
      if (n.poster_url && !posterImgCache[n.id]) {
        const img = new Image();
        img.src = n.poster_url;
        img.onload = () => { posterImgCache[n.id] = img; };
      }

      return {
        ...n,
        x,
        y,
        vx: 0,
        vy: 0,
        radius: n.is_center ? 38 : 26
      };
    });

    // Mapear links para referências de objetos de nó
    const nodeMap = new Map();
    graphNodes.forEach(n => nodeMap.set(n.id, n));

    graphLinks = rawLinks
      .map(l => ({
        ...l,
        sourceNode: nodeMap.get(l.source),
        targetNode: nodeMap.get(l.target)
      }))
      .filter(l => l.sourceNode && l.targetNode);

    // Selecionar o nó central no inspetor
    selectedTarget = graphNodes.find(n => n.is_center) || graphNodes[0];
    updateInspector(selectedTarget);

    // Reset de câmera
    camera = { x: 0, y: 0, scale: 1 };
    startSimulation();
  }

  function initCanvasSize() {
    if (!graphCanvas || !graphCanvasContainer) return;
    const rect = graphCanvasContainer.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    graphCanvas.width = rect.width * dpr;
    graphCanvas.height = rect.height * dpr;
    graphCanvas.style.width = `${rect.width}px`;
    graphCanvas.style.height = `${rect.height}px`;
  }

  window.addEventListener('resize', () => {
    if (isGraphViewActive) initCanvasSize();
  });

  // Física do Grafo
  function updatePhysics() {
    const kRepel = 7000;
    const centerAttract = 0.015;

    // Força das arestas (molas)
    for (const l of graphLinks) {
      const u = l.sourceNode;
      const v = l.targetNode;
      const dx = v.x - u.x;
      const dy = v.y - u.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const targetDist = 180 - (l.similarity * 0.7);
      const force = (dist - targetDist) * 0.035;

      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;

      if (!u.is_center && u !== dragTarget) {
        u.vx += fx;
        u.vy += fy;
      }
      if (!v.is_center && v !== dragTarget) {
        v.vx -= fx;
        v.vy -= fy;
      }
    }

    // Repulsão mútua entre nós
    for (let i = 0; i < graphNodes.length; i++) {
      for (let j = i + 1; j < graphNodes.length; j++) {
        const a = graphNodes[i];
        const b = graphNodes[j];
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const distSq = dx * dx + dy * dy + 100;
        const dist = Math.sqrt(distSq);
        const repForce = kRepel / distSq;

        const rfx = (dx / dist) * repForce;
        const rfy = (dy / dist) * repForce;

        if (!a.is_center && a !== dragTarget) {
          a.vx -= rfx;
          a.vy -= rfy;
        }
        if (!b.is_center && b !== dragTarget) {
          b.vx += rfx;
          b.vy += rfy;
        }
      }
    }

    // Gravidade central leve
    for (const n of graphNodes) {
      if (n.is_center) {
        n.vx -= n.x * 0.1;
        n.vy -= n.y * 0.1;
      } else if (n !== dragTarget) {
        n.vx -= n.x * centerAttract;
        n.vy -= n.y * centerAttract;
      }

      // Amortecimento
      n.vx *= 0.85;
      n.vy *= 0.85;

      n.x += n.vx;
      n.y += n.vy;
    }
  }

  // Renderização do Canvas
  function drawGraph() {
    if (!graphCanvas) return;
    const ctx = graphCanvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;

    ctx.save();
    ctx.clearRect(0, 0, graphCanvas.width, graphCanvas.height);

    // Sistema de coordenadas centrado e escalado
    ctx.scale(dpr, dpr);
    const w = graphCanvas.width / dpr;
    const h = graphCanvas.height / dpr;

    ctx.translate(w / 2 + camera.x, h / 2 + camera.y);
    ctx.scale(camera.scale, camera.scale);

    // Círculos radiais de fundo discretos
    ctx.strokeStyle = '#f1f5f9';
    ctx.lineWidth = 1;
    [150, 250, 350].forEach(rad => {
      ctx.beginPath();
      ctx.arc(0, 0, rad, 0, 2 * Math.PI);
      ctx.stroke();
    });

    // 1. Desenhar Linhas (Conexões)
    for (const l of graphLinks) {
      const u = l.sourceNode;
      const v = l.targetNode;

      const isHighlight = (hoveredTarget && (hoveredTarget.id === u.id || hoveredTarget.id === v.id)) ||
                          (selectedTarget && (selectedTarget.id === u.id || selectedTarget.id === v.id));

      ctx.beginPath();
      ctx.moveTo(u.x, u.y);
      ctx.lineTo(v.x, v.y);

      if (isHighlight) {
        ctx.strokeStyle = '#1d4ed8';
        ctx.lineWidth = 2.8;
      } else {
        const alpha = Math.min(0.75, Math.max(0.18, l.similarity / 110));
        ctx.strokeStyle = `rgba(37, 99, 235, ${alpha})`;
        ctx.lineWidth = Math.max(1.2, (l.similarity / 100) * 3);
      }
      ctx.stroke();

      // Badge de Porcentagem no Meio da Linha
      const midX = (u.x + v.x) / 2;
      const midY = (u.y + v.y) / 2;

      ctx.fillStyle = '#ffffff';
      ctx.strokeStyle = isHighlight ? '#1d4ed8' : '#bfdbfe';
      ctx.lineWidth = 1;
      const txt = `${l.similarity}%`;
      ctx.font = 'bold 9.5px system-ui, sans-serif';
      const tw = ctx.measureText(txt).width;

      ctx.beginPath();
      ctx.roundRect(midX - tw / 2 - 4, midY - 7, tw + 8, 14, 3);
      ctx.fill();
      ctx.stroke();

      ctx.fillStyle = isHighlight ? '#1d4ed8' : '#1e3a8a';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(txt, midX, midY);
    }

    // 2. Desenhar Nós
    for (const n of graphNodes) {
      const isSelected = selectedTarget && selectedTarget.id === n.id;
      const isHovered = hoveredTarget && hoveredTarget.id === n.id;

      ctx.save();
      ctx.translate(n.x, n.y);

      // Anel externo de seleção/hover
      if (isSelected || isHovered) {
        ctx.beginPath();
        ctx.arc(0, 0, n.radius + 6, 0, 2 * Math.PI);
        ctx.strokeStyle = 'rgba(29, 78, 216, 0.4)';
        ctx.lineWidth = 3;
        ctx.stroke();
      }

      // Corpo do Nó
      ctx.beginPath();
      ctx.arc(0, 0, n.radius, 0, 2 * Math.PI);

      if (n.is_center) {
        ctx.fillStyle = '#1e3a8a';
        ctx.fill();
        ctx.strokeStyle = '#bfdbfe';
        ctx.lineWidth = 3;
        ctx.stroke();
      } else {
        ctx.fillStyle = '#ffffff';
        ctx.fill();
        ctx.strokeStyle = isSelected ? '#1d4ed8' : '#2563eb';
        ctx.lineWidth = isSelected ? 3 : 2;
        ctx.stroke();
      }

      // Foto recortada se disponível, senão ícone
      const img = posterImgCache[n.id];
      if (img && img.complete) {
        ctx.save();
        ctx.beginPath();
        ctx.arc(0, 0, n.radius - 2, 0, 2 * Math.PI);
        ctx.clip();
        ctx.drawImage(img, -n.radius, -n.radius, n.radius * 2, n.radius * 2);
        ctx.restore();
      } else {
        // Fallback de Ícone
        ctx.fillStyle = n.is_center ? '#ffffff' : '#1d4ed8';
        ctx.font = 'bold 13px system-ui, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(n.is_center ? '★' : '🎬', 0, 0);
      }

      // Mini Badge do CineScore no Nó
      const cs = n.cinescore || 70;
      ctx.beginPath();
      ctx.arc(n.radius - 4, -n.radius + 4, 10, 0, 2 * Math.PI);
      ctx.fillStyle = '#1d4ed8';
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.5;
      ctx.stroke();

      ctx.fillStyle = '#ffffff';
      ctx.font = 'bold 8.5px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(`${cs}`, n.radius - 4, -n.radius + 4);

      // Rótulos Textuais do Filme Abaixo do Nó
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';

      // Título
      ctx.fillStyle = '#0f172a';
      ctx.font = n.is_center ? 'bold 12px system-ui, sans-serif' : 'bold 11px system-ui, sans-serif';
      const truncatedTitle = n.title.length > 20 ? n.title.slice(0, 19) + '…' : n.title;
      ctx.fillText(truncatedTitle, 0, n.radius + 5);

      // Sub-rótulo: Gênero / Streaming
      ctx.fillStyle = '#1d4ed8';
      ctx.font = '10px system-ui, sans-serif';
      const subInfo = n.is_center ? `${n.streaming_name} • Foco` : `${n.genres ? n.genres[0] : 'Filme'} • ${n.streaming_name}`;
      ctx.fillText(subInfo, 0, n.radius + 20);

      ctx.restore();
    }

    ctx.restore();
  }

  function startSimulation() {
    if (simAnimId) cancelAnimationFrame(simAnimId);
    let frames = 0;
    function loop() {
      if (!isGraphViewActive) return;
      updatePhysics();
      drawGraph();
      frames++;
      // A simulação relaxa naturalmente após ~200 frames se não estiver interagindo
      if (frames < 300 || isDraggingNode || isPanningCanvas) {
        simAnimId = requestAnimationFrame(loop);
      } else {
        // Redesenhar estático
        drawGraph();
      }
    }
    simAnimId = requestAnimationFrame(loop);
  }

  function resumeSimulation() {
    startSimulation();
  }

  // Interatividade com Mouse no Canvas
  function getCanvasCoords(e) {
    const rect = graphCanvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const dpr = window.devicePixelRatio || 1;
    const w = graphCanvas.width / dpr;
    const h = graphCanvas.height / dpr;

    // Converter para espaço centralizado/escalado
    const worldX = (x - (w / 2 + camera.x)) / camera.scale;
    const worldY = (y - (h / 2 + camera.y)) / camera.scale;
    return { x: worldX, y: worldY };
  }

  function findNodeAt(coords) {
    for (let i = graphNodes.length - 1; i >= 0; i--) {
      const n = graphNodes[i];
      const dx = coords.x - n.x;
      const dy = coords.y - n.y;
      if (dx * dx + dy * dy <= (n.radius + 8) * (n.radius + 8)) {
        return n;
      }
    }
    return null;
  }

  graphCanvas.addEventListener('mousedown', (e) => {
    const coords = getCanvasCoords(e);
    const clickedNode = findNodeAt(coords);

    if (clickedNode) {
      isDraggingNode = true;
      dragTarget = clickedNode;
      selectedTarget = clickedNode;
      updateInspector(clickedNode);
      resumeSimulation();
    } else {
      isPanningCanvas = true;
      panOrigin = { x: e.clientX - camera.x, y: e.clientY - camera.y };
    }
  });

  window.addEventListener('mousemove', (e) => {
    if (isDraggingNode && dragTarget) {
      const coords = getCanvasCoords(e);
      dragTarget.x = coords.x;
      dragTarget.y = coords.y;
      dragTarget.vx = 0;
      dragTarget.vy = 0;
      resumeSimulation();
    } else if (isPanningCanvas) {
      camera.x = e.clientX - panOrigin.x;
      camera.y = e.clientY - panOrigin.y;
      resumeSimulation();
    } else if (isGraphViewActive) {
      const coords = getCanvasCoords(e);
      const hit = findNodeAt(coords);
      if (hit !== hoveredTarget) {
        hoveredTarget = hit;
        graphCanvas.style.cursor = hit ? 'pointer' : 'grab';
        resumeSimulation();
      }
    }
  });

  window.addEventListener('mouseup', () => {
    isDraggingNode = false;
    dragTarget = null;
    isPanningCanvas = false;
  });

  // Duplo clique: Pivô / Tornar novo centro
  graphCanvas.addEventListener('dblclick', (e) => {
    const coords = getCanvasCoords(e);
    const node = findNodeAt(coords);
    if (node && !node.is_center) {
      loadGraphForMovie(node.id);
    }
  });

  // Zoom pelo scroll
  graphCanvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    const zoomFactor = e.deltaY < 0 ? 1.1 : 0.9;
    camera.scale = Math.min(2.5, Math.max(0.4, camera.scale * zoomFactor));
    resumeSimulation();
  }, { passive: false });

  // Botão Centralizar
  btnRecenterGraph.addEventListener('click', () => {
    camera = { x: 0, y: 0, scale: 1 };
    resumeSimulation();
  });

  // Filtros de Afinidade e Conexões
  graphMinSimSelect.addEventListener('change', () => {
    if (currentGraphMovieId) loadGraphForMovie(currentGraphMovieId);
  });

  graphMaxNodesSelect.addEventListener('change', () => {
    if (currentGraphMovieId) loadGraphForMovie(currentGraphMovieId);
  });

  // Busca e Dropdown no Grafo
  graphMovieSearch.addEventListener('input', () => {
    const query = graphMovieSearch.value.trim().toLowerCase();
    if (!query) {
      graphSearchDropdown.style.display = 'none';
      return;
    }

    const matches = allCatalogMovies.filter(m =>
      m.title.toLowerCase().includes(query) ||
      (m.genres && m.genres.some(g => g.toLowerCase().includes(query)))
    ).slice(0, 7);

    if (matches.length === 0) {
      graphSearchDropdown.innerHTML = `<div class="dropdown-item" style="color: var(--text-muted);">Nenhum título encontrado.</div>`;
    } else {
      graphSearchDropdown.innerHTML = matches.map(m => `
        <div class="dropdown-item" data-id="${m.id}">
          <span><strong>${m.title}</strong> <small style="color: var(--text-muted);">(${m.streaming_name})</small></span>
          <span class="service-pill-tag" style="font-size: 10px;">${m.genres ? m.genres[0] : 'Filme'}</span>
        </div>
      `).join('');
    }
    graphSearchDropdown.style.display = 'block';
  });

  graphSearchDropdown.addEventListener('click', (e) => {
    const item = e.target.closest('.dropdown-item');
    if (!item || !item.dataset.id) return;
    const movieId = item.dataset.id;
    graphMovieSearch.value = '';
    graphSearchDropdown.style.display = 'none';
    loadGraphForMovie(movieId);
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.graph-search-box')) {
      graphSearchDropdown.style.display = 'none';
    }
  });

  // ==========================================
  // PAINEL INSPETOR DE RELAÇÕES E ROTEIRO
  // ==========================================
  function updateInspector(node) {
    if (!node) return;

    inspTag.textContent = node.is_center ? 'NÓ CENTRAL (FOCO)' : 'FILME RELACIONADO';
    inspTitle.textContent = node.title;

    const writersList = (node.screenwriters && node.screenwriters.length > 0)
      ? node.screenwriters.join(', ')
      : 'Não creditado nas APIs';

    const keywordsBadges = (node.keywords && node.keywords.length > 0)
      ? node.keywords.map(k => `<span class="insp-pill insp-pill-blue"><i class="fa-solid fa-tag"></i> ${k}</span>`).join('')
      : '<span class="insp-pill">Enredo geral</span>';

    const genresBadges = (node.genres && node.genres.length > 0)
      ? node.genres.map(g => `<span class="insp-pill">${g}</span>`).join('')
      : '<span class="insp-pill">Filme</span>';

    // Se for nó vizinho, gerar bloco de motivos da conexão
    let connectionBlock = '';
    if (!node.is_center) {
      const sim = node.similarity || 50;
      const reasons = node.reasons || ['Afinidade temática de catálogo'];

      connectionBlock = `
        <div class="sim-progress-box">
          <div class="sim-bar-label-row">
            <span>Afinidade com Nó Central:</span>
            <strong>${sim}% de Similaridade</strong>
          </div>
          <div class="sim-bar-track">
            <div class="sim-bar-fill" style="width: ${sim}%;"></div>
          </div>
          <ul class="insp-reasons-list">
            ${reasons.map(r => `<li><i class="fa-solid fa-circle-check"></i> ${r}</li>`).join('')}
          </ul>
        </div>
      `;
    }

    inspBody.innerHTML = `
      <div class="insp-movie-top">
        <img class="insp-poster" src="${node.poster_url}" alt="${node.title}" onerror="this.src='https://placehold.co/200x300/eff6ff/1e3a8a?text=Foto'">
        <div class="insp-top-details">
          <span class="service-pill-tag" style="width: fit-content;">${node.streaming_name}</span>
          <span style="font-size: 11px; color: var(--text-light);">${node.year || '2026'}</span>
          <div class="insp-cinescore-pill">
            <i class="fa-solid fa-award"></i> CineScore: <strong>${node.cinescore}</strong>
          </div>
        </div>
      </div>

      ${connectionBlock}

      <!-- Bloco de Gêneros -->
      <div class="insp-section-block">
        <span class="insp-section-title"><i class="fa-solid fa-masks-theater"></i> Gêneros do Filme</span>
        <div class="insp-badges-wrap">
          ${genresBadges}
        </div>
      </div>

      <!-- Bloco de Roteiro e Narrativa -->
      <div class="insp-section-block">
        <span class="insp-section-title"><i class="fa-solid fa-pen-nib"></i> Roteiro & Narrativa</span>
        <div style="font-size: 12px; color: var(--text-body); margin-bottom: 6px;">
          <strong>Roteirista(s):</strong> ${writersList}
        </div>
        <div style="font-size: 12px; color: var(--text-body); margin-bottom: 6px;">
          <strong>Direção:</strong> ${node.director || 'Não informada'}
        </div>
        <div style="margin-top: 8px;">
          <span style="font-size: 11px; font-weight: 700; color: var(--text-muted); display: block; margin-bottom: 4px;">TEMAS DO ENREDO (KEYWORDS):</span>
          <div class="insp-badges-wrap">
            ${keywordsBadges}
          </div>
        </div>
      </div>

      <!-- Sinopse -->
      <div class="insp-section-block">
        <span class="insp-section-title"><i class="fa-solid fa-align-left"></i> Sinopse do Enredo</span>
        <p style="font-size: 12px; color: var(--text-body); line-height: 1.5;">
          ${node.synopsis || 'Sem sinopse cadastrada.'}
        </p>
      </div>

      <!-- Ações do Inspetor -->
      <div class="insp-actions-row">
        ${!node.is_center ? `
          <button class="btn btn-primary btn-sm" id="btn-insp-recenter">
            <i class="fa-solid fa-crosshairs"></i> Centralizar este Filme no Grafo
          </button>
        ` : ''}
        <button class="btn btn-outline btn-sm" id="btn-insp-details">
          <i class="fa-solid fa-circle-info"></i> Abrir Ficha Completa & Trailer
        </button>
      </div>
    `;

    // Listeners dos botões de ação do inspetor
    const btnInspRecenter = document.getElementById('btn-insp-recenter');
    if (btnInspRecenter) {
      btnInspRecenter.addEventListener('click', () => {
        loadGraphForMovie(node.id);
      });
    }

    const btnInspDetails = document.getElementById('btn-insp-details');
    if (btnInspDetails) {
      btnInspDetails.addEventListener('click', () => {
        window.openModal(node.id);
      });
    }
  }

  // ==========================================
  // CINESWIPE - TINDER DE FILMES NOS 4 EIXOS
  // ==========================================
  const cineswipeSection = document.getElementById('cineswipe-view-section');
  const cineswipeDeck = document.getElementById('cineswipe-deck');
  const cineswipeEmpty = document.getElementById('cineswipe-empty');
  const cineswipeServiceFilter = document.getElementById('cineswipe-service-filter');
  const btnOpenWatchlist = document.getElementById('btn-open-watchlist');
  const watchlistCount = document.getElementById('watchlist-count');
  const watchlistModal = document.getElementById('watchlist-modal');
  const watchlistCloseBtn = document.getElementById('watchlist-close-btn');
  const watchlistItemsFeed = document.getElementById('watchlist-items-feed');
  const btnClearWatchlist = document.getElementById('btn-clear-watchlist');
  const btnResetCineswipe = document.getElementById('btn-reset-cineswipe');

  const btnSwipeDislike = document.getElementById('btn-swipe-dislike');
  const btnSwipeSkip = document.getElementById('btn-swipe-skip');
  const btnSwipeWatchlist = document.getElementById('btn-swipe-watchlist');
  const btnSwipeLike = document.getElementById('btn-swipe-like');

  let cineswipeQueue = [];
  let isCineswipeActive = false;
  let activeCardEl = null;

  // Persistência no LocalStorage
  let userHistory = JSON.parse(localStorage.getItem('cineswipe_history') || '{"liked":[],"disliked":[],"watchlist":[],"skipped":[]}');
  let userWatchlist = JSON.parse(localStorage.getItem('cineswipe_watchlist') || '[]');

  updateWatchlistBadge();

  function updateWatchlistBadge() {
    if (watchlistCount) {
      watchlistCount.textContent = userWatchlist.length;
    }
  }

  function saveHistory() {
    localStorage.setItem('cineswipe_history', JSON.stringify(userHistory));
    localStorage.setItem('cineswipe_watchlist', JSON.stringify(userWatchlist));
    updateWatchlistBadge();
  }

  function initCineswipeDeck() {
    const selectedService = cineswipeServiceFilter ? cineswipeServiceFilter.value : 'all';
    
    // Filtrar filmes por serviço e remover os já avaliados
    const evaluatedIds = new Set([
      ...userHistory.liked,
      ...userHistory.disliked,
      ...userHistory.watchlist,
      ...userHistory.skipped
    ]);

    let candidates = allCatalogMovies.filter(m => {
      if (selectedService !== 'all' && m.streaming_service !== selectedService) return false;
      return !evaluatedIds.has(m.id);
    });

    if (candidates.length === 0) {
      candidates = allCatalogMovies.filter(m => {
        if (selectedService !== 'all' && m.streaming_service !== selectedService) return false;
        return true;
      });
    }

    // Reordenação dinâmica baseada no gosto prévio (Watchlist & Liked)
    if (userWatchlist.length > 0 || userHistory.liked.length > 0) {
      const preferredGenres = new Set();
      userWatchlist.forEach(item => {
        (item.genres || []).forEach(g => preferredGenres.add(g));
      });

      candidates.sort((a, b) => {
        let scoreA = a.scores?.cinescore || 70;
        let scoreB = b.scores?.cinescore || 70;
        (a.genres || []).forEach(g => { if (preferredGenres.has(g)) scoreA += 15; });
        (b.genres || []).forEach(g => { if (preferredGenres.has(g)) scoreB += 15; });
        return scoreB - scoreA;
      });
    }

    cineswipeQueue = candidates.slice(0, 35);
    renderSwipeDeck();
  }

  function renderSwipeDeck() {
    if (!cineswipeDeck) return;
    cineswipeDeck.querySelectorAll('.cineswipe-card').forEach(el => el.remove());

    if (cineswipeQueue.length === 0) {
      if (cineswipeEmpty) cineswipeEmpty.style.display = 'flex';
      return;
    }
    if (cineswipeEmpty) cineswipeEmpty.style.display = 'none';

    const cardsToRender = cineswipeQueue.slice(0, 2);
    for (let i = cardsToRender.length - 1; i >= 0; i--) {
      const movie = cardsToRender[i];
      const isTop = (i === 0);
      const cardEl = createCardElement(movie, isTop);
      cineswipeDeck.appendChild(cardEl);
      if (isTop) {
        activeCardEl = cardEl;
        attachDragEvents(cardEl);
      }
    }
  }

  function createCardElement(m, isTop) {
    const card = document.createElement('div');
    card.className = `cineswipe-card ${isTop ? 'card-active' : 'card-next'}`;
    card.dataset.id = m.id;

    const genresPills = (m.genres || ['Filme']).slice(0, 3).map(g => `<span class="insp-pill">${g}</span>`).join('');
    const director = m.director || 'Direção não informada';
    const writers = (m.screenwriters && m.screenwriters.length > 0) ? m.screenwriters.join(', ') : null;

    card.innerHTML = `
      <div class="cineswipe-media-wrap">
        <img class="cineswipe-poster-img" src="${m.poster_url}" alt="${m.title}" onerror="this.src='https://placehold.co/400x600/eff6ff/1e3a8a?text=Foto'">
        <div class="cineswipe-media-overlay">
          <div class="cineswipe-top-tags">
            <span class="cineswipe-badge-platform">${m.streaming_name}</span>
            <span class="cineswipe-cinescore-tag"><i class="fa-solid fa-award"></i> ${m.scores?.cinescore || 70}</span>
          </div>
          <div class="cineswipe-media-bottom">
            <h2>${m.title}</h2>
            <div class="cineswipe-sub-line">${m.year || '2026'} • ${m.runtime || '105 min'}</div>
          </div>
        </div>
        <div class="swipe-stamp stamp-like">GOSTEI!</div>
        <div class="swipe-stamp stamp-dislike">NÃO GOSTEI</div>
        <div class="swipe-stamp stamp-watchlist">QUERO VER! 🍿</div>
        <div class="swipe-stamp stamp-skip">NÃO ASSISTIRIA</div>
      </div>

      <div class="cineswipe-card-content">
        <div class="cineswipe-genres-row">
          ${genresPills}
        </div>
        <p class="cineswipe-synopsis">${m.synopsis || 'Sem sinopse cadastrada.'}</p>
        <div class="cineswipe-crew-info">
          <span><strong>Direção:</strong> ${director}</span>
          ${writers ? `<br><span><strong>Roteiro:</strong> ${writers}</span>` : ''}
        </div>
      </div>
    `;

    return card;
  }

  function attachDragEvents(card) {
    let startX = 0, startY = 0, currentX = 0, currentY = 0;
    let isDragging = false;

    const stampLike = card.querySelector('.stamp-like');
    const stampDislike = card.querySelector('.stamp-dislike');
    const stampWatchlist = card.querySelector('.stamp-watchlist');
    const stampSkip = card.querySelector('.stamp-skip');

    const onPointerDown = (e) => {
      isDragging = true;
      startX = e.clientX || (e.touches && e.touches[0].clientX);
      startY = e.clientY || (e.touches && e.touches[0].clientY);
      card.style.transition = 'none';
    };

    const onPointerMove = (e) => {
      if (!isDragging) return;
      currentX = e.clientX || (e.touches && e.touches[0].clientX);
      currentY = e.clientY || (e.touches && e.touches[0].clientY);
      const dx = currentX - startX;
      const dy = currentY - startY;

      const rot = dx * 0.06;
      card.style.transform = `translate(${dx}px, ${dy}px) rotate(${rot}deg)`;

      const absX = Math.abs(dx);
      const absY = Math.abs(dy);

      if (absX > absY) {
        if (dx > 30) {
          if (stampLike) stampLike.style.opacity = Math.min(1, (dx - 30) / 90);
          if (stampDislike) stampDislike.style.opacity = 0;
        } else if (dx < -30) {
          if (stampDislike) stampDislike.style.opacity = Math.min(1, (-dx - 30) / 90);
          if (stampLike) stampLike.style.opacity = 0;
        }
        if (stampWatchlist) stampWatchlist.style.opacity = 0;
        if (stampSkip) stampSkip.style.opacity = 0;
      } else {
        if (dy < -30) {
          if (stampWatchlist) stampWatchlist.style.opacity = Math.min(1, (-dy - 30) / 90);
          if (stampSkip) stampSkip.style.opacity = 0;
        } else if (dy > 30) {
          if (stampSkip) stampSkip.style.opacity = Math.min(1, (dy - 30) / 90);
          if (stampWatchlist) stampWatchlist.style.opacity = 0;
        }
        if (stampLike) stampLike.style.opacity = 0;
        if (stampDislike) stampDislike.style.opacity = 0;
      }
    };

    const onPointerUp = () => {
      if (!isDragging) return;
      isDragging = false;
      const dx = currentX - startX;
      const dy = currentY - startY;
      const absX = Math.abs(dx);
      const absY = Math.abs(dy);

      card.style.transition = 'transform 0.3s ease, opacity 0.3s ease';

      if (absX > absY && absX > 75) {
        if (dx > 0) executeSwipe('right', card);
        else executeSwipe('left', card);
      } else if (absY > absX && absY > 75) {
        if (dy < 0) executeSwipe('up', card);
        else executeSwipe('down', card);
      } else {
        card.style.transform = 'translate(0, 0) rotate(0deg)';
        if (stampLike) stampLike.style.opacity = 0;
        if (stampDislike) stampDislike.style.opacity = 0;
        if (stampWatchlist) stampWatchlist.style.opacity = 0;
        if (stampSkip) stampSkip.style.opacity = 0;
      }
    };

    card.addEventListener('mousedown', onPointerDown);
    window.addEventListener('mousemove', onPointerMove);
    window.addEventListener('mouseup', onPointerUp);

    card.addEventListener('touchstart', onPointerDown, { passive: true });
    window.addEventListener('touchmove', onPointerMove, { passive: true });
    window.addEventListener('touchend', onPointerUp);
  }

  function executeSwipe(direction, cardEl) {
    if (!cardEl || cineswipeQueue.length === 0) return;
    const movie = cineswipeQueue[0];

    cardEl.style.transition = 'transform 0.35s ease, opacity 0.35s ease';
    if (direction === 'right') {
      cardEl.style.transform = 'translate(140%, 0) rotate(25deg)';
      userHistory.liked.push(movie.id);
      showToast(`Gostei: "${movie.title}" registrado!`);
    } else if (direction === 'left') {
      cardEl.style.transform = 'translate(-140%, 0) rotate(-25deg)';
      userHistory.disliked.push(movie.id);
      showToast(`Não Gostei: "${movie.title}"`);
    } else if (direction === 'up') {
      cardEl.style.transform = 'translate(0, -140%) scale(0.9)';
      userHistory.watchlist.push(movie.id);
      if (!userWatchlist.some(w => w.id === movie.id)) {
        userWatchlist.unshift(movie);
      }
      showToast(`🍿 Salvo na Watchlist: "${movie.title}"`);
    } else if (direction === 'down') {
      cardEl.style.transform = 'translate(0, 140%) scale(0.9)';
      userHistory.skipped.push(movie.id);
      showToast(`Não assistiria: "${movie.title}"`);
    }

    cardEl.style.opacity = '0';
    saveHistory();

    setTimeout(() => {
      cineswipeQueue.shift();
      renderSwipeDeck();
    }, 280);
  }

  if (btnSwipeLike) {
    btnSwipeLike.addEventListener('click', () => {
      if (activeCardEl) executeSwipe('right', activeCardEl);
    });
  }
  if (btnSwipeDislike) {
    btnSwipeDislike.addEventListener('click', () => {
      if (activeCardEl) executeSwipe('left', activeCardEl);
    });
  }
  if (btnSwipeWatchlist) {
    btnSwipeWatchlist.addEventListener('click', () => {
      if (activeCardEl) executeSwipe('up', activeCardEl);
    });
  }
  if (btnSwipeSkip) {
    btnSwipeSkip.addEventListener('click', () => {
      if (activeCardEl) executeSwipe('down', activeCardEl);
    });
  }

  document.addEventListener('keydown', (e) => {
    if (!isCineswipeActive || !activeCardEl || movieModal.style.display === 'flex' || watchlistModal.style.display === 'flex') return;
    if (e.key === 'ArrowRight') {
      e.preventDefault();
      executeSwipe('right', activeCardEl);
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      executeSwipe('left', activeCardEl);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      executeSwipe('up', activeCardEl);
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      executeSwipe('down', activeCardEl);
    }
  });

  if (cineswipeServiceFilter) {
    cineswipeServiceFilter.addEventListener('change', () => {
      initCineswipeDeck();
    });
  }

  if (btnResetCineswipe) {
    btnResetCineswipe.addEventListener('click', () => {
      userHistory = { liked: [], disliked: [], watchlist: [], skipped: [] };
      saveHistory();
      initCineswipeDeck();
      showToast('Pilha de filmes reiniciada!');
    });
  }

  if (btnOpenWatchlist) {
    btnOpenWatchlist.addEventListener('click', () => {
      renderWatchlistFeed();
      watchlistModal.style.display = 'flex';
      document.body.style.overflow = 'hidden';
    });
  }

  if (watchlistCloseBtn) {
    watchlistCloseBtn.addEventListener('click', () => {
      watchlistModal.style.display = 'none';
      document.body.style.overflow = 'auto';
    });
  }

  watchlistModal.addEventListener('click', (e) => {
    if (e.target === watchlistModal) {
      watchlistModal.style.display = 'none';
      document.body.style.overflow = 'auto';
    }
  });

  if (btnClearWatchlist) {
    btnClearWatchlist.addEventListener('click', () => {
      if (confirm('Deseja limpar todos os itens da sua Watchlist?')) {
        userWatchlist = [];
        saveHistory();
        renderWatchlistFeed();
      }
    });
  }

  function renderWatchlistFeed() {
    if (!watchlistItemsFeed) return;
    if (userWatchlist.length === 0) {
      watchlistItemsFeed.innerHTML = `
        <div style="text-align: center; padding: 30px; color: var(--text-muted); font-size: 13px;">
          <i class="fa-regular fa-bookmark" style="font-size: 28px; margin-bottom: 8px; display: block; color: var(--blue-400);"></i>
          Sua lista está vazia. Dê swipe para cima (ou clique em "Quero Ver!") no CineSwipe para salvar filmes aqui!
        </div>
      `;
      return;
    }

    watchlistItemsFeed.innerHTML = userWatchlist.map(m => `
      <div class="watchlist-item-card">
        <img class="watchlist-poster-thumb" src="${m.poster_url}" alt="${m.title}">
        <div style="flex: 1; min-width: 0;">
          <strong style="font-size: 13px; color: var(--text-black); display: block;">${m.title}</strong>
          <span style="font-size: 11.5px; color: var(--text-muted);">${m.streaming_name} • ${m.year || '2026'} • CineScore: ${m.scores?.cinescore || 70}</span>
          <div style="font-size: 11px; color: var(--blue-700); margin-top: 2px;">
            ${m.genres ? m.genres.slice(0, 2).join(' • ') : 'Filme'}
          </div>
        </div>
        <div style="display: flex; gap: 6px;">
          <button class="btn btn-primary btn-sm" onclick="window.openModal('${m.id}')" style="font-size: 11px; padding: 4px 8px;">
            Ficha
          </button>
          <button class="btn btn-outline btn-sm" onclick="window.removeFromWatchlist('${m.id}')" style="font-size: 11px; padding: 4px 8px;" title="Remover da Lista">
            <i class="fa-solid fa-trash"></i>
          </button>
        </div>
      </div>
    `).join('');
  }

  window.removeFromWatchlist = (mid) => {
    userWatchlist = userWatchlist.filter(w => w.id !== mid);
    saveHistory();
    renderWatchlistFeed();
    showToast('Filme removido da Watchlist.');
  };

  // Notificações Toast
  function showToast(msg) {
    const toast = document.createElement('div');
    toast.className = 'clean-toast';
    toast.textContent = msg;
    toastStack.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transition = 'opacity 0.2s ease';
      setTimeout(() => toast.remove(), 200);
    }, 3200);
  }
});
