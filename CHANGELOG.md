# CineRank Streaming — Changelog do Projeto

Todas as alterações notáveis, implementações de funcionalidades e decisões de arquitetura deste projeto estão documentadas neste arquivo.

---

## [4.0.0] - 2026-10-12
Catálogo completo desde 2020 a partir dos datasets do IMDb, sem APIs pagas nem chaves.

### Dados
- **Ingestão em bulk** (`ingest/imdb.py`): baixa os datasets públicos do IMDb e carrega 138 mil filmes desde 2020, com notas, votos, direção, roteiro, elenco principal e títulos em português, em ~4 minutos.
- **Banco publicado** (`ingest/export.py` → `data/catalog.db`): ≈30 mil filmes com 100+ votos, SQLite somente leitura (26 MB), aberto em modo imutável no site.
- **CineScore recalibrado para escala:** prior bayesiano de 10.000 votos e média do catálogo como referência. Com o prior anterior (100 votos), títulos com poucos milhares de votos e nota ~9,9 lideravam.
- Enriquecimento via TMDB (`ingest/tmdb.py`) preparado como opcional; não está ligado ao site.
- Removidos `movie_api.py` e `data/movies.json` (coleta de lançamentos via TMDB/OMDb). CineScore extraído para `scoring.py`.

### Site
- API paginada em SQL (`/api/movies` com `year`, `genre`, `search`, `sort`, `min_votes`, `page`), `/api/facets`; busca por título, diretor ou ator.
- Interface só de texto: sem pôsteres e sem plataformas de streaming. Tabela com duração, nota IMDb, votos e CineScore; "Mostrar mais" para paginar.
- Ficha com posição no ranking, direção, roteiro, elenco e links para o IMDb e para a busca do trailer.
- Grafo por gênero, direção, roteiro e elenco, com o CineScore dentro de cada nó.
- Descobrir sobre os 500 mais bem ranqueados com 1.000+ votos, filtrável por gênero. Dados locais migrados para chaves `cinerank.v2.*`.
- Gêneros traduzidos para o português; metodologia e atribuição do IMDb atualizadas.
- Rotas `/api/sync` e `/api/providers` removidas.

---

## [3.0.0] - 2026-10-12
Revisão de credibilidade, segurança e design.

### Dados e metodologia
- **CineScore bayesiano:** média ponderada das fontes disponíveis (RT 30%, IMDb 30%, Metacritic 20%, TMDB 20%) ajustada pelo volume de votos (`m = 100`, `C = 60`). Antes, um título com 1 voto 10/10 liderava o ranking.
- **Fim dos dados inventados:** removidos o "Popcornmeter" (era a nota do TMDB × 10), o fallback que copiava a nota do TMDB para o campo IMDb, as ~100 resenhas geradas automaticamente ("Lançamento adicionado ao catálogo…"), `critics_count`/`likes` fixos e os valores padrão de duração, nota e diretor.
- Datas em português ("3 de setembro de 2026").
- Similaridade do grafo sem piso de 15% e sem bônus por mesma plataforma.
- `data/movies.json` migrado para o novo schema (`scores.tmdb_votes`, `scores.votes`, `scores.raw_score`, `scores.low_confidence`).

### Segurança
- Chaves de API lidas de variáveis de ambiente (`TMDB_API_KEY`, `OMDB_API_KEY`); a chave antes versionada deve ser revogada.
- `POST /api/sync` exige o header `X-Sync-Token`; `POST /api/config` e `POST /api/movies/<id>/reviews` removidos (sem autenticação e incompatíveis com o filesystem somente leitura da Vercel).
- Todo conteúdo da API é escapado antes de entrar no DOM (havia XSS armazenado via resenhas).
- Cabeçalhos `X-Content-Type-Options` e `Referrer-Policy`; cache de 1 dia para arquivos estáticos versionados.

### Interface
- Redesign editorial: neutros com um único acento, Source Serif 4 nos títulos, Inter no texto, tema escuro automático.
- Ranking em lista como visão padrão, com notas por fonte, contagem de votos e sinalização de "poucos votos"; filtro "Somente com 100+ votos".
- Ficha com tabela de fontes, ficha técnica, trailer, críticas do TMDB e links para TMDB/IMDb.
- Metodologia pública acessível em todas as páginas.
- "CineSwipe" renomeado para **Descobrir**, sem emojis; fila reordenada pelo perfil de gêneros; arraste via Pointer Events (corrige vazamento de listeners).
- Grafo com cores do tema, enquadramento automático e rótulos menos poluídos.
- Diálogos nativos (`<dialog>`) com Esc e foco; navegação por hash (`#conexoes`, `#descobrir`); cabeçalho responsivo.

### Código
- CSS extraído do template para `static/css/style.css` (antes duplicado e não carregado).
- `scraper.py` removido (não utilizado; dependia de `bs4` fora do `requirements.txt`).

---

## [2.2.0] - 2026-10-12
### Adicionado
- **Modelagem de Algoritmos de Recomendação e Aprendizado por Reforço (RL):**
  - Documentação e formulação matemática da função de recompensa para o CineSwipe nos 4 eixos ($R \in \{+1.0, -1.0, +0.8, -0.5\}$).
  - Especificação técnica da **Arquitetura A (LinUCB Contextual Bandits)** para balanceamento dinâmico de exploração vs. explotação com complexidade $O(d^2)$ ($<0.5\text{ms}$).
  - Especificação técnica da **Arquitetura B (Taste Drift por Média Móvel Exponencial - EMA)** com fator de memória $\lambda \approx 0.85$ e reranking por similaridade de cosseno.
  - Especificação técnica da **Arquitetura C (Session-Based Deep Q-Learning)** com espaço de estados temporal, ações de deck e fator de desconto de retenção $\gamma = 0.9$.
  - Especificação técnica da **Arquitetura D (Two-Tower Retrieval + Bandit Reranking)** para catálogos em escala com `pgvector` e HNSW index.
