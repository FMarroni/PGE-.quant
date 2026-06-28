"""
pipeline_jurimetria.py
Pipeline de ingestão da base de dados de jurimetria - PGE/SP.
Aceita dois arquivos: Relatório de Processos + Relatório de Demandas.
A entidade principal agregadora é a Pasta (chave primária no Attus).
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
# CAPÍTULO 5 – AGREGAÇÃO POR PASTA
# =============================================================================

def _agregar_por_pasta(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrupa o DataFrame pela coluna 'pasta' (entidade principal do Attus).
    - processos_vinculados: números de processo únicos da pasta, separados por vírgula
    - valor: máximo entre os processos (risco financeiro real atualizado)
    - ajuizamento: data mais antiga (início real do litígio)
    - demais colunas: primeira ocorrência válida
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
        "valor":       "max",
        "ajuizamento": "min",
    }
    agg.update({c: "first" for c in colunas_first})

    df_agg = df.groupby("pasta", as_index=False, sort=False).agg(agg)
    df_agg = df_agg.rename(columns={"processo": "processos_vinculados"})

    log.info("Agregação por pasta concluída | pastas_únicas=%d", len(df_agg))
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
    deriva por pasta: ult_demanda, data_ultima_demanda, procurador,
    total_horas, status_exito.
    """
    if "pasta" not in df_dem.columns:
        raise KeyError("Coluna 'pasta' não encontrada no DataFrame de demandas.")

    df_sorted    = df_dem.sort_values("conclusao", ascending=False, na_position="last")
    mais_recente = df_sorted.groupby("pasta", sort=False).first().reset_index()

    if "horas" in df_dem.columns:
        total_horas = (
            df_dem.groupby("pasta")["horas"]
            .sum().reset_index(name="total_horas")
        )
    else:
        total_horas = pd.DataFrame(columns=["pasta", "total_horas"])

    if "demanda" in df_sorted.columns:
        status_por_pasta = (
            df_sorted.groupby("pasta", sort=False)["demanda"]
            .apply(lambda s: _classificar_exito_serie(s.tolist()))
            .reset_index(name="status_exito")
        )
    else:
        status_por_pasta = pd.DataFrame(columns=["pasta", "status_exito"])

    df_deriv = mais_recente[["pasta"]].copy()
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

    df_deriv = df_deriv.merge(total_horas,     on="pasta", how="left")
    df_deriv = df_deriv.merge(status_por_pasta, on="pasta", how="left")
    df_deriv["total_horas"]  = df_deriv.get("total_horas",  pd.Series(dtype=float)).fillna(0.0)
    df_deriv["status_exito"] = df_deriv.get("status_exito", pd.Series(dtype=str)).fillna("Em Andamento")
    df_deriv = df_deriv.where(pd.notnull(df_deriv), other=None)

    log.info("Derivação concluída | pastas_com_demandas=%d", len(df_deriv))
    return df_deriv


# =============================================================================
# CAPÍTULO 7 – BANCO DE DADOS: SCHEMA E UPSERT
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

_COLUNAS_SCHEMA_PASTA = (
    "pasta", "processos_vinculados", "valor", "ajuizamento", "cadastro", "classe",
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
    "pasta", "processo_orig", "processo_base", "unidade", "procurador",
    "demanda", "qualificacao", "materia", "assuntos", "tribunal",
    "origem", "status_demanda", "entrada", "conclusao", "horas",
    "publicou", "nucleo", "data_upload",
)

# status_exito não é atualizado pelo UPSERT do Arquivo A — vem da derivação do Arquivo B
_COLUNAS_ATUALIZAVEIS = (
    "processos_vinculados",
    "valor",
    "tramitacao",
    "situacao",
    "nucleo",
    "data_ultima_atualizacao",
)


def _preparar_df_pastas(df: pd.DataFrame, nome_nucleo: str | None) -> pd.DataFrame:
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


def _merge_processos_vinculados(df_pasta: pd.DataFrame, con) -> pd.DataFrame:
    """
    Para pastas já presentes no banco, mescla os processos_vinculados
    do upload com os existentes, preservando todos os números únicos.
    """
    if "processos_vinculados" not in df_pasta.columns:
        return df_pasta
    try:
        existentes = _db.read_sql(
            "SELECT pasta, processos_vinculados FROM pastas_consolidadas", con
        )
    except Exception:
        return df_pasta  # tabela ainda não existe

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


