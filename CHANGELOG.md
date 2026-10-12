# CineRank Streaming — Changelog do Projeto

Todas as alterações notáveis, implementações de funcionalidades e decisões de arquitetura deste projeto estão documentadas neste arquivo.

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
- **Limpeza de Infraestrutura Cloud:**
  - Conta Vercel (`jusolu`) limpa via Vercel CLI (removidos projetos obsoletos `catalogo-mimos-de-leo` e `projeto-esp32-irrigacao`). Verificado 0 projetos ativos.
  - Google Cloud SDK 588.0.0 instalado e autenticado com `jusolu@gmail.com`. Verificado 0 projetos ativos (garantia de R$ 0,00 de cobrança recorrente).

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