- **Estratégia de Expansão Massiva de Catálogo:**
  - Diagnóstico dos limites do coletor inicial (teto fixo de 120 e filtro de 60 dias).
  - Definição do pipeline de enriquecimento multidimensional via TMDB/JustWatch para 1.000+ a 10.000+ títulos (populares por streaming, clássicos aclamados pela crítica, multianual e segmentação por 12 gêneros-chave).
- **Publicação Remota e CI/CD em Produção:**
  - Repositório remoto criado e sincronizado em [https://github.com/jusolu/cinerank-streaming](https://github.com/jusolu/cinerank-streaming).
  - Deploy em produção ativo e funcional via Vercel em [https://projetofilmesranking.vercel.app](https://projetofilmesranking.vercel.app).
  - Integração contínua ativada: qualquer push na branch `main` gera nova build serverless na Vercel automaticamente.

---

## [2.1.0] - 2026-10-12
### Adicionado
- **Mecanismo CineSwipe (Modo Descoberta Estilo Tinder em 4 Eixos):**
  - Implementação de swipe interativo nos 4 quadrantes cardeais:
    - $\rightarrow$ **Direita:** "Já assisti e Gostei!" (+1.0 taste signal).
    - $\leftarrow$ **Esquerda:** "Já assisti e Não Gostei" (-1.0 taste penalty).
    - $\uparrow$ **Cima:** "Não assisti, mas Quero Ver!" (+0.8 intent / salva na Watchlist).
    - $\downarrow$ **Baixo:** "Não assisti e Não Assistiria" (-0.5 skip/filtro).
  - Física de arrasto em tempo real com aceleração angular ($dx \times 0.06$).
  - Suporte completo a eventos de ponteiro (mouse drag) e toque capacitivo (touch mobile).
  - Selos visuais com opacidade responsiva (`GOSTEI!`, `NÃO GOSTEI`, `QUERO VER! 🍿`, `NÃO ASSISTIRIA`).
  - Navegação ergonômica via teclado com as 4 setas direcionais ($\leftarrow, \rightarrow, \uparrow, \downarrow$).
  - Modal dedicado **"Minha Watchlist"** com remoção de itens e acesso à ficha completa.
  - Persistência de estado local via `localStorage` (`cineswipe_history` e `cineswipe_watchlist`).
  - Filtro de catálogo por plataforma de streaming dentro do CineSwipe.
- **Preparação de Deploy Serverless (Zero-Cost / Vercel):**
  - Adicionado `vercel.json` com roteamento reverso de static files e endpoint serverless.
  - Criado `api/index.py` para exportação nativa WSGI da aplicação Flask na Vercel.
  - Adicionado `requirements.txt` com `Flask>=3.0.0` e `requests>=2.31.0`.
  - Criado `.gitignore` com exclusão de caches Python, ambientes virtuais e credenciais.

---

## [2.0.0] - 2026-10-11
### Adicionado
- **Grafo Interativo de Conexões de Roteiro e Temas:**
  - Visualização em Canvas 2D interativo com algoritmo force-directed layout (simulação física com repulsão de Coulomb e atração de mola Hooke).
  - Cálculo de similaridade ponderada entre filmes considerando roteiristas, diretores, elenco e gêneros.
  - Painel lateral inspetor com sinopse comparativa, temas em comum e percentual de afinidade.
  - Controles de sensibilidade (afinidade mínima de 15% a 50%) e número máximo de conexões.
  - Navegação no canvas com zoom, arrasto (pan) e clique para selecionar/centralizar nós.
- **Redesign Editorial Estrito (White / Black / Blue):**
  - Eliminação de tons pretos escuros excessivos e cores neon.
  - Adoção de paleta estrita de 3 cores: Branco (fundos e cards), Preto (tipografia com alto contraste) e Azul institucional (acentos, badges e CTAs).
  - Tipografia de sistema nativo sem serifa para máxima legibilidade e carregamento instantâneo.
- **Filtros e Visualizações Alternadas:**
  - Alternador dinâmico entre visualização em Grade de Cards e visualização em Tabela/Lista analítica.
  - Filtro por janela de lançamento: 7 dias (semana), 15 dias, 30 dias, 60 dias ou todo o catálogo.
  - Busca inteligente em tempo real com debounce por título, diretor, elenco ou gênero.

---

## [1.0.0] - 2026-10-10
### Adicionado
- **Fundação da Plataforma CineRank Streaming:**
  - Servidor backend em Flask com rotas REST (`/api/movies`, `/api/movies/<id>`, `/api/movies/sync`, `/api/movies/<id>/reviews`).
  - Motor de cálculo **CineScore** ponderando Tomatometer (Rotten Tomatoes), Popcornmeter (público RT), IMDb Score e Metascore.
  - Integração com as APIs oficiais do TMDB (The Movie Database) e OMDb (Open Movie Database).
  - Modal com sinopse, ficha técnica, elenco, player oficial de trailer no YouTube e sistema de avaliações com envio de comentários.
  - Catálogo inicial com 24 lançamentos recentes de 2026 nos principais serviços (Netflix, Prime Video, Disney+, Max, Paramount+, Hulu, Apple TV+, MUBI, Peacock).
