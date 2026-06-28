"""
pipeline_jurimetria.py
Pipeline de ingestão da base de dados de jurimetria - PGE/SP.
Aceita dois arquivos: Relatório de Processos + Relatório de Demandas.
"""

# =============================================================================
# CAPÍTULO 1 – IMPORTS E CONFIGURAÇÃO DE LOG
# =============================================================================
import logging
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

def _ler_csv_bruto(caminho_arquivo: str | Path) -> pd.DataFrame:
    """Lê CSV com suporte a múltiplos encodings e campos multi-linha."""
    caminho = Path(caminho_arquivo)
    if not caminho.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {caminho.resolve()}")

    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            df = pd.read_csv(
                caminho,
                sep=",",
                quotechar='"',
                engine="python",
                encoding=enc,
                dtype=str,
                keep_default_na=False,
                na_values=[],
            )
            log.info(
                "Arquivo lido | encoding=%s | linhas=%d | colunas=%d",
                enc, len(df), len(df.columns),
            )
            return df
        except UnicodeDecodeError:
            continue

    raise ValueError("Nenhum encoding suportado conseguiu ler o arquivo.")


# =============================================================================
# CAPÍTULO 3 – MAPEAMENTOS DE COLUNAS
# =============================================================================

# Arquivo A — Relatório de Processos (Últ. Andamento Judicial e Data do Andamento
# foram removidos da exportação; mantidos no schema do banco para dados históricos)
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
        .str.strip()
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .pipe(pd.to_numeric, errors="coerce")
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


# =============================================================================
# CAPÍTULO 5 – AGREGAÇÃO DE PROCESSOS
# =============================================================================

def _agregar_por_processo(df: pd.DataFrame) -> pd.DataFrame:
    if "processo" not in df.columns:
        raise KeyError("Coluna 'processo' não encontrada após normalização.")
    df["processo"] = df["processo"].str.strip()
    colunas_cat = [c for c in df.columns if c not in ("processo", "valor")]
    agg: dict = {"valor": "sum"}
    agg.update({c: "first" for c in colunas_cat})
    df_agg = df.groupby("processo", as_index=False, sort=False).agg(agg)
    log.info("Agregação concluída | processos_únicos=%d", len(df_agg))
    return df_agg


# =============================================================================
# CAPÍTULO 6 – REGRAS DE NEGÓCIO: STATUS DE ÊXITO
# =============================================================================

_REGRAS_EXITO: list[tuple[str, str]] = [
    ("Favorável ao Estado",               "Vitória"),
    ("Extinção sem julgamento do mérito", "Vitória"),
    ("Desfavorável ao Estado",            "Perda"),
]


def _classificar_exito_serie(demandas_list: list) -> str:
    """Itera do mais recente para o mais antigo e retorna o primeiro status identificado."""
    for dem in demandas_list:
        if not dem or pd.isna(dem):
            continue
        for frag, status in _REGRAS_EXITO:
            if frag in str(dem):
                return status
    return "Em Andamento"


def _derivar_campos_demandas(df_dem: pd.DataFrame) -> pd.DataFrame:
    """
    A partir do DataFrame de demandas (com conclusao como datetime),
    deriva por processo_base: ult_demanda, data_ultima_demanda, procurador,
    total_horas, status_exito.
    """
    df_sorted = df_dem.sort_values("conclusao", ascending=False, na_position="last")
    mais_recente = df_sorted.groupby("processo_base", sort=False).first().reset_index()

    if "horas" in df_dem.columns:
        total_horas = (
            df_dem.groupby("processo_base")["horas"]
            .sum().reset_index(name="total_horas")
        )
    else:
        total_horas = pd.DataFrame(columns=["processo_base", "total_horas"])

    if "demanda" in df_sorted.columns:
        status_por_proc = (
            df_sorted.groupby("processo_base", sort=False)["demanda"]
            .apply(lambda s: _classificar_exito_serie(s.tolist()))
            .reset_index(name="status_exito")
        )
    else:
        status_por_proc = pd.DataFrame(columns=["processo_base", "status_exito"])

    df_deriv = mais_recente[["processo_base"]].copy()
    df_deriv["ult_demanda"] = (
        mais_recente["demanda"].values if "demanda" in mais_recente.columns else None
    )
    df_deriv["data_ultima_demanda"] = (
        mais_recente["conclusao"].dt.strftime("%Y-%m-%d").values
        if "conclusao" in mais_recente.columns
        else None
    )
    df_deriv["procurador"] = (
        mais_recente["procurador"].values if "procurador" in mais_recente.columns else None
    )

    df_deriv = df_deriv.merge(total_horas,   on="processo_base", how="left")
    df_deriv = df_deriv.merge(status_por_proc, on="processo_base", how="left")
    df_deriv["total_horas"]  = df_deriv.get("total_horas",  pd.Series(dtype=float)).fillna(0.0)
    df_deriv["status_exito"] = df_deriv.get("status_exito", pd.Series(dtype=str)).fillna("Em Andamento")
    df_deriv = df_deriv.where(pd.notnull(df_deriv), other=None)

    log.info("Derivação concluída | processos_com_demandas=%d", len(df_deriv))
    return df_deriv


