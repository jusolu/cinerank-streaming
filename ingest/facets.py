"""
Monta o pacote de atributos ("facets") de cada filme a partir de todas as fontes do banco de trabalho.

Saída por filme: {grupo: [valores]}, com rótulos em português sempre que possível.
Usado pelo export (data/catalog.db) e, no site, pela ficha, pela árvore de decisão e pela vizinhança.
"""
import re
from collections import defaultdict

# Grupo → categoria mostrada na árvore.
CATEGORY = {
    'Elenco': 'Equipe', 'Direção': 'Equipe', 'Roteiro': 'Equipe', 'Fotografia': 'Equipe',
    'Trilha sonora': 'Equipe', 'Montagem': 'Equipe', 'Direção de arte': 'Equipe',
    'País': 'Origem', 'Região': 'Origem', 'Idioma original': 'Origem', 'Estúdio': 'Origem', 'Orçamento': 'Origem',
    'Subgênero': 'Enredo', 'Tema': 'Enredo', 'Assunto': 'Enredo', 'Baseado em': 'Enredo',
    'Local da história': 'Enredo', 'Franquia': 'Enredo',
    'Prêmios': 'Reconhecimento', 'Crítica': 'Reconhecimento', 'Bilheteria': 'Reconhecimento',
    'Classificação': 'Público', 'Locais de filmagem': 'Origem',
    'Cor': 'Estilo', 'Universo': 'Enredo', 'Paleta': 'Estilo', 'Pôster': 'Estilo',
    'Percepção do público': 'Estilo', 'Produção': 'Equipe', 'Figurino': 'Equipe',
}

LIMITS = {'Produção': 3, 'Figurino': 1, 'Percepção do público': 6, 'Elenco': 4, 'Roteiro': 3, 'Fotografia': 2, 'Trilha sonora': 2, 'Montagem': 1, 'Direção de arte': 1,
          'País': 3, 'Estúdio': 3, 'Locais de filmagem': 3, 'Tema': 8, 'Subgênero': 5, 'Assunto': 4, 'Local da história': 3, 'Franquia': 2}

ROLE_GROUP = {'cast': 'Elenco', 'director': 'Direção', 'writer': 'Roteiro', 'photography': 'Fotografia',
              'music': 'Trilha sonora', 'editing': 'Montagem', 'design': 'Direção de arte',
              'producer': 'Produção', 'costume': 'Figurino'}

LANGUAGES = {
    'en': 'Inglês', 'es': 'Espanhol', 'fr': 'Francês', 'ja': 'Japonês', 'de': 'Alemão', 'it': 'Italiano',
    'zh': 'Chinês', 'cn': 'Cantonês', 'hi': 'Hindi', 'pt': 'Português', 'ru': 'Russo', 'ko': 'Coreano',
    'ml': 'Malaiala', 'ta': 'Tâmil', 'te': 'Telugo', 'tr': 'Turco', 'sv': 'Sueco', 'da': 'Dinamarquês',
    'no': 'Norueguês', 'nb': 'Norueguês', 'fi': 'Finlandês', 'nl': 'Holandês', 'pl': 'Polonês', 'fa': 'Persa',
    'ar': 'Árabe', 'he': 'Hebraico', 'th': 'Tailandês', 'id': 'Indonésio', 'tl': 'Filipino', 'cs': 'Tcheco',
    'hu': 'Húngaro', 'ro': 'Romeno', 'el': 'Grego', 'uk': 'Ucraniano', 'bn': 'Bengali', 'mr': 'Marati',
    'kn': 'Canarês', 'pa': 'Panjabi', 'is': 'Islandês', 'et': 'Estoniano', 'sr': 'Sérvio', 'hr': 'Croata',
}

