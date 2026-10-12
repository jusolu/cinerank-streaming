# 🎬 CineRank Streaming & CineSwipe

Plataforma inteligente de recomendação, ranking editorial e descoberta de filmes por gestos nos principais serviços de streaming (**Netflix, Prime Video, Disney+, Max, Paramount+, Hulu, Apple TV+, MUBI, Peacock**).

Projetada com arquitetura **Zero-Cost ($0/mês)**, design editorial limpo de alta legibilidade (3 cores: Branco, Preto e Azul) e suporte a três modos complementares de experiência do usuário.

---

## 🌟 Principais Recursos

### 1. 📊 Catálogo Editorial & Ranking Ponderado
- **CineScore Oficial (0 a 100):** Cálculo estatístico que consolida o **Tomatometer** (Crítica do Rotten Tomatoes), o **Popcornmeter** (Público verificado), a nota e volume de votos do **IMDb** e o **Metascore**.
- **Filtros Ágeis:** Segmentação instantânea por provedor de streaming, período de lançamento (7, 15, 30, 60 dias ou catálogo completo) e ordenação por critérios múltiplos.
- **Alternador de Visualização:** Modo Grade de Cartazes ou Modo Tabela Analítica.
- **Ficha Completa com Trailer:** Sinopse, elenco, direção, trailer oficial em alta definição no YouTube e fórum de resenhas com recálculo dinâmico de score.

### 2. 🕸️ Grafo Interativo de Conexões de Roteiro
- Visualização em Canvas 2D interativo com algoritmo de layout force-directed (física de nós e molas).
- Cálculo de similaridade temática e de roteiro baseado em diretores, roteiristas, palavras-chave de enredo e gêneros.
- Inspetor lateral com análise de afinidade percentual e pontos em comum entre obras cinematográficas.

### 3. 🔥 CineSwipe (Modo Descoberta em 4 Eixos Estilo Tinder)
- Descoberta ultrarrápida de títulos através de gestos intuitivos (mouse drag, touch mobile ou setas do teclado):
  - ➡️ **Swipe para Direita:** *Já assisti e Gostei!* (+1.0 taste boost).
  - ⬅️ **Swipe para Esquerda:** *Já assisti e Não Gostei* (-1.0 taste penalty).
  - ⬆️ **Swipe para Cima:** *Não assisti, mas Quero Ver!* (Salva automaticamente na **Minha Watchlist**).
  - ⬇️ **Swipe para Baixo:** *Não assisti e Não Assistiria* (-0.5 skip).
- Carimbos dinâmicos com opacidade proporcional ao arrasto.
- Modal ergonômico de **Watchlist** para gerenciar e assistir posteriormente aos filmes salvos.
- Persistência contínua via `localStorage`.

---

## 🚀 Como Executar o Projeto Localmente

### Pré-requisitos
- Python 3.10 ou superior instalado.

### Execução Rápida
```powershell
# 1. Instalar as dependências
pip install -r requirements.txt

# 2. Iniciar o servidor
python app.py
```

Abra no seu navegador:
👉 **[http://127.0.0.1:5000](http://127.0.0.1:5000)**

---

## ☁️ Deploy Serverless com Custo Zero ($0 / Mês)

A aplicação está configurada para deploy na **Vercel** via Serverless Python:

```powershell
# Deploy para produção
cmd /c npx vercel --prod --yes
```

- **Roteador:** [vercel.json](file:///c:/projeto_filmes_ranking/vercel.json)
- **Entrypoint:** [api/index.py](file:///c:/projeto_filmes_ranking/api/index.py)

---

## 📚 Documentação Técnica Completa

Para aprofundar na estratégia, histórico ou instruções para outros agentes de desenvolvimento:

- 📖 **[ARCHITECTURE.md](file:///c:/projeto_filmes_ranking/ARCHITECTURE.md):** Estratégia completa de cobertura global de filmes (streaming + compra/aluguel), pipelines com GitHub Actions + TMDB/JustWatch, Supabase Free Tier com `pgvector` e matemática do algoritmo de recomendação.
- 📜 **[CHANGELOG.md](file:///c:/projeto_filmes_ranking/CHANGELOG.md):** Histórico completo de versões, funcionalidades adicionadas e modificações de design.
- 🗺️ **[ROADMAP.md](file:///c:/projeto_filmes_ranking/ROADMAP.md):** Manual passo a passo para o próximo agente continuar as próximas fases (Supabase, embeddings via Gemini API e pipelines diários).

---

## 🎨 Sistema de Design
- **Paleta Estrita de 3 Cores:**
  - Fundo: Branco Puro (`#ffffff`) e cinza sutil de apoio (`#f8fafc`).
  - Tipografia: Preto de Alto Contraste (`#000000` / `#0f172a`).
  - Acentos: Azul Institucional (`#1e3a8a` / `#2563eb`).
- **Fontes:** Família nativa do sistema (`system-ui`, `-apple-system`, `Segoe UI`, `Roboto`), garantindo 0ms de bloqueio de renderização (*zero FOIT/FOUT*).
- **Acessibilidade:** Ícones FontAwesome 6, tags semânticas HTML5 e contraste WCAG AAA em todos os textos editoriais.
