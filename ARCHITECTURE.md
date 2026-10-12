# CineRank & CineSwipe — Arquitetura de Sistema e Estratégia de Dados

Este documento documenta a estratégia para catalogar **todos os filmes disponíveis na internet (streaming, aluguel e compra)** com custo de infraestrutura de **R$ 0,00 / US$ 0,00 (Zero-Cost / Free-Tier)**, o funcionamento do mecanismo **CineSwipe**, do **Grafo de Conexões** e as **Estratégias de Recomendação e Aprendizado por Reforço (RL)**.

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
   - **Sinal:** Taste Signal Positivo ($R = +1.0$).
   - **Ação:** Fortalece o vetor de gosto do usuário nos gêneros, diretores e palavras-chave temáticas desse filme.
   - **Efeito na Fila:** Filmes similares ganham peso e sobem para o topo do baralho.

2. **Swipe para a Esquerda ($\leftarrow$) — "Já assisti e Não Gostei"**
   - **Sinal:** Penalidade Negativa ($R = -1.0$).
   - **Ação:** Penaliza a afinidade do usuário com os atributos centrais do filme (ex: subgêneros repetitivos ou direções específicas).
   - **Efeito na Fila:** Rebaixa títulos do mesmo nicho e evita recomendações repetitivas.

3. **Swipe para Cima ($\uparrow$) — "Não assisti, mas Quero Ver!"**
   - **Sinal:** Sinal de Alta Intenção ($R = +0.8$).
   - **Ação:** Adiciona automaticamente o título à **Minha Watchlist** persistente.
   - **Efeito na Fila:** Identifica interesse latente e explora títulos que compartilham a mesma sinopse conceitual.

4. **Swipe para Baixo ($\downarrow$) — "Não assisti e Não Assistiria"**
   - **Sinal:** Skip / Filtro de Ruído ($R = -0.5$).
   - **Ação:** Suprime títulos correlatos sem penalizar severamente os gêneros macros (apenas silencia o estilo específico).

---

## 3. Estratégias de Recomendação e Aprendizado por Reforço (RL)

A interação contínua de swipes gera um fluxo rico de **feedback implícito e explícito**. Para transformar essa torrente de interações em uma experiência viciante e hiperpersonalizada, desenhamos 4 arquiteturas de algoritmos:

### Arquitetura A: Contextual Multi-Armed Bandits (LinUCB / Thompson Sampling) ⭐ *(Recomendada)*
O problema fundamental de apps de descoberta rápida (como TikTok, Spotify Discover e Tinder) é o equilíbrio entre **Exploração** (mostrar novidades fora da zona de conforto) e **Explotação** (mostrar o que já sabemos que o usuário adora).

- **Como funciona matematicamente:**
  - Cada filme $a$ possui um vetor de contexto de atributos normalizados $x_a \in \mathbb{R}^d$ (gêneros, diretor, palavras-chave, CineScore, faixa de ano).
  - O algoritmo mantém uma estimativa de preferências do usuário $\hat{\theta} \in \mathbb{R}^d$ e uma matriz de covariância acumulada $A \in \mathbb{R}^{d \times d}$.
  - Para cada filme candidato $a$, calcula-se o limite superior de confiança (UCB):
    $$\text{Score}(a) = x_a^T \hat{\theta} + \alpha \sqrt{x_a^T A^{-1} x_a}$$
    - O termo $x_a^T \hat{\theta}$ é a **recompensa esperada** (explotação).
    - O termo $\alpha \sqrt{x_a^T A^{-1} x_a}$ é a **incerteza estatística** (exploração). Se o usuário nunca interagiu com *suspenses escandinavos*, a incerteza é alta e o filme recebe um bônus de exibição.
  - **Atualização Online em Tempo Real:** A cada swipe com recompensa $r_t \in \{+1.0, -1.0, +0.8, -0.5\}$:
    $$A \leftarrow A + x_t x_t^T, \qquad b \leftarrow b + r_t x_t, \qquad \hat{\theta} = A^{-1} b$$
  - **Complexidade:** Inversão de matriz de dimensão pequena ($d \approx 20$) executada em menos de **$0.5\text{ms}$**, viabilizando execução direta no navegador (JavaScript) ou na API serverless sem custo de infraestrutura.