def salvar_no_banco(
    df_pasta: pd.DataFrame,
    df_dem: pd.DataFrame,
    df_deriv: pd.DataFrame,
    caminho_processos: str | Path,
    caminho_demandas: str | Path,
    nome_banco: str = "jurimetria_pge.db",
    nucleo: str | None = None,
) -> None:
    """
    Persiste pastas e demandas no SQLite e aplica a derivação cruzada.
    """
    ts = datetime.now().isoformat(timespec="seconds")
    _p = _db.ph()

    sql_del_dem  = f"DELETE FROM demandas WHERE nucleo = {_p}"
    sql_ins_ctrl = (
        f"INSERT INTO controle_uploads "
        f"(data_upload, nome_arquivo, quantidade_registros_processados, nucleo) "
        f"VALUES ({_p}, {_p}, {_p}, {_p})"
    )
    sql_update_deriv = (
        f"UPDATE pastas_consolidadas "
        f"SET procurador = {_p}, ult_demanda = {_p}, data_ultima_demanda = {_p}, "
        f"    total_horas = {_p}, status_exito = {_p}, data_ultima_atualizacao = {_p} "
        f"WHERE pasta = {_p}"
    )

    con = _db.connect()
    try:
        cur = con.cursor()
        cur.execute(_DDL_PASTAS)
        cur.execute(_DDL_DEMANDAS)
        cur.execute(_DDL_CONTROLE)
        for col, tipo in [
            ("processos_vinculados", "TEXT"),
            ("nucleo",               "TEXT"),
            ("procurador",           "TEXT"),
            ("ult_demanda",          "TEXT"),
            ("data_ultima_demanda",  "TEXT"),
            ("total_horas",          "REAL"),
        ]:
            _db.add_column_if_not_exists(cur, "pastas_consolidadas", col, tipo)
        _db.add_column_if_not_exists(cur, "controle_uploads", "nucleo", "TEXT")

        # Mescla processos_vinculados com os já persistidos antes do UPSERT
        df_pasta = _merge_processos_vinculados(df_pasta, con)

        cols_pasta  = [c for c in _COLUNAS_SCHEMA_PASTA if c in df_pasta.columns]
        df_pasta_db = df_pasta[cols_pasta]
        cols_str    = ", ".join(cols_pasta)
        placeh_str  = ", ".join([_p] * len(cols_pasta))
        atualizacoes = ", ".join(
            f"{c} = excluded.{c}" for c in _COLUNAS_ATUALIZAVEIS if c in cols_pasta
        )
        sql_upsert = (
            f"INSERT INTO pastas_consolidadas ({cols_str}) "
            f"VALUES ({placeh_str}) "
            f"ON CONFLICT(pasta) DO UPDATE SET {atualizacoes}"
        )
        registros_pasta = [tuple(r) for r in df_pasta_db.itertuples(index=False, name=None)]

        cols_dem    = [c for c in _COLUNAS_SCHEMA_DEM if c in df_dem.columns]
        df_dem_db   = df_dem[cols_dem]
        placeh_dem  = ", ".join([_p] * len(cols_dem))
        cols_dem_str = ", ".join(cols_dem)
        sql_ins_dem = f"INSERT INTO demandas ({cols_dem_str}) VALUES ({placeh_dem})"
        registros_dem = [tuple(r) for r in df_dem_db.itertuples(index=False, name=None)]

        update_records = [
            (
                row["procurador"],
                row["ult_demanda"],
                row["data_ultima_demanda"],
                row.get("total_horas"),
                row["status_exito"],
                ts,
                row["pasta"],
            )
            for _, row in df_deriv.iterrows()
        ]

        _db.executemany(cur, sql_upsert, registros_pasta)
        log.info("UPSERT pastas | registros=%d | banco=%s", len(registros_pasta), nome_banco)

        if nucleo:
            cur.execute(sql_del_dem, (nucleo,))
        _db.executemany(cur, sql_ins_dem, registros_dem)
        log.info("INSERT demandas | registros=%d", len(registros_dem))

        _db.executemany(cur, sql_update_deriv, update_records)
        log.info("UPDATE derivado | pastas_atualizadas=%d", len(update_records))

        cur.execute(sql_ins_ctrl, (ts, Path(caminho_processos).name, len(registros_pasta), nucleo))
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
    """Pipeline do Arquivo A (Processos), agregando por Pasta."""
    log.info("=== PIPELINE PROCESSOS | arquivo=%s ===", Path(caminho_arquivo).name)
    df = _ler_csv_bruto(caminho_arquivo)
    df = _descartar_colunas_espurias(df)
    df = _normalizar_nomes(df, MAPA_COLUNAS)
    df = _converter_datas_processos(df)
    df = _converter_valor_financeiro(df)
    df = _agregar_por_pasta(df)
    log.info("Pipeline processos concluída | pastas=%d", len(df))
    return df