# =============================================================================
# CAPÍTULO 7 – BANCO DE DADOS: SCHEMA E UPSERT
# =============================================================================

_DDL_PROCESSOS = """
CREATE TABLE IF NOT EXISTS processos_consolidados (
    processo                    TEXT PRIMARY KEY,
    pasta                       TEXT,
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
    processo_base   TEXT NOT NULL,
    processo_orig   TEXT,
    pasta           TEXT,
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
    data_upload     TEXT
)
"""

_DDL_CONTROLE = """
CREATE TABLE IF NOT EXISTS controle_uploads (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    data_upload                     TEXT    NOT NULL,
    nome_arquivo                    TEXT    NOT NULL,
    quantidade_registros_processados INTEGER NOT NULL,
    nucleo                          TEXT
)
"""

_COLUNAS_SCHEMA_PROC = (
    "processo", "pasta", "valor", "ajuizamento", "cadastro", "classe",
    "materia", "assuntos", "tribunal", "unidade_judicial", "vara",
    "polo_pge", "qualificacao", "parte_representada", "documento_parte_rep",
    "parte_contraria", "documento", "outras_partes_ativas",
    "outras_partes_passivas", "advogados", "ult_andamento_judicial",
    "data_do_andamento", "tramitacao", "situacao", "unidade", "mesa",
    "tipo_distribuicao", "num_dividas", "soma_val_atualizados",
    "status_exito", "nucleo", "data_ultima_atualizacao",
    "procurador", "ult_demanda", "data_ultima_demanda", "total_horas",
)

_COLUNAS_SCHEMA_DEM = (
    "processo_base", "processo_orig", "pasta", "unidade", "procurador",
    "demanda", "qualificacao", "materia", "assuntos", "tribunal",
    "origem", "status_demanda", "entrada", "conclusao", "horas",
    "publicou", "nucleo", "data_upload",
)

# status_exito não atualizado pelo UPSERT do Arquivo A; vem da derivação do Arquivo B
_COLUNAS_ATUALIZAVEIS = (
    "valor",
    "tramitacao",
    "situacao",
    "nucleo",
    "data_ultima_atualizacao",
)


def _preparar_df_processos(df: pd.DataFrame, nome_nucleo: str | None) -> pd.DataFrame:
    df = df.copy()
    for col in ("ajuizamento", "cadastro"):
        if col in df.columns and pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d")
    df["data_ultima_atualizacao"] = datetime.now().isoformat(timespec="seconds")
    df["status_exito"] = "Em Andamento"
    if nome_nucleo:
        df["nucleo"] = nome_nucleo
    return df.where(pd.notnull(df), other=None)


def _preparar_df_demandas(df: pd.DataFrame, nome_nucleo: str | None) -> pd.DataFrame:
    df = df.copy()
    for col in ("entrada", "conclusao"):
        if col in df.columns and pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d")
    df["data_upload"] = datetime.now().isoformat(timespec="seconds")
    if nome_nucleo:
        df["nucleo"] = nome_nucleo
    return df.where(pd.notnull(df), other=None)


