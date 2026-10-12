"""Build data_sample/requetes311_sample.csv: a small, deterministic, stratified sample.

Needs the full raw table (run ingestion/load_311.py first). The sample is committed so CI and
reviewers can run the whole pipeline without the 840 MB download.

It is NOT a statistical sample: it deliberately over-represents rare edge cases so the dbt
tests and monitors have something to bite on. Do not compute analytics from it.

Determinism: rows are chosen by ordering on md5(row id || SEED), and the output is written in
the original file order, so the same source data always yields the same file.
Usage:  python ingestion/build_sample.py
"""
import os
from pathlib import Path

import duckdb

SEED = "montreal-311-sample-v1"
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("MONTREAL_311_DB", ROOT / "data" / "montreal_311.duckdb"))
OUT = ROOT / "data_sample" / "requetes311_sample.csv"

# (name, SQL selecting row ids `rid` from the numbered source table `r`)
def pick(where: str, n: int | None) -> str:
    limit = f"limit {n}" if n else ""
    return f"select rid from r where {where} order by md5(cast(rid as varchar) || '{SEED}') {limit}"


def build_strata(cols: str) -> list[tuple[str, str]]:
    base = "regexp_replace(ID_UNIQUE, '-[A-Z]$', '')"
    return [
        # Every row with an unexpected 20-/21- request id prefix (only 3 exist).
        ("unexpected_id_prefix", pick("ID_UNIQUE like '20-%' or ID_UNIQUE like '21-%'", None)),
        # Whole exact-duplicate groups (all copies), chosen by group, so pairs stay intact.
        ("exact_duplicate_pairs", f"""
            select unnest(rids) as rid from (
                select list(rid) as rids, min(rid) as m from r group by {cols} having count(*) > 1
                order by md5(cast(min(rid) as varchar) || '{SEED}') limit 100
            )"""),  # LIMIT counts groups (pairs), not unnested rows
        ("borough_mismatch", pick(
            "ARRONDISSEMENT is not null and ARRONDISSEMENT_GEO is not null "
            "and replace(ARRONDISSEMENT, ' - ', '-') <> ARRONDISSEMENT_GEO", 200)),
        ("non_borough_exterior", pick("ARRONDISSEMENT = 'Extérieur de Montréal'", None)),
        ("non_borough_ville", pick("ARRONDISSEMENT = 'Ville de Montréal'", 100)),
        ("null_borough_requests", pick("ARRONDISSEMENT is null and ID_UNIQUE is not null", None)),
        ("information_rows", pick("NATURE = 'Information'", 2000)),
        # Base ids shared by several suffixed request ids: keep every member.
        ("shared_base_ids", f"""
            select rid from (select rid, count(*) over (partition by {base}) c from r where ID_UNIQUE is not null)
            where c > 1"""),
        ("suffixed_ids", pick("regexp_matches(ID_UNIQUE, '-[A-Z]$')", 100)),
        # Every status in the seed. Rare ones are taken in full or nearly so.
        ("status_urgente", pick("DERNIER_STATUT = 'Urgente'", None)),
        ("status_reactivee", pick("DERNIER_STATUT = 'Réactivée'", 50)),
        ("status_supprimee", pick("DERNIER_STATUT = 'Supprimée'", 100)),
        ("status_annulee", pick("DERNIER_STATUT = 'Annulée'", 100)),
        ("status_acceptee", pick("DERNIER_STATUT = 'Acceptée'", 100)),
        ("status_refusee", pick("DERNIER_STATUT = 'Refusée'", 100)),
        ("status_prise_en_charge", pick("DERNIER_STATUT = 'Prise en charge'", 100)),
        ("status_transmise", pick("DERNIER_STATUT = 'Transmise pour traitement'", 100)),
        ("status_terminee", pick("DERNIER_STATUT = 'Terminée'", 1000)),
        # Text-cleaning edge cases: casing variants, stray whitespace, a lowercase postal code.
        ("casing_variants", pick("lower(ACTI_NOM) = 'soutien aux élus'", None)),
        ("whitespace", pick("RUE <> trim(RUE) or ACTI_NOM <> trim(ACTI_NOM)", 50)),
        ("postal_code_case", pick("LIN_CODE_POSTAL <> upper(LIN_CODE_POSTAL)", None)),
        ("geocoding_errors", pick("LOC_ERREUR_GDT = '1'", 100)),
        ("general_requests", pick("ID_UNIQUE is not null", 2500)),
    ]


def main() -> None:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    cols = [r[0] for r in con.execute("describe raw.requetes311").fetchall()]
    col_list = ", ".join(f'"{c}"' for c in cols)
    # rowid is DuckDB's physical row number; stable because the table was loaded in file order.
    con.execute(f"create temp table r as select rowid as rid, {col_list} from raw.requetes311")

    chosen: set[int] = set()
    print(f"{'stratum':26} {'rows':>6} {'new':>6}")
    for name, sql in build_strata(col_list):
        ids = {x[0] for x in con.execute(sql).fetchall()}
        print(f"{name:26} {len(ids):6} {len(ids - chosen):6}")
        chosen |= ids

    con.execute("create temp table chosen(rid bigint)")
    con.executemany("insert into chosen values (?)", [(i,) for i in sorted(chosen)])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"copy (select {col_list} from r join chosen using (rid) order by rid) "
        f"to '{OUT.as_posix()}' (format csv, header true)"
    )
    print(f"\n{len(chosen):,} rows -> {OUT} ({OUT.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