# País (nome do TMDB) → (nome em português, região)
COUNTRIES = {
    'United States of America': ('Estados Unidos', 'América do Norte'), 'Canada': ('Canadá', 'América do Norte'),
    'Mexico': ('México', 'América Latina'), 'Brazil': ('Brasil', 'América Latina'),
    'Argentina': ('Argentina', 'América Latina'), 'Chile': ('Chile', 'América Latina'),
    'Colombia': ('Colômbia', 'América Latina'), 'Peru': ('Peru', 'América Latina'),
    'Uruguay': ('Uruguai', 'América Latina'), 'Cuba': ('Cuba', 'América Latina'),
    'United Kingdom': ('Reino Unido', 'Europa'), 'Ireland': ('Irlanda', 'Europa'), 'France': ('França', 'Europa'),
    'Germany': ('Alemanha', 'Europa'), 'Italy': ('Itália', 'Europa'), 'Spain': ('Espanha', 'Europa'),
    'Portugal': ('Portugal', 'Europa'), 'Belgium': ('Bélgica', 'Europa'), 'Netherlands': ('Holanda', 'Europa'),
    'Sweden': ('Suécia', 'Europa'), 'Denmark': ('Dinamarca', 'Europa'), 'Norway': ('Noruega', 'Europa'),
    'Finland': ('Finlândia', 'Europa'), 'Iceland': ('Islândia', 'Europa'), 'Poland': ('Polônia', 'Europa'),
    'Czech Republic': ('República Tcheca', 'Europa'), 'Austria': ('Áustria', 'Europa'),
    'Switzerland': ('Suíça', 'Europa'), 'Hungary': ('Hungria', 'Europa'), 'Romania': ('Romênia', 'Europa'),
    'Greece': ('Grécia', 'Europa'), 'Russia': ('Rússia', 'Europa'), 'Ukraine': ('Ucrânia', 'Europa'),
    'Turkey': ('Turquia', 'Oriente Médio'), 'Iran': ('Irã', 'Oriente Médio'), 'Israel': ('Israel', 'Oriente Médio'),
    'Lebanon': ('Líbano', 'Oriente Médio'), 'Egypt': ('Egito', 'África'), 'South Africa': ('África do Sul', 'África'),
    'Nigeria': ('Nigéria', 'África'), 'Morocco': ('Marrocos', 'África'),
    'Japan': ('Japão', 'Ásia'), 'South Korea': ('Coreia do Sul', 'Ásia'), 'China': ('China', 'Ásia'),
    'Hong Kong': ('Hong Kong', 'Ásia'), 'Taiwan': ('Taiwan', 'Ásia'), 'India': ('Índia', 'Ásia'),
    'Thailand': ('Tailândia', 'Ásia'), 'Indonesia': ('Indonésia', 'Ásia'), 'Philippines': ('Filipinas', 'Ásia'),
    'Australia': ('Austrália', 'Oceania'), 'New Zealand': ('Nova Zelândia', 'Oceania'),
}

GENERIC_SUBGENRES = re.compile(
    r'^(drama|filme|film|longa-metragem|feature film|filme de (ação|aventura|comédia|drama|terror|suspense|'
    r'ficção científica|fantasia|animação|romance|mistério|guerra|crime)|comédia|filme dramático|'
    r'filme de ficção|fiction film|thriller|action film|adventure film|comedy film|drama film|filme de faroeste|'
    r'filme musical|filme documentário|documentário|filme biográfico|filme histórico|filme sobre crimes|'
    r'filme policial|filme romântico|filme de romance)$', re.I)

NOT_FRANCHISE = re.compile(r'melhores|best|lista|list|\b100\b|afi|bfi|bbc|top ', re.I)

BASED_ON_TYPES = [
    (re.compile(r'romance|novela|livro|obra liter|conto|novel|book|short story|literary', re.I), 'Livro'),
    (re.compile(r'quadrinho|hq|mangá|manga|comic|graphic novel|super-?her', re.I), 'Quadrinhos / mangá'),
    (re.compile(r'peça|teatr|musical|play\b|stage', re.I), 'Teatro / musical'),
    (re.compile(r'jogo eletr|videogame|video game|jogo de v[ií]deo', re.I), 'Videogame'),
    (re.compile(r'série de televisão|programa de televisão|television series|tv series|anime', re.I), 'Série de TV'),
    (re.compile(r'evento|acontecimento|batalha|guerra|crime|assassinato|desastre|caso|biografia|ser humano$|pessoa', re.I),
     'Fatos ou pessoas reais'),
    (re.compile(r'filme|film', re.I), 'Outro filme (remake)'),
    (re.compile(r'artigo|reportagem|article|journal', re.I), 'Reportagem'),
]

AWARDS = [
    (re.compile(r'[oó]scar de melhor filme|academy award for best picture', re.I), 'Oscar de melhor filme'),
    (re.compile(r'[oó]scar|academy award', re.I), 'Venceu Oscar'),
    (re.compile(r'palma de ouro|palme d.or', re.I), 'Palma de Ouro (Cannes)'),
    (re.compile(r'le[aã]o de ouro|golden lion', re.I), 'Leão de Ouro (Veneza)'),
    (re.compile(r'urso de ouro|golden bear', re.I), 'Urso de Ouro (Berlim)'),
    (re.compile(r'globo de ouro|golden globe', re.I), 'Globo de Ouro'),
    (re.compile(r'bafta', re.I), 'BAFTA'),
]