def salvar_no_banco(
    df_proc: pd.DataFrame,
    df_dem: pd.DataFrame,
    df_deriv: pd.DataFrame,
    caminho_processos: str | Path,
    caminho_demandas: str | Path,
    nome_banco: str = "jurimetria_pge.db",
    nucleo: str | None = None,
) -> None:
    """
    Persiste processos e demandas no SQLite e aplica a derivação cruzada.
    """
    ts = datetime.now().isoformat(timespec="seconds")

    # Processos
    cols_proc = [c for c in _COLUNAS_SCHEMA_PROC if c in df_proc.columns]
    df_proc_db = df_proc[cols_proc]
    cols_str   = ", ".join(cols_proc)
    placeh_str = ", ".join([_db.ph()] * len(cols_proc))
    atualizacoes = ", ".join(
        f"{c} = excluded.{c}" for c in _COLUNAS_ATUALIZAVEIS if c in cols_proc
    )
    sql_upsert = f"""
        INSERT INTO processos_consolidados ({cols_str})
        VALUES ({placeh_str})
        ON CONFLICT(processo) DO UPDATE SET
            {atualizacoes}
    """
    registros_proc = [tuple(r) for r in df_proc_db.itertuples(index=False, name=None)]

    # Demandas
    cols_dem = [c for c in _COLUNAS_SCHEMA_DEM if c in df_dem.columns]
    df_dem_db = df_dem[cols_dem]
    placeh_dem = ", ".join([_db.ph()] * len(cols_dem))
    cols_dem_str = ", ".join(cols_dem)
    sql_insert_dem = f"INSERT INTO demandas ({cols_dem_str}) VALUES ({placeh_dem})"
    registros_dem = [tuple(r) for r in df_dem_db.itertuples(index=False, name=None)]

    # Derivação
    _p = _db.ph()
    sql_update_deriv = (
        f"UPDATE processos_consolidados "
        f"SET procurador = {_p}, ult_demanda = {_p}, data_ultima_demanda = {_p}, "
        f"    total_horas = {_p}, status_exito = {_p}, data_ultima_atualizacao = {_p} "
        f"WHERE processo = {_p}"
    )
    sql_del_dem  = _db.adapt_sql("DELETE FROM demandas WHERE nucleo = ?")
    sql_ins_ctrl = _db.adapt_sql(
        "INSERT INTO controle_uploads "
        "(data_upload, nome_arquivo, quantidade_registros_processados, nucleo) VALUES (?, ?, ?, ?)"
    )
    update_records = [
        (
            row["procurador"],
            row["ult_demanda"],
            row["data_ultima_demanda"],
            row.get("total_horas"),
            row["status_exito"],
            ts,
            row["processo_base"],
        )
        for _, row in df_deriv.iterrows()
    ]

    db_label = str(nome_banco)
    con = _db.connect()
    try:
        cur = con.cursor()
        cur.execute(_db.adapt_sql(_DDL_PROCESSOS))
        cur.execute(_db.adapt_sql(_DDL_DEMANDAS))
        cur.execute(_db.adapt_sql(_DDL_CONTROLE))
        for col, tipo in [
            ("nucleo",              "TEXT"),
            ("procurador",          "TEXT"),
            ("ult_demanda",         "TEXT"),
            ("data_ultima_demanda", "TEXT"),
            ("total_horas",         "REAL"),
        ]:
            _db.add_column_if_not_exists(cur, "processos_consolidados", col, tipo)
        _db.add_column_if_not_exists(cur, "controle_uploads", "nucleo", "TEXT")

        _db.executemany(cur, sql_upsert, registros_proc)
        log.info("UPSERT processos | registros=%d | banco=%s", len(registros_proc), db_label)

        if nucleo:
            cur.execute(sql_del_dem, (nucleo,))
        _db.executemany(cur, sql_insert_dem, registros_dem)
        log.info("INSERT demandas | registros=%d", len(registros_dem))

        _db.executemany(cur, sql_update_deriv, update_records)
        log.info("UPDATE derivado | processos_atualizados=%d", len(update_records))

        cur.execute(sql_ins_ctrl, (ts, Path(caminho_processos).name, len(registros_proc), nucleo))
        cur.execute(sql_ins_ctrl, (ts, Path(caminho_demandas).name, len(registros_dem), nucleo))
        con.commit()
        log.info("Transação concluída com sucesso.")
    except Exception:
        con.rollback()
        log.exception("Erro ao salvar no banco. Transação revertida.")
        raise
    finally:
        con.close()


# =============================================================================
# CAPÍTULO 8 – FUNÇÕES PRINCIPAIS DA PIPELINE
# =============================================================================

def processar_processos(caminho_arquivo: str | Path) -> pd.DataFrame:
    """Pipeline do Arquivo A (Processos)."""
    log.info("=== PIPELINE PROCESSOS | arquivo=%s ===", Path(caminho_arquivo).name)
    df = _ler_csv_bruto(caminho_arquivo)
    df = _descartar_colunas_espurias(df)
    df = _normalizar_nomes(df, MAPA_COLUNAS)
    df = _converter_datas_processos(df)
    df = _converter_valor_financeiro(df)
    df = _agregar_por_processo(df)
    log.info("Pipeline processos concluída | processos=%d", len(df))
    return df