---

### Arquitetura B: Perfil Dinâmico Vetorial com *Taste Drift* (EMA)
O humor e o objetivo do usuário mudam dinamicamente ao longo de uma sessão (ex: em uma sexta-feira à noite o usuário pode querer comédias leves, enquanto num domingo à tarde busca documentários).

- **Como funciona:**
  - O vetor do usuário $U_t \in \mathbb{R}^d$ é atualizado continuamente via Média Móvel Exponencial (*Exponential Moving Average*):
    $$U_t = \lambda U_{t-1} + (1 - \lambda) \cdot R_t \cdot V_{\text{filme}}$$
    - $\lambda \in [0.80, 0.90]$ é o **fator de memória** (permite que o vetor se adapte gradualmente sem esquecer as preferências base).
    - $R_t$ é o sinal de recompensa do gesto.
    - $V_{\text{filme}}$ é o embedding de atributos do filme.
  - A reordenação dos próximos cards da fila é dada pela similaridade de cosseno ponderada pelo CineScore:
    $$\text{Ranking}(i) = \cos(U_t, V_i) \times \left(0.7 + 0.3 \times \frac{\text{CineScore}_i}{100}\right)$$

---

### Arquitetura C: Deep Q-Learning por Sessão (Session-Based RL)
Modelagem formal do CineSwipe como um Processo de Decisão de Markov (MDP):
- **Estado ($S_t$):** As últimas $k=10$ interações $(m_1, r_1), (m_2, r_2), \dots, (m_k, r_k)$ concatenadas com o contexto temporal (dia da semana, hora do dia, provedor filtrado).
- **Ação ($A_t$):** Seleção do próximo filme a ser apresentado no topo do deck.
- **Função de Recompensa:**
  $$R_t = \begin{cases} +1.0 & \text{Swipe Direita (Gostei)} \\ -1.0 & \text{Swipe Esquerda (Não Gostei)} \\ +0.8 \times w_{\text{intent}} & \text{Swipe Cima (Quero Ver)} \\ -0.5 & \text{Swipe Baixo (Não Assistiria)} \end{cases}$$
- **Objetivo da Política:** Maximizar o retorno de longo prazo $G_t = \sum_{\tau=0}^{\infty} \gamma^\tau R_{t+\tau}$, onde $\gamma=0.9$ evita a "fadiga de catálogo" e desestimula o abandono do app.

---

### Arquitetura D: Pipeline Híbrido de Duas Fases (Two-Tower Retrieval + Bandit Reranking)
Padrão de engenharia adotado por empresas como Netflix, YouTube e TikTok para catálogos com centenas de milhares de títulos:

```
[ Catálogo de 50.000+ Filmes ]
               |
               v
  (FASE 1: CANDIDATE GENERATION)
  Supabase pgvector (HNSW Index)
  Similaridade Vetorial em ~5ms
               |
               v
       [ Top 100 Candidatos ]
               |
               v
     (FASE 2: SCORING & RERANKING)
     LinUCB Contextual Bandit
     (Balanceamento Exploração vs Explotação)
               |
               v
   [ Pilha de 15 Cards do CineSwipe ]
```

---

## 4. Estratégia de Expansão Massiva de Catálogo

### Diagnóstico das Limitações Iniciais:
Na versão de demonstração inicial, o arquivo `movie_api.py` possuía travas para viabilizar testes rápidos de desenvolvimento:
1. `days_back = 60` (Capturava apenas lançamentos recentes de 2 meses).
2. `items_to_enrich[:120]` (Teto estático de 120 filmes no pipeline).
3. Uma única chamada de descoberta por provedor (`sort_by='primary_release_date.desc'`).