# Palavras-chave muito genéricas que não ajudam a distinguir filmes.
GENERIC_KEYWORDS = {'woman director', 'duringcreditsstinger', 'aftercreditsstinger', 'based on novel or book',
                    'sequel', 'remake', 'independent film', 'biography', 'murder', 'love', 'friendship',
                    'new york city', 'los angeles, california', 'death', 'family', 'revenge', 'violence', 'police'}


RATING_US = {'G': 'MPAA G (livre)', 'PG': 'MPAA PG', 'PG-13': 'MPAA PG-13', 'R': 'MPAA R (adulto)',
             'NC-17': 'MPAA NC-17'}


def rating_br(value):
    v = value.strip().upper()
    if v in ('L', 'LIVRE', 'AL'):
        return 'Livre'
    digits = re.sub(r'\D', '', v)
    return f'{digits} anos' if digits in ('10', '12', '14', '16', '18') else None


def parse_number(value):
    m = re.search(r'-?\d+(?:\.\d+)?', value or '')
    return float(m.group()) if m else None


def budget_bucket(budget):
    if not budget or budget < 1000:
        return None
    if budget < 5_000_000:
        return 'Baixo orçamento (até US$ 5 mi)'
    if budget < 40_000_000:
        return 'Orçamento médio (US$ 5–40 mi)'
    if budget < 120_000_000:
        return 'Orçamento alto (US$ 40–120 mi)'
    return 'Superprodução (US$ 120 mi+)'


def split(value):
    return [v.strip() for v in (value or '').split(',') if v.strip()]