def processar_demandas(caminho_arquivo: str | Path) -> pd.DataFrame:
    """Pipeline do Arquivo B (Demandas)."""
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
    df["processo_base"] = df["processo_orig"].str.split("/").str[0]  # referência histórica

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
    Retorna (df_pastas, df_demandas, df_derivado).
    """
    df_pasta = processar_processos(caminho_processos)
    df_dem   = processar_demandas(caminho_demandas)

    df_deriv = _derivar_campos_demandas(df_dem)

    df_pasta_db = _preparar_df_pastas(df_pasta, nome_nucleo)
    df_dem_db   = _preparar_df_demandas(df_dem, nome_nucleo)

    salvar_no_banco(
        df_pasta_db, df_dem_db, df_deriv,
        caminho_processos, caminho_demandas,
        nome_banco=nome_banco,
        nucleo=nome_nucleo,
    )
    return df_pasta, df_dem, df_deriv


# =============================================================================
# CAPÍTULO 9 – BLOCO PRINCIPAL
# =============================================================================

if __name__ == "__main__":
    BASE = Path(__file__).parent / "Base de dados"

    CAMINHO_PROCESSOS = BASE / "Processos - Relatório Detalhado para Excel (Lista).txt"
    CAMINHO_DEMANDAS  = BASE / "Demandas por unidade e procurador para Excel (Lista).txt"
    NOME_BANCO        = str(Path(__file__).parent / "jurimetria_pge.db")
    NUCLEO            = None  # defina ex: "Núcleo Trabalhista"

    df_pasta, df_dem, df_deriv = processar_base_jurimetria(
        CAMINHO_PROCESSOS,
        CAMINHO_DEMANDAS,
        nome_nucleo=NUCLEO,
        nome_banco=NOME_BANCO,
    )

    sep = "-" * 60
    print(f"\n{sep}")
    print("RESUMO DO PROCESSAMENTO")
    print(sep)
    print(f"Pastas únicas     : {len(df_pasta):>10,}")
    print(f"Valor total (R$)  : {df_pasta['valor'].sum():>15,.2f}")
    print(f"Demandas          : {len(df_dem):>10,}")
    print(f"Pastas c/ dem.    : {df_deriv['pasta'].nunique():>10,}")

    print("\nStatus de Êxito (derivado):")
    print(df_deriv["status_exito"].value_counts().to_string())

    print("\nAmostra de derivação (5 primeiros):")
    colunas_amostra = ["pasta", "procurador", "ult_demanda", "total_horas", "status_exito"]
    cols_ex = [c for c in colunas_amostra if c in df_deriv.columns]
    print(df_deriv[cols_ex].head().to_string(index=False))

    print(f"\n{sep}")
    print(f"Banco: {NOME_BANCO}")
    print(sep)

    con = _db.connect()
    try:
        total_pasta = _db.read_sql(
            "SELECT COUNT(*) AS total FROM pastas_consolidadas", con
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
            "FROM pastas_consolidadas GROUP BY status_exito ORDER BY qtd DESC",
            con,
        )
    finally:
        con.close()

    print(f"Total pastas no banco    : {total_pasta:,}")
    print(f"Total demandas no banco  : {total_dem:,}")
    print("\nHistórico de uploads (últimos 6):")
    print(historico.to_string(index=False))
    print("\nConsolidado por Status de Êxito:")
    print(por_status.to_string(index=False))
    print(sep)
