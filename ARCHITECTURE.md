# CineRank & CineSwipe — Arquitetura de Sistema e Estratégia de Dados

Este documento documenta a estratégia para catalogar **todos os filmes disponíveis na internet (streaming, aluguel e compra)** com custo de infraestrutura de **R$ 0,00 / US$ 0,00 (Zero-Cost / Free-Tier)**, além do funcionamento técnico do mecanismo **CineSwipe** e do **Grafo de Conexões**.

---

## 1. Estratégia de Catálogo Universal de Filmes

### O Desafio
A internet possui mais de 800.000 longas-metragens catalogados, distribuídos entre:
- **SVOD (Assinatura):** Netflix, Prime Video, Disney+, Max, Apple TV+, Paramount+, MUBI, Globoplay, etc.
- **TVOD (Aluguel/Compra Digital):** Apple iTunes/Apple TV Store, Google Play Filmes/YouTube Filmes, Amazon Prime Video Store.
- **AVOD/FAST (Gratuitos com Anúncios):** Pluto TV, Runtime, NetMovies, Mercado Play.

Contratar APIs corporativas pagas (ex: JustWatch B2B API ou Gracenote) custa entre US$ 1.500 e US$ 10.000/mês. Para viabilizar o projeto com **CUSTO ZERO**, a estratégia arquitetural utiliza **Data Dumps Oficiais + APIs Abertas com Caching Inteligente**.

---

### Pipeline de Extração com Custo Zero

```
+---------------------------------------------------------------------------------+
|                                FONTES PRIMÁRIAS                                 |
|                                                                                 |
| 1. TMDB Daily Export Dumps (Gzips diários gratuitos de IDs e metadados)         |
| 2. TMDB Watch Providers API (Alimentado oficialmente pelo JustWatch)           |
| 3. OMDb API / IMDb Public Datasets (Notas Rotten Tomatoes, IMDb, Metacritic)    |
| 4. Google Gemini 1.5 Flash API (Free Tier: Embeddings vetoriais de sinopses)   |
+---------------------------------------------------------------------------------+
                                         |
                                         v
+---------------------------------------------------------------------------------+
|                    INGESTÃO & CRON: GITHUB ACTIONS (GRÁTIS)                     |
|                                                                                 |
| - 2.000 minutos mensais gratuitos de runners Linux.                             |
| - Cron executado diariamente às 04:00 UTC (delta de lançamentos das últimas 24h)|
| - Baixa o diff de filmes e disponibilidades por país ('BR').                   |
+---------------------------------------------------------------------------------+
                                         |
                                         v
+---------------------------------------------------------------------------------+
|                    BANCO DE DADOS: SUPABASE FREE TIER ($0)                      |
|                                                                                 |
| - PostgreSQL gerenciado com 500 MB (suficiente para ~150.000 títulos ativos).  |
| - Extensão pgvector nativa para busca por similaridade semântica de roteiros.   |
| - Edge Caching automático e REST API via PostgREST.                             |
+---------------------------------------------------------------------------------+
                                         |
                                         v
+---------------------------------------------------------------------------------+
|                    FRONTEND & API: VERCEL HOBBY ($0)                            |
|                                                                                 |
| - Hospedagem Serverless (Python / Node.js / HTML estático).                     |
| - 100 GB de transferência mensal com CDN global da Cloudflare / AWS Anycast.    |
+---------------------------------------------------------------------------------+
```

#### Passo 1: Ingestão de Metadados Base (TMDB Daily File Export)
- O TMDB disponibiliza diariamente arquivos `.json.gz` públicos contendo todos os IDs de filmes válidos:
  - URL: `http://files.tmdb.org/p/exports/movie_ids_MM_DD_YYYY.json.gz`
- O script baixa o dump em menos de 10 segundos no GitHub Actions, compara os IDs já existentes no banco e identifica apenas os novos filmes ou filmes modificados (delta update).

#### Passo 2: Mapeamento de Onde Assistir (Watch Providers)
- O endpoint `/movie/{movie_id}/watch/providers` do TMDB é alimentado por parceria direta com a **JustWatch**.
- Ele retorna exatamente a disponibilidade no Brasil (`BR`):
  - `flatrate`: Streaming por assinatura (Netflix, Max, Prime, etc.)
  - `rent`: Aluguel digital (Apple TV, Google Play, Prime Video)
  - `buy`: Compra definitiva
  - `ads` / `free`: Streaming gratuito com comerciais

#### Passo 3: Métricas Consolidadas (CineScore)
- Notas coletadas:
  1. **Tomatometer** (Crítica especializada do Rotten Tomatoes via OMDb API)
  2. **Popcornmeter / Audience Score** (Aprovação do público no Rotten Tomatoes)
  3. **IMDb Rating & Votes** (Nota ponderada mundial do IMDb)
  4. **Metascore** (Metacritic)
- Fórmula matemática do **CineScore**:
  $$\text{CineScore} = (0.35 \times \text{Tomatometer}) + (0.25 \times \text{Popcornmeter}) + (0.30 \times (\text{IMDb} \times 10)) + (0.10 \times \text{Metascore})$$

---

## 2. CineSwipe — Mecanismo de Recomendação por Gestos (4 Eixos)

O **CineSwipe** transforma a descoberta de filmes em uma experiência intuitiva, lúdica e de alta velocidade inspirada no Tinder, mas otimizada para a jornada de decisão cinematográfica:

```
                          [ ↑ CIMA ]
                     Quero Assistir! 🍿
               (+0.8 Intent / Salva na Watchlist)
                            |
                            |
   [ ← ESQUERDA ]           |            [ → DIREITA ]
   Já vi e NÃO Gostei       +--------    Já vi e GOSTEI!
  (-1.0 Taste Penalty)      |            (+1.0 Taste Boost)
                            |
                            |
                         [ ↓ BAIXO ]
                       Não Assistiria
                    (-0.5 Skip / Silêncio)
```

### Semântica dos 4 Gestos:

1. **Swipe para a Direita ($\rightarrow$) — "Já assisti e Gostei!"**
   - **Sinal:** Taste Signal Positivo (+1.0).
   - **Ação:** Fortalece o vetor de gosto do usuário nos gêneros, diretores e palavras-chave temáticas desse filme.
   - **Efeito na Fila:** Filmes similares ganham peso e sobem para o topo do baralho.

2. **Swipe para a Esquerda ($\leftarrow$) — "Já assisti e Não Gostei"**
   - **Sinal:** Penalidade Negativa (-1.0).
   - **Ação:** Penaliza a afinidade do usuário com os atributos centrais do filme (ex: subgêneros repetitivos ou direções específicas).
   - **Efeito na Fila:** Rebaixa títulos do mesmo nicho e evita recomendações repetitivas.

3. **Swipe para Cima ($\uparrow$) — "Não assisti, mas Quero Ver!"**
   - **Sinal:** Sinal de Alta Intenção (+0.8 Intent).
   - **Ação:** Adiciona automaticamente o título à **Minha Watchlist** persistente.
   - **Efeito na Fila:** Identifica interesse latente e explora títulos que compartilham a mesma sinopse conceitual.

4. **Swipe para Baixo ($\downarrow$) — "Não assisti e Não Assistiria"**
   - **Sinal:** Skip / Filtro de Ruído (-0.5).
   - **Ação:** Suprime títulos correlatos sem penalizar severamente os gêneros macros (apenas silencia o estilo específico).

### Arquitetura de Interação:
- **Gestos Touch & Mouse:** Detecção contínua via `pointerdown`, `pointermove`, `pointerup` com cálculo trigonométrico de ângulo e rotação de aceleração ($dx \times 0.06$).
- **Selos Dinâmicos de Feedback:** Carimbos visuais (`GOSTEI!`, `NÃO GOSTEI`, `QUERO VER! 🍿`, `NÃO ASSISTIRIA`) cuja opacidade escala proporcionalmente ao deslocamento do card ($0.0 \to 1.0$).
- **Atalhos de Teclado:** Setas direcionais ($\leftarrow, \rightarrow, \uparrow, \downarrow$) para navegação desktop ergonômica sem fadiga de mouse.
- **Persistência Local e Nuvem:** Armazenamento instantâneo via `localStorage` (`cineswipe_history` e `cineswipe_watchlist`) com sync opcional no banco.

---

## 3. Grafo Interativo de Conexões de Roteiro

O sistema possui uma engine em Canvas 2D que renderiza um **grafo dirigido por forças físicas** (*Force-Directed Graph*):
- **Nós:** Filmes da base.
- **Arestas:** Similaridade calculada entre roteiros, diretores, elenco e temas.
- **Fórmula de Similaridade ($S$):**
  $$S(A, B) = w_{\text{genre}} \cdot \text{Jaccard}(G_A, G_B) + w_{\text{director}} \cdot \delta(D_A, D_B) + w_{\text{cast}} \cdot \text{Jaccard}(C_A, C_B) + w_{\text{text}} \cdot \cos(\vec{T}_A, \vec{T}_B)$$
- **Inspetor Lateral:** Permite analisar roteiristas, estúdio, justificativa da conexão narrativa e afinidade percentual em tempo real.

---

## 4. Guia de Infraestrutura Zero-Custo ($0 / Mês)

### 1. Vercel (Frontend & Serverless Python)
- **Plano:** Hobby (100% Gratuito).
- **Limites:** 100 GB de largura de banda/mês, execuções serverless ilimitadas dentro da cota.
- **Arquivo de Configuração:** `vercel.json` e `api/index.py`.
- **Deploy:** `cmd /c npx vercel --prod`.

### 2. Google Cloud Platform (GCP)
- **Status da Conta:** `jusolu@gmail.com` com 0 projetos ativos cobrados.
- **Serviço Gratuito Recomendado:** **Cloud Run** (Free Tier perpétuo).
  - 2 milhões de requisições gratuitas por mês.
  - Escala a zero (quando ninguém acessa, o custo é rigorosamente US$ 0,00).
  - 360.000 GiB-segundos de memória grátis.

### 3. Supabase (PostgreSQL + pgvector)
- **Plano:** Free Tier.
- **Recursos:** 500 MB de banco relacional, 50.000 usuários ativos de autenticação, API REST automática e extensão vetorial instalada com 1 clique (`CREATE EXTENSION vector;`).

### 4. GitHub Actions (Automated Pipelines)
- **Plano:** Gratuito em repositórios públicos ou 2.000 minutos/mês em repositórios privados.
- **Papel:** Executa os scrapers e sincronizações diárias sem exigir servidor ligado 24 horas por dia.

---

## 5. Diretrizes de Design
1. **Paleta Estrita de 3 Cores:**
   - **Branco (`#ffffff`):** Fundos e cartões de conteúdo.
   - **Preto (`#000000` / `#0f172a`):** Tipografia de títulos e textos com alto contraste.
   - **Azul (`#1e3a8a` / `#2563eb`):** Acentos, destaques editoriais, botões primários e badges.
2. **Tipografia:** Sem serifa moderna do sistema (`system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue`).
3. **Design Limpo e Funcional:** Zero tons escuros cansativos, zero cores neon estridentes.