def processar_demandas(caminho_arquivo: str | Path) -> pd.DataFrame:
    """Pipeline do Arquivo B (Demandas)."""
    log.info("=== PIPELINE DEMANDAS | arquivo=%s ===", Path(caminho_arquivo).name)
    df = _ler_csv_bruto(caminho_arquivo)
    df = _descartar_colunas_espurias(df)
    df = _normalizar_nomes(df, MAPA_COLUNAS_DEMANDAS)

    if "processo_orig" not in df.columns:
        raise KeyError("Coluna 'Processo' não encontrada no arquivo de demandas.")
    df["processo_orig"] = df["processo_orig"].str.strip()
    df["processo_base"] = df["processo_orig"].str.split("/").str[0]

    df = _converter_demandas_tipos(df)
    log.info("Pipeline demandas concluída | linhas=%d", len(df))
    return df


def processar_base_jurimetria(
    caminho_processos: str | Path,
    caminho_demandas: str | Path,
    nome_nucleo: str | None = None,
    nome_banco: str = "jurimetria_pge.db",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Pipeline completa de ingestão.
    Retorna (df_processos, df_demandas, df_derivado).
    """
    df_proc = processar_processos(caminho_processos)
    df_dem  = processar_demandas(caminho_demandas)

    df_deriv = _derivar_campos_demandas(df_dem)

    df_proc_db = _preparar_df_processos(df_proc, nome_nucleo)
    df_dem_db  = _preparar_df_demandas(df_dem, nome_nucleo)

    salvar_no_banco(
        df_proc_db, df_dem_db, df_deriv,
        caminho_processos, caminho_demandas,
        nome_banco=nome_banco,
        nucleo=nome_nucleo,
    )
    return df_proc, df_dem, df_deriv


# =============================================================================
# CAPÍTULO 9 – BLOCO PRINCIPAL
# =============================================================================

if __name__ == "__main__":
    BASE = Path(__file__).parent / "Base de dados"

    CAMINHO_PROCESSOS = BASE / "Processos - Relatório Detalhado para Excel (Lista).txt"
    CAMINHO_DEMANDAS  = BASE / "Demandas por unidade e procurador para Excel (Lista).txt"
    NOME_BANCO        = str(Path(__file__).parent / "jurimetria_pge.db")
    NUCLEO            = None  # defina ex: "Núcleo Trabalhista"

    df_proc, df_dem, df_deriv = processar_base_jurimetria(
        CAMINHO_PROCESSOS,
        CAMINHO_DEMANDAS,
        nome_nucleo=NUCLEO,
        nome_banco=NOME_BANCO,
    )

    sep = "-" * 60
    print(f"\n{sep}")
    print("RESUMO DO PROCESSAMENTO")
    print(sep)
    print(f"Processos únicos  : {len(df_proc):>10,}")
    print(f"Valor total (R$)  : {df_proc['valor'].sum():>15,.2f}")
    print(f"Demandas          : {len(df_dem):>10,}")
    print(f"Processos c/ dem. : {df_deriv['processo_base'].nunique():>10,}")

    print(f"\nStatus de Êxito (derivado):")
    print(df_deriv["status_exito"].value_counts().to_string())

    print(f"\nAmostra de derivação (5 primeiros):")
    colunas_amostra = ["processo_base", "procurador", "ult_demanda", "total_horas", "status_exito"]
    cols_ex = [c for c in colunas_amostra if c in df_deriv.columns]
    print(df_deriv[cols_ex].head().to_string(index=False))

    print(f"\n{sep}")
    print(f"Banco: {NOME_BANCO}")
    print(sep)

    con = _db.connect()
    try:
        total_proc = _db.read_sql(
            "SELECT COUNT(*) AS total FROM processos_consolidados", con
        ).iloc[0, 0]
        total_dem = _db.read_sql(
            "SELECT COUNT(*) AS total FROM demandas", con
        ).iloc[0, 0]
        historico = _db.read_sql(
            "SELECT id, data_upload, nome_arquivo, quantidade_registros_processados "
            "FROM controle_uploads ORDER BY id DESC LIMIT 6",
            con,
        )
        por_status = _db.read_sql(
            "SELECT status_exito, COUNT(*) AS qtd, ROUND(SUM(valor), 2) AS valor_total "
            "FROM processos_consolidados GROUP BY status_exito ORDER BY qtd DESC",
            con,
        )
    finally:
        con.close()

    print(f"Total processos no banco : {total_proc:,}")
    print(f"Total demandas no banco  : {total_dem:,}")
    print("\nHistórico de uploads (últimos 6):")
    print(historico.to_string(index=False))
    print("\nConsolidado por Status de Êxito:")
    print(por_status.to_string(index=False))
    print(sep)