def build_all(conn, imdb_ids, table_prefix=''):
    """Retorna {imdb_id: {grupo: [valores]}} para os filmes pedidos.

    `table_prefix` permite consultar um banco anexado (ex.: 'src.').
    """
    ids = list(imdb_ids)
    idset = set(ids)
    f = defaultdict(lambda: defaultdict(list))
    t = table_prefix
    # Tabela temporária com os filmes pedidos: as consultas filtram no SQL em vez de varrer tudo.
    conn.execute('DROP TABLE IF EXISTS temp._facet_ids')
    conn.execute('CREATE TEMP TABLE _facet_ids (imdb_id TEXT PRIMARY KEY)')
    conn.executemany('INSERT OR IGNORE INTO temp._facet_ids VALUES (?)', [(i,) for i in ids])
    scope = 'JOIN temp._facet_ids USING (imdb_id)'

    def add(iid, group, value):
        if value and value not in f[iid][group] and len(f[iid][group]) < LIMITS.get(group, 99):
            f[iid][group].append(value)

    # Pessoas, na ordem de crédito.
    for iid, role, name in conn.execute(f"""
            SELECT mp.imdb_id, mp.role, p.name FROM {t}movie_people mp {scope} JOIN {t}people p USING (person_id)
            WHERE mp.person_id NOT LIKE 'tmdb:%' ORDER BY mp.imdb_id, mp.role, mp.ord"""):
        if iid in idset and role in ROLE_GROUP:
            add(iid, ROLE_GROUP[role], name)

    # Origem (cópia do TMDB).
    for iid, lang, countries, companies, budget in conn.execute(
            f'SELECT imdb_id, original_language, countries, companies, budget FROM {t}movie_dump {scope}'):
        if iid not in idset:
            continue
        if lang:
            add(iid, 'Idioma original', LANGUAGES.get(lang, lang))
        for c in split(countries):
            pt, region = COUNTRIES.get(c, (c, None))
            add(iid, 'País', pt)
            if region:
                add(iid, 'Região', region)
        for c in split(companies):
            add(iid, 'Estúdio', c)
        add(iid, 'Orçamento', budget_bucket(budget))

    # Temas (palavras-chave do TMDB, em inglês).
    for iid, kw in conn.execute(f'SELECT imdb_id, keyword FROM {t}movie_keywords {scope}'):
        if iid in idset and kw.lower() not in GENERIC_KEYWORDS:
            add(iid, 'Tema', kw.lower())

    budgets = {}
    for iid, budget in conn.execute(f'SELECT imdb_id, budget FROM {t}movie_dump {scope} WHERE budget > 0'):
        budgets[iid] = budget
    box_office, rt, mc = defaultdict(float), {}, {}

    # Wikidata.
    for iid, prop, value in conn.execute(f'SELECT imdb_id, prop, value FROM {t}movie_wikidata {scope} ORDER BY imdb_id, prop, value'):
        if iid not in idset:
            continue
        if prop == 'subgenre' and not GENERIC_SUBGENRES.match(value.strip()):
            add(iid, 'Subgênero', value[0].upper() + value[1:])
        elif prop == 'based_on_type':
            for pattern, label in BASED_ON_TYPES:
                if pattern.search(value):
                    add(iid, 'Baseado em', label)
                    break
        elif prop == 'setting':
            add(iid, 'Local da história', value)
        elif prop == 'franchise' and not NOT_FRANCHISE.search(value):
            add(iid, 'Franquia', value)
        elif prop == 'subject':
            add(iid, 'Assunto', value)
        elif prop == 'color' and re.search(r'preto e branco|black.and.white|monocrom', value, re.I):
            add(iid, 'Cor', 'Preto e branco')
        elif prop == 'rating_br':
            add(iid, 'Classificação', rating_br(value))
        elif prop == 'rating_us':
            add(iid, 'Classificação', RATING_US.get(value.strip().upper()))
        elif prop == 'filming_location':
            add(iid, 'Locais de filmagem', value)
        elif prop == 'box_office_usd':
            box_office[iid] = max(box_office[iid], parse_number(value) or 0)
        elif prop == 'score_rt' and value.strip().endswith('%'):
            rt[iid] = parse_number(value)
        elif prop == 'score_metacritic' and '/100' in value:
            mc[iid] = parse_number(value)
        elif prop in ('award', 'nomination'):
            for pattern, label in AWARDS:
                if pattern.search(value):
                    if prop == 'nomination':
                        label = 'Indicado ao Oscar' if 'Oscar' in label else None  # rótulos já normalizados acima
                    add(iid, 'Prêmios', label)
                    break

    # Bilheteria: valor absoluto e retorno sobre o orçamento.
    for iid, gross in box_office.items():
        if gross >= 1e9:
            add(iid, 'Bilheteria', 'Bilheteria de US$ 1 bi+')
        elif gross >= 3e8:
            add(iid, 'Bilheteria', 'Blockbuster (US$ 300 mi+)')
        budget = budgets.get(iid)
        if budget and gross:
            ratio = gross / budget
            if ratio >= 8:
                add(iid, 'Bilheteria', 'Fenômeno (8× o orçamento)')
            elif ratio >= 2.5:
                add(iid, 'Bilheteria', 'Sucesso de bilheteria')
            elif ratio < 1:
                add(iid, 'Bilheteria', 'Fracasso de bilheteria')

    # Crítica: notas e veredito.
    for iid in set(rt) | set(mc):
        r, m = rt.get(iid), mc.get(iid)
        if r is not None:
            add(iid, 'Crítica', f'Rotten Tomatoes {r:.0f}%')
        if m is not None:
            add(iid, 'Crítica', f'Metacritic {m:.0f}')
        if (m is not None and m >= 80) or (r is not None and r >= 90):
            add(iid, 'Crítica', 'Aclamado pela crítica')
        elif (m is not None and m < 40) or (r is not None and r < 35):
            add(iid, 'Crítica', 'Mal recebido pela crítica')

    # Estilo derivado por modelos locais (ingest/style_text.py e ingest/style_visual.py), se já calculado.
    has_style = conn.execute(f"SELECT 1 FROM {t}sqlite_master WHERE type = 'table' AND name = 'movie_style'").fetchone()
    if has_style:
        kind_group = {'tone': 'Universo', 'palette': 'Paleta', 'poster': 'Pôster'}
        for iid, kind, value in conn.execute(f'SELECT imdb_id, kind, value FROM {t}movie_style {scope}'):
            if kind in kind_group:
                add(iid, kind_group[kind], value)

    # Percepção do público (tags do MovieLens), da mais citada para a menos.
    if conn.execute(f"SELECT 1 FROM {t}sqlite_master WHERE type = 'table' AND name = 'movie_tags'").fetchone():
        for iid, tag in conn.execute(f'SELECT imdb_id, tag FROM {t}movie_tags {scope} ORDER BY imdb_id, users DESC'):
            add(iid, 'Percepção do público', tag)

    conn.execute('DROP TABLE IF EXISTS temp._facet_ids')
    return {iid: dict(groups) for iid, groups in f.items()}
