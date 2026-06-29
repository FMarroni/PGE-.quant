"""
pipeline_jurimetria.py
Pipeline de ingestão da base de dados de jurimetria - PGE/SP.

Arquitetura:
  - Demandas  → fonte primária da Frente 1 (classificação de resultado)
  - Processos → catálogo complementar (valor, comarca, vara, classe)
  - resultado_economico → cruzamento que responde: quanto a PGE ganhou / perdeu / tem em andamento
"""

# =============================================================================
# CAPÍTULO 1 – IMPORTS E CONFIGURAÇÃO DE LOG
# =============================================================================
import io
import logging
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

import db as _db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# =============================================================================
# CAPÍTULO 2 – LEITURA SEGURA DO CSV
# =============================================================================

def _ler_csv_bytes(conteudo: bytes, nome: str = "<bytes>") -> pd.DataFrame:
    """Lê CSV a partir de bytes com suporte a múltiplos encodings."""
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            df = pd.read_csv(
                io.BytesIO(conteudo),
                sep=",",
                quotechar='"',
                engine="python",
                encoding=enc,
                dtype=str,
                keep_default_na=False,
                na_values=[],
            )
            log.info(
                "Arquivo lido | nome=%s | encoding=%s | linhas=%d | colunas=%d",
                nome, enc, len(df), len(df.columns),
            )
            return df
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Nenhum encoding suportado conseguiu ler o arquivo: {nome}")


def _ler_csv_bruto(caminho_arquivo: str | Path) -> pd.DataFrame:
    """Lê CSV de arquivo no disco."""
    caminho = Path(caminho_arquivo)
    if not caminho.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {caminho.resolve()}")
    with open(caminho, "rb") as f:
        return _ler_csv_bytes(f.read(), nome=caminho.name)


# =============================================================================
# CAPÍTULO 3 – MAPEAMENTOS DE COLUNAS
# =============================================================================

# Arquivo A — Relatório de Processos
MAPA_COLUNAS: dict[str, str] = {
    "Pasta":                              "pasta",
    "Processo":                           "processo",
    "Valor":                              "valor",
    "Ajuizamento":                        "ajuizamento",
    "Cadastro":                           "cadastro",
    "Classe":                             "classe",
    "Matéria":                            "materia",
    "Assuntos":                           "assuntos",
    "Tribunal":                           "tribunal",
    "Unidade Judicial / Instituição":     "unidade_judicial",
    "Vara":                               "vara",
    "Polo PGE":                           "polo_pge",
    "Qualificação":                       "qualificacao",
    "Parte Representada":                 "parte_representada",
    "Documento Parte Rep.":               "documento_parte_rep",
    "Parte Contrária":                    "parte_contraria",
    "Documento":                          "documento",
    "Outras Partes Ativas":               "outras_partes_ativas",
    "Outras Partes Passivas":             "outras_partes_passivas",
    "Advogados":                          "advogados",
    "Tramitação":                         "tramitacao",
    "Situação":                           "situacao",
    "Unidade":                            "unidade",
    "Mesa":                               "mesa",
    "Tipo Distribuição":                  "tipo_distribuicao",
    "Nº de Dívidas":                      "num_dividas",
    "Soma Val. Atualizados":              "soma_val_atualizados",
}

# Arquivo B — Relatório de Demandas por unidade e procurador
MAPA_COLUNAS_DEMANDAS: dict[str, str] = {
    "Unidade":      "unidade",
    "Procurador":   "procurador",
    "Demanda":      "demanda",
    "Processo":     "processo_orig",
    "Pasta":        "pasta",
    "Qualificação": "qualificacao",
    "Matéria":      "materia",
    "Assuntos":     "assuntos",
    "Tribunal":     "tribunal",
    "Origem":       "origem",
    "Status":       "status_demanda",
    "Entrada":      "entrada",
    "Conclusão":    "conclusao",
    "Horas":        "horas",
    "Publicou":     "publicou",
}


# =============================================================================
# CAPÍTULO 4 – LIMPEZA DE COLUNAS E TIPOS
# =============================================================================

def _descartar_colunas_espurias(df: pd.DataFrame) -> pd.DataFrame:
    """Remove colunas sem nome ou nomeadas apenas como 'R$', geradas pela exportação."""
    validas = []
    descartadas = []
    for col in df.columns:
        nome = str(col).strip()
        if nome in ("", "R$") or nome.startswith("Unnamed:"):
            descartadas.append(col)
        else:
            validas.append(col)
    if descartadas:
        log.info("Colunas descartadas: %s", descartadas)
    return df[validas]


def _normalizar_nomes(df: pd.DataFrame, mapa: dict[str, str]) -> pd.DataFrame:
    df.columns = [c.strip() for c in df.columns]
    nao_mapeadas = [c for c in df.columns if c not in mapa]
    if nao_mapeadas:
        log.warning("Colunas não mapeadas: %s", nao_mapeadas)
    return df.rename(columns={k: v for k, v in mapa.items() if k in df.columns})


def _converter_datas_processos(df: pd.DataFrame) -> pd.DataFrame:
    for col in ("ajuizamento", "cadastro"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], format="%d/%m/%Y", errors="coerce")
    return df


def _converter_valor_financeiro(df: pd.DataFrame) -> pd.DataFrame:
    if "valor" not in df.columns:
        raise KeyError("Coluna 'valor' não encontrada no arquivo de processos.")
    df["valor"] = (
        df["valor"]
        .fillna("0")
        .astype(str)
        .str.strip()
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .pipe(pd.to_numeric, errors="coerce")
        .fillna(0.0)
    )
    return df


def _converter_demandas_tipos(df: pd.DataFrame) -> pd.DataFrame:
    """Converte datas e horas do Arquivo B."""
    for col in ("entrada", "conclusao"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], format="%d/%m/%Y", errors="coerce")
    if "horas" in df.columns:
        df["horas"] = (
            df["horas"]
            .str.strip()
            .str.replace(",", ".", regex=False)
            .pipe(pd.to_numeric, errors="coerce")
            .fillna(0.0)
        )
    return df


def _extrair_oab(df: pd.DataFrame) -> pd.DataFrame:
    """Extrai advogado_principal e oab_principal da coluna 'advogados'."""
    if "advogados" not in df.columns:
        df["advogado_principal"] = None
        df["oab_principal"]      = None
        return df
    extraido = df["advogados"].str.extract(
        r'(?i)Advogado[a-z]*:\s*(.*?)\s*\(OAB:\s*(.*?)\)',
        expand=True,
    )
    df["advogado_principal"] = extraido[0]
    df["oab_principal"]      = extraido[1]
    return df