### Plano de Expansão Multidimensional (1.000 a 10.000+ Títulos):
Para compor um catálogo abrangente e variado sem custos de API:
1. **Lançamentos Recentes:** Manter a ingestão delta diária de 2025/2026.
2. **Populares & Tendências no Streaming:** Consultar `sort_by=popularity.desc` para cada serviço de streaming no Brasil.
3. **Clássicos & Melhores Avaliados de Todos os Tempos:** Consultar `sort_by=vote_average.desc&vote_count.gte=500` para garantir a presença de clássicos fundamentais (*Interestelar, Matrix, Pulp Fiction, Parasita, O Senhor dos Anéis, O Poderoso Chefão*).
4. **Diversidade por Gênero:** Realizar varreduras dedicadas por gênero para garantir profundidade em:
   - Ficção Científica (`with_genres=878`)
   - Suspense / Thriller (`with_genres=53`)
   - Terror (`with_genres=27`)
   - Animação (`with_genres=16`)
   - Drama (`with_genres=18`)
   - Comédia (`with_genres=35`)
   - Documentário (`with_genres=99`)
5. **Persistência Incremental:** Armazenamento em arquivos particionados ou no Supabase Free Tier, garantindo que filmes já enriquecidos nunca precisem ser reconsultados na API externa.

---

## 5. Grafo Interativo de Conexões de Roteiro

O sistema possui uma engine em Canvas 2D que renderiza um **grafo dirigido por forças físicas** (*Force-Directed Graph*):
- **Nós:** Filmes da base.
- **Arestas:** Similaridade calculada entre roteiros, diretores, elenco e temas.
- **Fórmula de Similaridade ($S$):**
  $$S(A, B) = w_{\text{genre}} \cdot \text{Jaccard}(G_A, G_B) + w_{\text{director}} \cdot \delta(D_A, D_B) + w_{\text{cast}} \cdot \text{Jaccard}(C_A, C_B) + w_{\text{text}} \cdot \cos(\vec{T}_A, \vec{T}_B)$$
- **Inspetor Lateral:** Permite analisar roteiristas, estúdio, justificativa da conexão narrativa e afinidade percentual em tempo real.

---

## 6. Guia de Infraestrutura Zero-Custo ($0 / Mês)

### 1. Vercel (Frontend & Serverless Python)
- **Plano:** Hobby (100% Gratuito).
- **Limites:** 100 GB de largura de banda/mês, execuções serverless ilimitadas dentro da cota.
- **Arquivo de Configuração:** `vercel.json` e `api/index.py`.
- **Deploy Automático:** Conectado ao repositório GitHub `jusolu/cinerank-streaming`.

### 2. Google Cloud Platform (GCP)
- **Status da Conta:** `jusolu@gmail.com` com 0 projetos ativos cobrados.
- **Serviço Gratuito Recomendado:** **Cloud Run** (Free Tier perpétuo).
  - 2 milhões de requisições gratuitas por mês.
  - Escala a zero (quando ninguém acessa, o custo é rigorosamente US$ 0,00).

### 3. Supabase (PostgreSQL + pgvector)
- **Plano:** Free Tier.
- **Recursos:** 500 MB de banco relacional, 50.000 usuários ativos de autenticação, API REST automática e extensão vetorial instalada com 1 clique (`CREATE EXTENSION vector;`).

### 4. GitHub Actions (Automated Pipelines)
- **Plano:** Gratuito (2.000 minutos/mês em repositórios privados ou ilimitado em públicos).
- **Papel:** Executa os scrapers e sincronizações diárias sem exigir servidor ligado 24 horas por dia.

---

## 7. Diretrizes de Design
1. **Paleta Estrita de 3 Cores:**
   - **Branco (`#ffffff`):** Fundos e cartões de conteúdo.
   - **Preto (`#000000` / `#0f172a`):** Tipografia de títulos e textos com alto contraste.
   - **Azul (`#1e3a8a` / `#2563eb`):** Acentos, destaques editoriais, botões primários e badges.
2. **Tipografia:** Sem serifa moderna do sistema (`system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue`).
3. **Design Limpo e Funcional:** Zero tons escuros cansativos, zero cores neon estridentes.