def _agregar_por_pasta(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrupa o DataFrame pela coluna 'pasta'.
    valor = SUM (soma de todos os componentes financeiros do processo).
    ajuizamento = MIN (data mais antiga do litígio).
    """
    if "pasta" not in df.columns:
        raise KeyError("Coluna 'pasta' não encontrada após normalização.")
    if "processo" not in df.columns:
        raise KeyError("Coluna 'processo' não encontrada após normalização.")

    df["pasta"]    = df["pasta"].str.strip()
    df["processo"] = df["processo"].str.strip()

    colunas_excluir = {"pasta", "processo", "valor", "ajuizamento"}
    colunas_first   = [c for c in df.columns if c not in colunas_excluir]

    agg: dict = {
        "processo":    lambda s: ", ".join(sorted(s.dropna().astype(str).unique())),
        "valor":       "sum",   # soma: risco total acumulado por pasta
        "ajuizamento": "min",
    }
    agg.update({c: "first" for c in colunas_first})

    df_agg = df.groupby("pasta", as_index=False, sort=False).agg(agg)
    df_agg = df_agg.rename(columns={"processo": "processos_vinculados"})

    log.info("Agregação por pasta concluída | pastas_únicas=%d", len(df_agg))
    return df_agg


# =============================================================================
# CAPÍTULO 5 – NORMALIZAÇÃO DE PROCESSO E REGRAS DE CLASSIFICAÇÃO DE EVENTOS
# =============================================================================

def normalizar_processo_base(valor: str) -> str:
    """Remove sufixo /N do número do processo para chave de cruzamento uniforme."""
    if not valor or pd.isna(valor):
        return ""
    return str(valor).strip().split("/")[0]


# Padrões de evento por categoria (regex case-insensitive, avaliados em ordem)
_PAD_PERDA_FINANCEIRA = [
    r"\bRPV\b",
    r"requisição.*pequeno.*valor",
    r"precatório",
    r"depósito.*RPV",
    r"depósito.*precatório",
    r"ofício requisitório",
    r"expedição.*ofício",
    r"sequestro.*verba",
    r"bloqueio.*verba",
    r"pagamento.*realizado",
    r"pagamento.*efetuado",
]

_PAD_PERDA_FORTE = [
    r"acórdão.*desfavor[aá]vel",
    r"desfavor[aá]vel.*acórdão",
    r"intimação.*acórdão.*desfavor[aá]vel",
    r"intimação de acórdão\s*-\s*desfavor",
]

_PAD_GANHO_FORTE = [
    r"acórdão.*favor[aá]vel",
    r"favor[aá]vel.*acórdão",
    r"intimação.*acórdão.*favor[aá]vel",
    r"intimação de acórdão\s*-\s*favor",
]

_PAD_PERDA_PROVAVEL = [
    r"sentença.*desfavor[aá]vel",
    r"desfavor[aá]vel.*sentença",
    r"intimação.*sentença.*desfavor[aá]vel",
    r"intimação de sentença\s*-\s*desfavor",
    r"decisão.*desfavor[aá]vel",
]

_PAD_GANHO_PROVAVEL = [
    r"sentença.*favor[aá]vel",
    r"favor[aá]vel.*sentença",
    r"intimação.*sentença.*favor[aá]vel",
    r"intimação de sentença\s*-\s*favor",
    r"decisão.*favor[aá]vel",
]

_PAD_TRANSITO = [
    r"trânsito.*julgado",
    r"transitou.*julgado",
    r"certificação.*trânsito",
    r"certidão.*trânsito",
]

# Hierarquia: índice menor = resultado mais forte/definitivo
HIERARQUIA_RESULTADO: list[str] = [
    "Perda financeira",    # 0
    "Perda definitiva",    # 1
    "Ganho definitivo",    # 2
    "Perda forte",         # 3
    "Ganho forte",         # 4
    "Perda provável",      # 5
    "Ganho provável",      # 6
    "Em andamento",        # 7
    "Indeterminado",       # 8
]

# Grupos para exibição / cálculo de KPIs
RESULTADOS_GANHO  = frozenset({"Ganho definitivo", "Ganho forte", "Ganho provável"})
RESULTADOS_PERDA  = frozenset({"Perda financeira", "Perda definitiva", "Perda forte", "Perda provável"})

# Ranking de qualidade do match (menor = melhor)
_NIVEL_MATCH_RANK: dict[str, int] = {"forte": 0, "medio": 1, "fraco": 2, "sem_match": 3}
RESULTADOS_FINAIS = RESULTADOS_GANHO | RESULTADOS_PERDA

# Cores para o dashboard
STATUS_CORES: dict[str, str] = {
    "Ganho definitivo": "#1A8A2E",
    "Ganho forte":      "#27AE60",
    "Ganho provável":   "#7DC95E",
    "Perda financeira": "#8B0000",
    "Perda definitiva": "#CC0000",
    "Perda forte":      "#E07B00",
    "Perda provável":   "#F5C518",
    "Em andamento":     "#ADB5BD",
    "Indeterminado":    "#6C757D",
}


def classificar_evento_demanda(texto: str) -> str | None:
    """
    Classifica um evento pelo texto da demanda.
    Retorna categoria ou None (evento sem classificação de resultado).
    Retorna '__transito__' para trânsito em julgado (contexto-dependente).
    """
    if not texto or pd.isna(texto):
        return None
    s = str(texto)

    def _m(pads: list[str]) -> bool:
        return any(re.search(p, s, re.IGNORECASE) for p in pads)

    if _m(_PAD_PERDA_FINANCEIRA):
        return "Perda financeira"
    if _m(_PAD_PERDA_FORTE):
        return "Perda forte"
    if _m(_PAD_GANHO_FORTE):
        return "Ganho forte"
    if _m(_PAD_PERDA_PROVAVEL):
        return "Perda provável"
    if _m(_PAD_GANHO_PROVAVEL):
        return "Ganho provável"
    if _m(_PAD_TRANSITO):
        return "__transito__"
    return None


def _forca(resultado: str) -> int:
    """Posição na hierarquia (menor = mais forte)."""
    try:
        return HIERARQUIA_RESULTADO.index(resultado)
    except ValueError:
        return len(HIERARQUIA_RESULTADO)


def _consolidar_resultado_pasta(
    eventos: list[tuple],
) -> tuple[str, dict]:
    """
    eventos: lista de (data_ref_str_ou_None, texto_demanda) em ordem cronológica.
    Retorna (resultado_final, metricas_qualidade).
    """
    FAVORAVEIS    = {"Ganho provável", "Ganho forte", "Ganho definitivo"}
    DESFAVORAVEIS = {"Perda provável", "Perda forte", "Perda definitiva", "Perda financeira"}

    resultados_acum  = []
    ultimo_substant  = None
    n_classificados  = 0
    n_transitos_sem  = 0

    for _, texto in eventos:
        c = classificar_evento_demanda(texto)
        if c is None:
            continue
        if c == "__transito__":
            if ultimo_substant in FAVORAVEIS:
                resultados_acum.append("Ganho definitivo")
            elif ultimo_substant in DESFAVORAVEIS:
                novo = "Perda financeira" if ultimo_substant == "Perda financeira" else "Perda definitiva"
                resultados_acum.append(novo)
            else:
                n_transitos_sem += 1
                resultados_acum.append("Indeterminado")
        else:
            resultados_acum.append(c)
            ultimo_substant = c
            n_classificados += 1

    if not resultados_acum:
        return "Em andamento", {"n_classificados": 0, "n_transitos_sem_evento": 0, "n_conflitantes": 0}

    tem_fav   = any(r in FAVORAVEIS   for r in resultados_acum)
    tem_desfav = any(r in DESFAVORAVEIS for r in resultados_acum)
    n_conflitantes = 1 if (tem_fav and tem_desfav) else 0

    melhor = min(resultados_acum, key=_forca)
    return melhor, {
        "n_classificados":        n_classificados,
        "n_transitos_sem_evento": n_transitos_sem,
        "n_conflitantes":         n_conflitantes,
    }


# =============================================================================
# CAPÍTULO 6 – CÁLCULO DE RESULTADO ECONÔMICO
# =============================================================================

def calcular_resultado_economico(
    df_dem: pd.DataFrame,
    df_proc: pd.DataFrame,
) -> pd.DataFrame:
    """
    Cruza demandas (classificação de eventos) com processos (catálogo de valor).

    Retorna DataFrame com uma linha por pasta, colunas:
      pasta, resultado, valor, tem_valor, nivel_match,
      n_classificados, n_transitos_sem_evento, n_conflitantes.
    """
    if df_dem.empty:
        return pd.DataFrame()

    df_dem = df_dem.copy()
    if "processo_base" not in df_dem.columns:
        col = "processo_orig" if "processo_orig" in df_dem.columns else None
        df_dem["processo_base"] = (
            df_dem[col].apply(normalizar_processo_base) if col else ""
        )

    # Construir tabelas de valor para lookup
    if not df_proc.empty and "valor" in df_proc.columns and "pasta" in df_proc.columns:
        df_p = df_proc.copy()

        # Quando o catálogo vem do banco (pastas_consolidadas), processo está em
        # processos_vinculados como string CSV; precisamos expandir para obter
        # (pasta, processo_base) individuais para o match forte.
        if "processo_base" not in df_p.columns and "processo" not in df_p.columns:
            if "processos_vinculados" in df_p.columns:
                linhas_exp = []
                for _, row in df_p.iterrows():
                    procs = [p.strip() for p in str(row.get("processos_vinculados") or "").split(",") if p.strip()]
                    if procs:
                        for p in procs:
                            r = row.to_dict()
                            r["processo_base"] = normalizar_processo_base(p)
                            linhas_exp.append(r)
                    else:
                        r = row.to_dict()
                        r["processo_base"] = ""
                        linhas_exp.append(r)
                df_p = pd.DataFrame(linhas_exp)
            else:
                df_p["processo_base"] = ""
        elif "processo_base" not in df_p.columns:
            col_p = "processo"
            df_p["processo_base"] = df_p[col_p].apply(normalizar_processo_base)

        vl_combo = df_p.groupby(["pasta", "processo_base"])["valor"].sum().reset_index()
        vl_proc  = df_p.groupby("processo_base")["valor"].sum().reset_index()
        vl_pasta = df_p.groupby("pasta")["valor"].sum().reset_index()
    else:
        vl_combo = pd.DataFrame(columns=["pasta", "processo_base", "valor"])
        vl_proc  = pd.DataFrame(columns=["processo_base", "valor"])
        vl_pasta = pd.DataFrame(columns=["pasta", "valor"])

    data_col = "entrada" if "entrada" in df_dem.columns else None

    # Classificar por (pasta, processo_base)
    linhas_pbase = []
    for (pasta, proc_base), grp in df_dem.groupby(["pasta", "processo_base"]):
        if data_col:
            grp = grp.sort_values(data_col, na_position="last")
            eventos = list(zip(grp[data_col].astype(str), grp.get("demanda", pd.Series(dtype=str))))
        else:
            eventos = [(None, t) for t in grp.get("demanda", pd.Series(dtype=str))]

        resultado, metricas = _consolidar_resultado_pasta(eventos)

        nivel = "sem_match"
        valor = 0.0
        if not vl_combo.empty:
            m = vl_combo[(vl_combo["pasta"] == pasta) & (vl_combo["processo_base"] == proc_base)]
            if not m.empty:
                valor = float(m["valor"].iloc[0])
                nivel = "forte"
        if nivel == "sem_match" and not vl_proc.empty and proc_base:
            m = vl_proc[vl_proc["processo_base"] == proc_base]
            if not m.empty:
                valor = float(m["valor"].iloc[0])
                nivel = "medio"
        if nivel == "sem_match" and not vl_pasta.empty:
            m = vl_pasta[vl_pasta["pasta"] == pasta]
            if not m.empty:
                valor = float(m["valor"].iloc[0])
                nivel = "fraco"

        linhas_pbase.append({
            "pasta":                  pasta,
            "processo_base":          proc_base,
            "resultado":              resultado,
            "valor":                  valor,
            "tem_valor":              int(nivel != "sem_match"),
            "nivel_match":            nivel,
            "n_classificados":        metricas["n_classificados"],
            "n_transitos_sem_evento": metricas["n_transitos_sem_evento"],
            "n_conflitantes":         metricas["n_conflitantes"],
        })

    if not linhas_pbase:
        return pd.DataFrame()

    df_pbase = pd.DataFrame(linhas_pbase)

    # Consolidar por pasta (uma pasta pode ter múltiplos processo_base)
    pasta_res   = (df_pbase.groupby("pasta")["resultado"]
                   .apply(lambda s: min(s.tolist(), key=_forca))
                   .reset_index(name="resultado"))
    pasta_val   = df_pbase.groupby("pasta")["valor"].sum().reset_index()
    pasta_tv    = (df_pbase.groupby("pasta")["tem_valor"].max() > 0).reset_index()
    pasta_metr  = df_pbase.groupby("pasta").agg(
        n_classificados=("n_classificados", "sum"),
        n_transitos_sem_evento=("n_transitos_sem_evento", "sum"),
        n_conflitantes=("n_conflitantes", "sum"),
    ).reset_index()
    # Melhor nível de match por pasta (forte > medio > fraco > sem_match)
    pasta_nivel = (
        df_pbase.groupby("pasta")["nivel_match"]
        .apply(lambda s: min(s.tolist(), key=lambda n: _NIVEL_MATCH_RANK.get(n, 99)))
        .reset_index(name="nivel_match")
    )

    df_final = (pasta_res
                .merge(pasta_val,   on="pasta", how="left")
                .merge(pasta_tv,    on="pasta", how="left")
                .merge(pasta_metr,  on="pasta", how="left")
                .merge(pasta_nivel, on="pasta", how="left"))
    df_final["valor"]     = df_final["valor"].fillna(0.0)
    df_final["tem_valor"] = df_final["tem_valor"].fillna(0).astype(int)

    log.info(
        "Resultado econômico | pastas=%d | classificadas=%d | sem_valor=%d",
        len(df_final),
        int((~df_final["resultado"].isin(["Em andamento", "Indeterminado"])).sum()),
        int((df_final["tem_valor"] == 0).sum()),
    )
    return df_final


def indicadores_qualidade(df_resultado: pd.DataFrame) -> dict:
    """Indicadores de cobertura e qualidade para exibição no dashboard."""
    if df_resultado.empty:
        return {}
    total = len(df_resultado)
    return {
        "total_pastas":           total,
        "pastas_classificadas":   int((df_resultado["resultado"].isin(RESULTADOS_FINAIS)).sum()),
        "pastas_com_valor":       int(df_resultado["tem_valor"].sum()),
        "pct_com_valor":          round(df_resultado["tem_valor"].sum() / total * 100, 1) if total else 0.0,
        "sem_valor_localizado":   int((df_resultado["tem_valor"] == 0).sum()),
        "transitos_sem_evento":   int(df_resultado["n_transitos_sem_evento"].sum()),
        "conflitantes":           int(df_resultado["n_conflitantes"].sum()),
        "indeterminados":         int((df_resultado["resultado"] == "Indeterminado").sum()),
        "em_andamento":           int((df_resultado["resultado"] == "Em andamento").sum()),
    }


# =============================================================================
# CAPÍTULO 7 – BANCO DE DADOS: SCHEMA E DDL
# =============================================================================

_DDL_PASTAS = """
CREATE TABLE IF NOT EXISTS pastas_consolidadas (
    pasta                       TEXT PRIMARY KEY,
    processos_vinculados        TEXT,
    valor                       REAL,
    ajuizamento                 TEXT,
    cadastro                    TEXT,
    classe                      TEXT,
    materia                     TEXT,
    assuntos                    TEXT,
    tribunal                    TEXT,
    unidade_judicial             TEXT,
    vara                        TEXT,
    polo_pge                    TEXT,
    qualificacao                TEXT,
    parte_representada          TEXT,
    documento_parte_rep         TEXT,
    parte_contraria             TEXT,
    documento                   TEXT,
    outras_partes_ativas        TEXT,
    outras_partes_passivas      TEXT,
    advogados                   TEXT,
    advogado_principal          TEXT,
    oab_principal               TEXT,
    ult_andamento_judicial       TEXT,
    data_do_andamento           TEXT,
    tramitacao                  TEXT,
    situacao                    TEXT,
    unidade                     TEXT,
    mesa                        TEXT,
    tipo_distribuicao           TEXT,
    num_dividas                 TEXT,
    soma_val_atualizados        TEXT,
    status_exito                TEXT,
    nucleo                      TEXT,
    data_ultima_atualizacao     TEXT,
    procurador                  TEXT,
    ult_demanda                 TEXT,
    data_ultima_demanda         TEXT,
    total_horas                 REAL
)
"""

_DDL_DEMANDAS = """
CREATE TABLE IF NOT EXISTS demandas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pasta           TEXT NOT NULL,
    processo_orig   TEXT,
    processo_base   TEXT,
    unidade         TEXT,
    procurador      TEXT,
    demanda         TEXT,
    qualificacao    TEXT,
    materia         TEXT,
    assuntos        TEXT,
    tribunal        TEXT,
    origem          TEXT,
    status_demanda  TEXT,
    entrada         TEXT,
    conclusao       TEXT,
    horas           REAL,
    publicou        TEXT,
    nucleo          TEXT,
    competencia     TEXT,
    data_upload     TEXT
)
"""

_DDL_RESULTADO_ECONOMICO = """
CREATE TABLE IF NOT EXISTS resultado_economico (
    pasta                   TEXT NOT NULL,
    resultado               TEXT,
    valor                   REAL,
    tem_valor               INTEGER DEFAULT 0,
    nivel_match             TEXT,
    n_classificados         INTEGER DEFAULT 0,
    n_transitos_sem_evento  INTEGER DEFAULT 0,
    n_conflitantes          INTEGER DEFAULT 0,
    nucleo                  TEXT,
    data_calculo            TEXT,
    PRIMARY KEY (pasta)
)
"""

_DDL_CONTROLE = """
CREATE TABLE IF NOT EXISTS controle_uploads (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    data_upload                     TEXT    NOT NULL,
    nome_arquivo                    TEXT    NOT NULL,
    quantidade_registros_processados INTEGER NOT NULL,
    nucleo                          TEXT,
    competencia                     TEXT,
    tipo_arquivo                    TEXT
)
"""

_COLUNAS_SCHEMA_PASTA = (
    "pasta", "processos_vinculados", "valor", "ajuizamento", "cadastro", "classe",
    "materia", "assuntos", "tribunal", "unidade_judicial", "vara",
    "polo_pge", "qualificacao", "parte_representada", "documento_parte_rep",
    "parte_contraria", "documento", "outras_partes_ativas",
    "outras_partes_passivas", "advogados", "advogado_principal", "oab_principal",
    "ult_andamento_judicial",
    "data_do_andamento", "tramitacao", "situacao", "unidade", "mesa",
    "tipo_distribuicao", "num_dividas", "soma_val_atualizados",
    "status_exito", "nucleo", "data_ultima_atualizacao",
    "procurador", "ult_demanda", "data_ultima_demanda", "total_horas",
)

_COLUNAS_SCHEMA_DEM = (
    "pasta", "processo_orig", "processo_base", "unidade", "procurador",
    "demanda", "qualificacao", "materia", "assuntos", "tribunal",
    "origem", "status_demanda", "entrada", "conclusao", "horas",
    "publicou", "nucleo", "competencia", "data_upload",
)

_COLUNAS_ATUALIZAVEIS = (
    "processos_vinculados",
    "valor",
    "tramitacao",
    "situacao",
    "status_exito",
    "nucleo",
    "data_ultima_atualizacao",
)

_COLUNAS_RESULTADO_ECO = (
    "pasta", "resultado", "valor", "tem_valor", "nivel_match",
    "n_classificados", "n_transitos_sem_evento", "n_conflitantes",
    "nucleo", "data_calculo",
)


def _garantir_schema(cur) -> None:
    """Cria todas as tabelas e adiciona colunas novas (operação não-destrutiva)."""
    cur.execute(_DDL_PASTAS)
    cur.execute(_DDL_DEMANDAS)
    cur.execute(_DDL_RESULTADO_ECONOMICO)
    cur.execute(_DDL_CONTROLE)
    _col = _db.add_column_if_not_exists
    _col(cur, "pastas_consolidadas", "processos_vinculados", "TEXT")
    _col(cur, "pastas_consolidadas", "nucleo",               "TEXT")
    _col(cur, "pastas_consolidadas", "procurador",           "TEXT")
    _col(cur, "pastas_consolidadas", "ult_demanda",          "TEXT")
    _col(cur, "pastas_consolidadas", "data_ultima_demanda",  "TEXT")
    _col(cur, "pastas_consolidadas", "total_horas",          "REAL")
    _col(cur, "pastas_consolidadas", "advogado_principal",   "TEXT")
    _col(cur, "pastas_consolidadas", "oab_principal",        "TEXT")
    _col(cur, "demandas",            "competencia",          "TEXT")
    _col(cur, "controle_uploads",    "nucleo",               "TEXT")
    _col(cur, "controle_uploads",    "competencia",          "TEXT")
    _col(cur, "controle_uploads",    "tipo_arquivo",         "TEXT")


# =============================================================================
# CAPÍTULO 8 – PREPARAÇÃO DE DATAFRAMES PARA PERSISTÊNCIA
# =============================================================================

def _preparar_df_pastas(df: pd.DataFrame, nome_nucleo: str | None) -> pd.DataFrame:
    df = df.copy()
    for col in ("ajuizamento", "cadastro"):
        if col in df.columns and pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d")
    df["data_ultima_atualizacao"] = datetime.now().isoformat(timespec="seconds")
    if nome_nucleo:
        df["nucleo"] = nome_nucleo
    return df.where(pd.notnull(df), other=None)


def _normalizar_competencia(s: str | None) -> str | None:
    """
    Converte string de competência para formato canônico YYYY-MM.
    Aceita: "MM/AAAA", "YYYY-MM", "YYYY-M". Retorna None se vazio/inválido.
    """
    if not s:
        return None
    s = s.strip()
    m = re.fullmatch(r"(\d{1,2})/(\d{4})", s)          # MM/AAAA (entrada do usuário)
    if m:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", s)           # YYYY-MM / YYYY-M (pandas Period)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    log.warning("Formato de competência não reconhecido: %r — mantido como está.", s)
    return s


def _inferir_competencia(df_dem: pd.DataFrame) -> str | None:
    if "entrada" not in df_dem.columns:
        return None
    ent = df_dem["entrada"]
    if not pd.api.types.is_datetime64_any_dtype(ent):
        ent = pd.to_datetime(ent, errors="coerce")
    periodos = ent.dt.to_period("M").dropna()
    if periodos.empty:
        return None
    return _normalizar_competencia(str(periodos.mode().iloc[0]))


def _preparar_df_demandas(
    df: pd.DataFrame,
    nome_nucleo: str | None,
    competencia: str | None,
) -> pd.DataFrame:
    df = df.copy()
    for col in ("entrada", "conclusao"):
        if col in df.columns and pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d")
    df["data_upload"] = datetime.now().isoformat(timespec="seconds")
    if nome_nucleo:
        df["nucleo"] = nome_nucleo
    if competencia:
        df["competencia"] = competencia
    return df.where(pd.notnull(df), other=None)


def _merge_processos_vinculados(df_pasta: pd.DataFrame, con) -> pd.DataFrame:
    """Mescla processos_vinculados com os já persistidos no banco."""
    if "processos_vinculados" not in df_pasta.columns:
        return df_pasta
    try:
        existentes = _db.read_sql(
            "SELECT pasta, processos_vinculados FROM pastas_consolidadas", con
        )
    except Exception:
        return df_pasta

    if existentes.empty:
        return df_pasta

    mapa_existente = dict(
        zip(existentes["pasta"], existentes["processos_vinculados"].fillna(""))
    )

    def _merge_linha(row) -> str:
        novos   = {p.strip() for p in str(row.get("processos_vinculados") or "").split(",") if p.strip()}
        antigos = {p.strip() for p in str(mapa_existente.get(row["pasta"], "")).split(",")  if p.strip()}
        return ", ".join(sorted(novos | antigos))

    df_pasta = df_pasta.copy()
    df_pasta["processos_vinculados"] = df_pasta.apply(_merge_linha, axis=1)
    return df_pasta


def _derivar_campos_demandas(df_dem: pd.DataFrame) -> pd.DataFrame:
    """Deriva procurador atual, última demanda e total de horas por pasta."""
    if "pasta" not in df_dem.columns:
        raise KeyError("Coluna 'pasta' não encontrada no DataFrame de demandas.")

    conclusao_col = "conclusao"
    if conclusao_col in df_dem.columns:
        df_sorted = df_dem.sort_values(conclusao_col, ascending=False, na_position="last")
    else:
        df_sorted = df_dem

    mais_recente = df_sorted.groupby("pasta", sort=False).first().reset_index()

    if "horas" in df_dem.columns:
        total_horas = df_dem.groupby("pasta")["horas"].sum().reset_index(name="total_horas")
    else:
        total_horas = pd.DataFrame(columns=["pasta", "total_horas"])

    df_deriv = mais_recente[["pasta"]].copy()
    df_deriv["ult_demanda"] = (
        mais_recente["demanda"].values if "demanda" in mais_recente.columns else None
    )

    if conclusao_col in mais_recente.columns:
        col_conc = mais_recente[conclusao_col]
        if pd.api.types.is_datetime64_any_dtype(col_conc):
            df_deriv["data_ultima_demanda"] = col_conc.dt.strftime("%Y-%m-%d").values
        else:
            df_deriv["data_ultima_demanda"] = pd.to_datetime(col_conc, errors="coerce").dt.strftime("%Y-%m-%d").values
    else:
        df_deriv["data_ultima_demanda"] = None

    df_deriv["procurador"] = (
        mais_recente["procurador"].values if "procurador" in mais_recente.columns else None
    )
    df_deriv = df_deriv.merge(total_horas, on="pasta", how="left")
    df_deriv["total_horas"] = df_deriv.get("total_horas", pd.Series(dtype=float)).fillna(0.0)
    df_deriv = df_deriv.where(pd.notnull(df_deriv), other=None)

    log.info("Derivação concluída | pastas_com_demandas=%d", len(df_deriv))
    return df_deriv


# =============================================================================
# CAPÍTULO 9 – PERSISTÊNCIA
# =============================================================================

def _recalcular_e_persistir_resultado(
    nucleo: str | None,
    con,
    cur,
    ts: str,
) -> int:
    """
    Recarrega o histórico completo de demandas e processos do banco, recalcula
    resultado_economico e atualiza status_exito em pastas_consolidadas.
    Retorna o número de pastas calculadas.
    Pré-condição: con.commit() deve ter sido chamado antes para tornar os dados visíveis.
    """
    _p = _db.ph()

    if nucleo:
        df_dem_hist = _db.read_sql(
            f"SELECT * FROM demandas WHERE nucleo = {_p}", con, params=(nucleo,)
        )
        df_proc_hist = _db.read_sql(
            f"SELECT pasta, processos_vinculados, valor FROM pastas_consolidadas WHERE nucleo = {_p}",
            con, params=(nucleo,),
        )
    else:
        df_dem_hist  = _db.read_sql("SELECT * FROM demandas WHERE nucleo IS NULL", con)
        df_proc_hist = _db.read_sql(
            "SELECT pasta, processos_vinculados, valor FROM pastas_consolidadas WHERE nucleo IS NULL", con
        )

    for c in ("entrada", "conclusao"):
        if c in df_dem_hist.columns:
            df_dem_hist[c] = pd.to_datetime(df_dem_hist[c], errors="coerce")

    df_resultado = calcular_resultado_economico(df_dem_hist, df_proc_hist)

    if df_resultado.empty:
        log.info("Nenhum resultado econômico a persistir.")
        return 0

    df_resultado["nucleo"]       = nucleo
    df_resultado["data_calculo"] = ts

    if nucleo:
        cur.execute(f"DELETE FROM resultado_economico WHERE nucleo = {_p}", (nucleo,))
    else:
        cur.execute("DELETE FROM resultado_economico WHERE nucleo IS NULL")

    cols_res     = [c for c in _COLUNAS_RESULTADO_ECO if c in df_resultado.columns]
    placeh_res   = ", ".join([_p] * len(cols_res))
    cols_res_str = ", ".join(cols_res)
    sql_ins_res  = f"INSERT OR REPLACE INTO resultado_economico ({cols_res_str}) VALUES ({placeh_res})"
    registros_res = [tuple(row[c] for c in cols_res) for _, row in df_resultado[cols_res].iterrows()]
    _db.executemany(cur, sql_ins_res, registros_res)
    log.info("INSERT resultado_economico | pastas=%d", len(registros_res))

    sql_upd_status = f"UPDATE pastas_consolidadas SET status_exito = {_p} WHERE pasta = {_p}"
    status_updates = [(row["resultado"], row["pasta"]) for _, row in df_resultado.iterrows()]
    _db.executemany(cur, sql_upd_status, status_updates)
    log.info("UPDATE status_exito | pastas=%d", len(status_updates))

    return len(registros_res)


def salvar_no_banco(
    df_pasta: pd.DataFrame,
    df_dem: pd.DataFrame,
    df_deriv: pd.DataFrame,
    nome_arquivo_proc: str | Path,
    nome_arquivo_dem: str | Path,
    nome_banco: str = "jurimetria_pge.db",
    nucleo: str | None = None,
    competencia: str | None = None,
) -> None:
    """
    Persiste pastas e demandas, computa resultado_economico e atualiza status_exito.
    competencia é obrigatória para o DELETE idempotente de demandas.
    """
    if competencia is None:
        raise ValueError("'competencia' é obrigatória. Informe o período (ex.: '2026-05').")

    ts = datetime.now().isoformat(timespec="seconds")
    _p = _db.ph()

    # DELETE idempotente: apaga apenas demandas da competência + nucleo sendo reprocessados
    if nucleo is None:
        sql_del_dem = f"DELETE FROM demandas WHERE competencia = {_p} AND nucleo IS NULL"
        del_params  = (competencia,)
    else:
        sql_del_dem = f"DELETE FROM demandas WHERE competencia = {_p} AND nucleo = {_p}"
        del_params  = (competencia, nucleo)

    sql_ins_ctrl = (
        f"INSERT INTO controle_uploads "
        f"(data_upload, nome_arquivo, quantidade_registros_processados, nucleo, competencia, tipo_arquivo) "
        f"VALUES ({_p}, {_p}, {_p}, {_p}, {_p}, {_p})"
    )
    sql_update_deriv = (
        f"UPDATE pastas_consolidadas "
        f"SET procurador = {_p}, ult_demanda = {_p}, data_ultima_demanda = {_p}, "
        f"    total_horas = {_p}, data_ultima_atualizacao = {_p} "
        f"WHERE pasta = {_p}"
    )

    con = _db.connect()
    try:
        cur = con.cursor()
        _garantir_schema(cur)

        # Mescla processos_vinculados com existentes
        df_pasta = _merge_processos_vinculados(df_pasta, con)

        # UPSERT pastas_consolidadas
        cols_pasta   = [c for c in _COLUNAS_SCHEMA_PASTA if c in df_pasta.columns]
        cols_str     = ", ".join(cols_pasta)
        placeh_str   = ", ".join([_p] * len(cols_pasta))
        atualizacoes = ", ".join(
            f"{c} = excluded.{c}" for c in _COLUNAS_ATUALIZAVEIS if c in cols_pasta
        )
        sql_upsert = (
            f"INSERT INTO pastas_consolidadas ({cols_str}) "
            f"VALUES ({placeh_str}) "
            f"ON CONFLICT(pasta) DO UPDATE SET {atualizacoes}"
        )
        registros_pasta = [tuple(r) for r in df_pasta[cols_pasta].itertuples(index=False, name=None)]
        _db.executemany(cur, sql_upsert, registros_pasta)
        log.info("UPSERT pastas | registros=%d", len(registros_pasta))

        # DELETE + INSERT demandas (idempotente por competencia)
        cur.execute(sql_del_dem, del_params)
        cols_dem     = [c for c in _COLUNAS_SCHEMA_DEM if c in df_dem.columns]
        placeh_dem   = ", ".join([_p] * len(cols_dem))
        cols_dem_str = ", ".join(cols_dem)
        sql_ins_dem  = f"INSERT INTO demandas ({cols_dem_str}) VALUES ({placeh_dem})"
        registros_dem = [tuple(r) for r in df_dem[cols_dem].itertuples(index=False, name=None)]
        _db.executemany(cur, sql_ins_dem, registros_dem)
        log.info("INSERT demandas | registros=%d", len(registros_dem))

        # UPDATE campos derivados das demandas em pastas_consolidadas
        update_records = [
            (
                row["procurador"],
                row["ult_demanda"],
                row["data_ultima_demanda"],
                row.get("total_horas"),
                ts,
                row["pasta"],
            )
            for _, row in df_deriv.iterrows()
        ]
        _db.executemany(cur, sql_update_deriv, update_records)
        log.info("UPDATE derivado | pastas=%d", len(update_records))

        # Recomputa resultado_economico usando histórico completo do banco
        con.commit()  # torna dados recém inseridos visíveis para o recálculo
        _recalcular_e_persistir_resultado(nucleo, con, cur, ts)

        # Controle de uploads
        cur.execute(sql_ins_ctrl, (ts, str(nome_arquivo_proc), len(registros_pasta), nucleo, competencia, "completo"))
        cur.execute(sql_ins_ctrl, (ts, str(nome_arquivo_dem),  len(registros_dem),   nucleo, competencia, "completo"))
        con.commit()
        log.info("Transação concluída com sucesso.")
    except Exception:
        con.rollback()
        log.exception("Erro ao salvar no banco. Transação revertida.")
        raise
    finally:
        con.close()


# =============================================================================
# CAPÍTULO 10 – FUNÇÕES PÚBLICAS DA PIPELINE
# =============================================================================

def processar_processos(caminho_arquivo: str | Path) -> pd.DataFrame:
    """Pipeline do Arquivo A (Processos), agregando por Pasta."""
    log.info("=== PIPELINE PROCESSOS | arquivo=%s ===", Path(caminho_arquivo).name)
    df = _ler_csv_bruto(caminho_arquivo)
    df = _descartar_colunas_espurias(df)
    df = _normalizar_nomes(df, MAPA_COLUNAS)
    df = _converter_datas_processos(df)
    df = _converter_valor_financeiro(df)
    df = _extrair_oab(df)
    df = _agregar_por_pasta(df)
    df["status_exito"] = "Em andamento"  # será sobrescrito pelo resultado_economico
    log.info("Pipeline processos concluída | pastas=%d", len(df))
    return df


def processar_demandas(
    caminho_arquivo: str | Path,
    competencia: str | None = None,
) -> pd.DataFrame:
    """Pipeline do Arquivo B (Demandas). competencia inferida de Entrada se omitida."""
    log.info("=== PIPELINE DEMANDAS | arquivo=%s ===", Path(caminho_arquivo).name)
    df = _ler_csv_bruto(caminho_arquivo)
    df = _descartar_colunas_espurias(df)
    df = _normalizar_nomes(df, MAPA_COLUNAS_DEMANDAS)

    if "processo_orig" not in df.columns:
        raise KeyError("Coluna 'Processo' não encontrada no arquivo de demandas.")
    if "pasta" not in df.columns:
        raise KeyError("Coluna 'Pasta' não encontrada no arquivo de demandas.")

    df["processo_orig"] = df["processo_orig"].str.strip()
    df["pasta"]         = df["pasta"].str.strip()
    df["processo_base"] = df["processo_orig"].apply(normalizar_processo_base)
    df = _converter_demandas_tipos(df)

    comp = _normalizar_competencia(competencia) or _inferir_competencia(df)
    df["competencia"] = comp

    log.info("Pipeline demandas concluída | linhas=%d | competencia=%s", len(df), comp)
    return df


def ingerir_upload_bytes(
    bytes_processos: bytes,
    bytes_demandas: bytes,
    nome_arquivo_proc: str,
    nome_arquivo_dem: str,
    nome_nucleo: str,
    competencia: str | None = None,
) -> tuple[int, int]:
    """
    Ponto de entrada para upload via dashboard Streamlit.
    Aceita bytes lidos dos file uploaders.
    Retorna (qtd_pastas, qtd_demandas).
    """
    # Processos
    df = _ler_csv_bytes(bytes_processos, nome=nome_arquivo_proc)
    df = _descartar_colunas_espurias(df)
    df = _normalizar_nomes(df, MAPA_COLUNAS)
    df = _converter_datas_processos(df)
    df = _converter_valor_financeiro(df)
    df = _extrair_oab(df)
    df = _agregar_por_pasta(df)
    df["status_exito"] = "Em andamento"
    df_pasta_db = _preparar_df_pastas(df, nome_nucleo)

    # Demandas
    df_dem = _ler_csv_bytes(bytes_demandas, nome=nome_arquivo_dem)
    df_dem = _descartar_colunas_espurias(df_dem)
    df_dem = _normalizar_nomes(df_dem, MAPA_COLUNAS_DEMANDAS)

    if "processo_orig" not in df_dem.columns:
        raise KeyError("Coluna 'Processo' não encontrada no arquivo de demandas.")
    if "pasta" not in df_dem.columns:
        raise KeyError("Coluna 'Pasta' não encontrada no arquivo de demandas.")

    df_dem["processo_orig"] = df_dem["processo_orig"].str.strip()
    df_dem["pasta"]         = df_dem["pasta"].str.strip()
    df_dem["processo_base"] = df_dem["processo_orig"].apply(normalizar_processo_base)
    df_dem = _converter_demandas_tipos(df_dem)

    comp = _normalizar_competencia(competencia) or _inferir_competencia(df_dem)
    df_deriv   = _derivar_campos_demandas(df_dem)
    df_dem_db  = _preparar_df_demandas(df_dem, nome_nucleo, comp)

    salvar_no_banco(
        df_pasta_db, df_dem_db, df_deriv,
        nome_arquivo_proc, nome_arquivo_dem,
        nucleo=nome_nucleo,
        competencia=comp,
    )
    return len(df_pasta_db), len(df_dem_db)


def ingerir_somente_processos(
    bytes_processos: bytes,
    nome_arquivo: str,
    nome_nucleo: str,
    competencia: str | None = None,
) -> int:
    """
    Ingere apenas o Relatório de Processos: UPSERT no catálogo de pastas e
    recalcula resultado_economico cruzando com TODAS as demandas históricas do banco.
    Não apaga demandas. Retorna a quantidade de pastas processadas.
    """
    log.info("=== PIPELINE SOMENTE PROCESSOS | arquivo=%s ===", nome_arquivo)

    df = _ler_csv_bytes(bytes_processos, nome=nome_arquivo)
    df = _descartar_colunas_espurias(df)
    df = _normalizar_nomes(df, MAPA_COLUNAS)
    df = _converter_datas_processos(df)
    df = _converter_valor_financeiro(df)
    df = _extrair_oab(df)
    df = _agregar_por_pasta(df)
    df["status_exito"] = "Em andamento"
    df_pasta_db = _preparar_df_pastas(df, nome_nucleo)

    comp = _normalizar_competencia(competencia)  # opcional; apenas para registro
    ts   = datetime.now().isoformat(timespec="seconds")
    _p   = _db.ph()

    con = _db.connect()
    try:
        cur = con.cursor()
        _garantir_schema(cur)

        df_pasta_db = _merge_processos_vinculados(df_pasta_db, con)

        cols_pasta   = [c for c in _COLUNAS_SCHEMA_PASTA if c in df_pasta_db.columns]
        cols_str     = ", ".join(cols_pasta)
        placeh_str   = ", ".join([_p] * len(cols_pasta))
        atualizacoes = ", ".join(f"{c} = excluded.{c}" for c in _COLUNAS_ATUALIZAVEIS if c in cols_pasta)
        sql_upsert   = (
            f"INSERT INTO pastas_consolidadas ({cols_str}) "
            f"VALUES ({placeh_str}) "
            f"ON CONFLICT(pasta) DO UPDATE SET {atualizacoes}"
        )
        registros_pasta = [tuple(r) for r in df_pasta_db[cols_pasta].itertuples(index=False, name=None)]
        _db.executemany(cur, sql_upsert, registros_pasta)
        log.info("UPSERT pastas (somente processos) | registros=%d", len(registros_pasta))

        cur.execute(
            f"INSERT INTO controle_uploads "
            f"(data_upload, nome_arquivo, quantidade_registros_processados, nucleo, competencia, tipo_arquivo) "
            f"VALUES ({_p}, {_p}, {_p}, {_p}, {_p}, {_p})",
            (ts, nome_arquivo, len(registros_pasta), nome_nucleo, comp, "processos"),
        )

        con.commit()
        _recalcular_e_persistir_resultado(nome_nucleo, con, cur, ts)
        con.commit()
        log.info("Transação somente processos concluída.")
    except Exception:
        con.rollback()
        log.exception("Erro ao salvar processos. Transação revertida.")
        raise
    finally:
        con.close()

    return len(registros_pasta)


def ingerir_somente_demandas(
    bytes_demandas: bytes,
    nome_arquivo: str,
    nome_nucleo: str,
    competencia: str | None = None,
) -> int:
    """
    Ingere apenas o Relatório de Demandas: DELETE idempotente por (competencia, nucleo),
    INSERT, atualiza campos derivados e recalcula resultado_economico com catálogo histórico.
    Retorna a quantidade de demandas processadas.
    """
    log.info("=== PIPELINE SOMENTE DEMANDAS | arquivo=%s ===", nome_arquivo)

    df_dem = _ler_csv_bytes(bytes_demandas, nome=nome_arquivo)
    df_dem = _descartar_colunas_espurias(df_dem)
    df_dem = _normalizar_nomes(df_dem, MAPA_COLUNAS_DEMANDAS)

    if "processo_orig" not in df_dem.columns:
        raise KeyError("Coluna 'Processo' não encontrada no arquivo de demandas.")
    if "pasta" not in df_dem.columns:
        raise KeyError("Coluna 'Pasta' não encontrada no arquivo de demandas.")

    df_dem["processo_orig"] = df_dem["processo_orig"].str.strip()
    df_dem["pasta"]         = df_dem["pasta"].str.strip()
    df_dem["processo_base"] = df_dem["processo_orig"].apply(normalizar_processo_base)
    df_dem = _converter_demandas_tipos(df_dem)

    comp      = _normalizar_competencia(competencia) or _inferir_competencia(df_dem)
    df_deriv  = _derivar_campos_demandas(df_dem)
    df_dem_db = _preparar_df_demandas(df_dem, nome_nucleo, comp)

    ts = datetime.now().isoformat(timespec="seconds")
    _p = _db.ph()

    if nome_nucleo is None:
        sql_del_dem = f"DELETE FROM demandas WHERE competencia = {_p} AND nucleo IS NULL"
        del_params  = (comp,)
    else:
        sql_del_dem = f"DELETE FROM demandas WHERE competencia = {_p} AND nucleo = {_p}"
        del_params  = (comp, nome_nucleo)

    sql_update_deriv = (
        f"UPDATE pastas_consolidadas "
        f"SET procurador = {_p}, ult_demanda = {_p}, data_ultima_demanda = {_p}, "
        f"    total_horas = {_p}, data_ultima_atualizacao = {_p} "
        f"WHERE pasta = {_p}"
    )

    con = _db.connect()
    try:
        cur = con.cursor()
        _garantir_schema(cur)

        cur.execute(sql_del_dem, del_params)

        cols_dem      = [c for c in _COLUNAS_SCHEMA_DEM if c in df_dem_db.columns]
        placeh_dem    = ", ".join([_p] * len(cols_dem))
        cols_dem_str  = ", ".join(cols_dem)
        sql_ins_dem   = f"INSERT INTO demandas ({cols_dem_str}) VALUES ({placeh_dem})"
        registros_dem = [tuple(r) for r in df_dem_db[cols_dem].itertuples(index=False, name=None)]
        _db.executemany(cur, sql_ins_dem, registros_dem)
        log.info("INSERT demandas (somente demandas) | registros=%d", len(registros_dem))

        update_records = [
            (row["procurador"], row["ult_demanda"], row["data_ultima_demanda"],
             row.get("total_horas"), ts, row["pasta"])
            for _, row in df_deriv.iterrows()
        ]
        _db.executemany(cur, sql_update_deriv, update_records)
        log.info("UPDATE derivado | pastas=%d", len(update_records))

        cur.execute(
            f"INSERT INTO controle_uploads "
            f"(data_upload, nome_arquivo, quantidade_registros_processados, nucleo, competencia, tipo_arquivo) "
            f"VALUES ({_p}, {_p}, {_p}, {_p}, {_p}, {_p})",
            (ts, nome_arquivo, len(registros_dem), nome_nucleo, comp, "demandas"),
        )

        con.commit()
        _recalcular_e_persistir_resultado(nome_nucleo, con, cur, ts)
        con.commit()
        log.info("Transação somente demandas concluída.")
    except Exception:
        con.rollback()
        log.exception("Erro ao salvar demandas. Transação revertida.")
        raise
    finally:
        con.close()

    return len(registros_dem)


def processar_base_jurimetria(
    caminho_processos: str | Path,
    caminho_demandas: str | Path,
    nome_nucleo: str | None = None,
    competencia: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Pipeline completa via arquivo. Retorna (df_pastas, df_demandas, df_derivado)."""
    df_pasta = processar_processos(caminho_processos)
    df_dem   = processar_demandas(caminho_demandas, competencia=competencia)

    comp = _normalizar_competencia(competencia) or _inferir_competencia(df_dem)
    df_deriv    = _derivar_campos_demandas(df_dem)
    df_pasta_db = _preparar_df_pastas(df_pasta, nome_nucleo)
    df_dem_db   = _preparar_df_demandas(df_dem, nome_nucleo, comp)

    salvar_no_banco(
        df_pasta_db, df_dem_db, df_deriv,
        caminho_processos, caminho_demandas,
        nucleo=nome_nucleo,
        competencia=comp,
    )
    return df_pasta, df_dem, df_deriv


# =============================================================================
# CAPÍTULO 11 – BLOCO PRINCIPAL
# =============================================================================

if __name__ == "__main__":
    BASE = Path(__file__).parent / "Base de dados"

    CAMINHO_PROCESSOS = BASE / "Processos - Relatório Detalhado para Excel (Lista).txt"
    CAMINHO_DEMANDAS  = BASE / "Demandas por unidade e procurador para Excel (Lista).txt"
    NUCLEO            = None
    COMPETENCIA       = None   # ex.: "2026-05"

    df_pasta, df_dem, df_deriv = processar_base_jurimetria(
        CAMINHO_PROCESSOS,
        CAMINHO_DEMANDAS,
        nome_nucleo=NUCLEO,
        competencia=COMPETENCIA,
    )

    sep = "-" * 60
    print(f"\n{sep}")
    print("RESUMO DO PROCESSAMENTO")
    print(sep)
    print(f"Pastas únicas     : {len(df_pasta):>10,}")
    print(f"Valor total (R$)  : {df_pasta['valor'].sum():>15,.2f}")
    print(f"Demandas          : {len(df_dem):>10,}")
    print(f"Pastas c/ dem.    : {df_deriv['pasta'].nunique():>10,}")

    con = _db.connect()
    try:
        total_pasta = _db.read_sql("SELECT COUNT(*) AS total FROM pastas_consolidadas", con).iloc[0, 0]
        total_dem   = _db.read_sql("SELECT COUNT(*) AS total FROM demandas", con).iloc[0, 0]
        por_result  = _db.read_sql(
            "SELECT resultado, COUNT(*) AS qtd, ROUND(SUM(valor), 2) AS valor_total "
            "FROM resultado_economico GROUP BY resultado ORDER BY qtd DESC",
            con,
        )
        historico   = _db.read_sql(
            "SELECT id, data_upload, nome_arquivo, quantidade_registros_processados, competencia "
            "FROM controle_uploads ORDER BY id DESC LIMIT 6",
            con,
        )
    finally:
        con.close()

    print(f"\n{sep}")
    print(f"Total pastas no banco    : {total_pasta:,}")
    print(f"Total demandas no banco  : {total_dem:,}")
    print("\nResultado Econômico por categoria:")
    print(por_result.to_string(index=False))
    print("\nHistórico de uploads (últimos 6):")
    print(historico.to_string(index=False))
    print(sep)
