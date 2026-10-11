"""Lead source: Brazilian open CNPJ data (Receita Federal registry, Parquet mirror on Hugging Face, read with DuckDB).

Lead-source adapter contract
----------------------------
A lead source is a CLI the hunter agent runs (`squad leads ...`) that prints candidates as JSON on stdout and never
contacts anyone. Generic on purpose: niche, place and time window come from the arguments, nothing is fixed in code.
Output: {"params": {...}, "source": "<name>", "candidates": [...]}, each candidate with these keys (null when unknown):

    suggested_slug  unique within the run, lowercase-hyphenated   name, cnpj (tax id), niche, opened_on (YYYY-MM-DD)
    address, city, state, phone, whatsapp (+E.164, only when the number is certainly mobile), email
    has_site        always "unknown: ..." unless the source really checked (the lead-dossier skill resolves it)
    signals         list of short buying/risk signals             origin  source + data version, stored on the deal

Errors go to stderr with a non-zero exit. Keep personal data minimal (LGPD/GDPR): business data only.

The campaign the hunter registers before searching mirrors the call: `source` is the adapter name ("cnpj"),
`criteria` the search arguments (e.g. {"cnae": ["8630504"], "days": 365}), `location` "City/ST".

Adding another source (Google Places, Apollo, a CSV export...): write agent_squad/adapters/leads/<name>.py (or any
importable module) with main(argv) printing the same JSON, set `[adapters] leads = "<name>"`, and tell the hunter
which arguments it takes in agents/hunter/skills/hunter-cycle/SKILL.md.

    squad leads search --state SP --city "Cubatão" --cnae 8630504,8630505 --days 365 --limit 5
    squad leads cache --state SP        # builds the state cache (first time: a few minutes, reads ~3 GB)
    squad leads cnae --query dentist    # finds CNAE codes by text (never guess a code)
"""
import argparse
import json
import os
import re
import sys
import unicodedata
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from ...config import ROOT, settings

HF_DATASET = "cnpjaberto/cnpj-dados-receita-brasil-{month}"


def month() -> str:
    m = os.environ.get("CNPJ_MONTH") or settings().section("cnpj").get("month", "2026-09")
    if not re.fullmatch(r"\d{4}-\d{2}", m):
        sys.exit(f"invalid CNPJ month {m!r} (expected YYYY-MM)")
    return m


def hf() -> str:
    return "https://huggingface.co/datasets/" + HF_DATASET.format(month=month())


def cache_dir() -> Path:
    return ROOT / settings().section("cnpj").get("cache_dir", ".cache/cnpj")


def norm(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn").upper().strip()


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", norm(s).lower()).strip("-")


def files(table: str) -> list[str]:
    url = f"https://huggingface.co/api/datasets/{HF_DATASET.format(month=month())}/tree/main/{table}"
    with urllib.request.urlopen(url, timeout=20) as r:
        paths = [e["path"] for e in json.load(r)]
    if not all(re.fullmatch(r"[\w./-]+\.parquet", p) for p in paths):  # these paths go into SQL text
        raise SystemExit(f"unexpected file names in the dataset listing for {table}")
    return [f"{hf()}/resolve/main/{p}" for p in paths]


def connect():
    try:
        import duckdb
    except ImportError:
        sys.exit("duckdb is missing: install the 'leads' extra (uv sync --extra leads)")
    c = duckdb.connect()
    c.execute("install httpfs; load httpfs")
    return c


def ensure_cache(state: str) -> tuple[Path, Path]:
    cache, m = cache_dir(), month()
    est, emp = cache / f"estab_{m}_{state}.parquet", cache / f"emp_{m}_{state}.parquet"
    if est.exists() and emp.exists():
        return est, emp
    print(f"building the {state} cache (first time: reads ~3 GB, takes a few minutes)...", file=sys.stderr)
    cache.mkdir(parents=True, exist_ok=True)
    c = connect()
    c.execute(f"""copy (select cnpj_basico||cnpj_ordem||cnpj_dv as cnpj, cnpj_basico, nome_fantasia,
            data_inicio_atividade, cnae_fiscal_principal, tipo_logradouro, logradouro, numero, bairro, cep,
            uf, municipio, ddd1, telefone1, correio_eletronico
        from read_parquet({files('estabelecimentos')})
        where uf = '{state}' and situacao_cadastral = '02' and identificador_matriz_filial = '1')
        to '{est.as_posix()}.tmp' (format parquet)""")
    os.replace(f"{est}.tmp", est)
    c.execute(f"""copy (select cnpj_basico, razao_social, natureza_juridica, porte_empresa
        from read_parquet({files('empresas')})
        where cnpj_basico in (select cnpj_basico from read_parquet('{est.as_posix()}')))
        to '{emp.as_posix()}.tmp' (format parquet)""")
    os.replace(f"{emp}.tmp", emp)
    return est, emp


def candidates(state: str, city: str, cnaes: list[str], since: date, limit: int, until: date | None = None,
               named_only: bool = True) -> list[dict]:
    """Newest first inside [since, until]. named_only skips sole proprietors without a trade name: their legal name
    is the owner's personal name, so they cannot become a dossier (common in MEI-heavy niches)."""
    est, emp = ensure_cache(state)
    c = connect()
    r = c.execute(f"select codigo from read_parquet('{hf()}/resolve/main/municipios/municipios.parquet') "
                  "where descricao = ?", [norm(city)]).fetchone()
    if not r:
        sys.exit(f"city not found in the Receita table: {norm(city)}")
    like = " or ".join(f"e.cnae_fiscal_principal like '{x}%'" for x in cnaes)
    rows = c.execute(f"""select e.*, m.razao_social, m.natureza_juridica,
            (select descricao from read_parquet('{hf()}/resolve/main/cnaes/cnaes.parquet') n
             where n.codigo = e.cnae_fiscal_principal) as cnae_desc
        from read_parquet('{est.as_posix()}') e left join read_parquet('{emp.as_posix()}') m using (cnpj_basico)
        where e.municipio = ? and ({like}) and e.data_inicio_atividade between ? and ?
          and (e.telefone1 is not null or e.correio_eletronico is not null)
          and (not ? or nullif(trim(e.nome_fantasia), '') is not null or coalesce(m.natureza_juridica, '') <> '2135')
        order by e.data_inicio_atividade desc limit ?""",
        [r[0], since.strftime("%Y%m%d"), (until or date.today()).strftime("%Y%m%d"), named_only, limit]).fetchall()
    cols = [d[0] for d in c.description]
    return [dict(zip(cols, x)) for x in rows]


def summarize(row: dict, city: str, today: date, origin: str) -> dict:
    sole = row["natureza_juridica"] == "2135"  # a sole proprietor's legal name is the owner's own name
    name = (row["nome_fantasia"] or ("" if sole else row["razao_social"]) or "").strip().title()
    name = re.sub(r"\s*\d{11}\b", "", name)  # sole-proprietor (MEI/EI) legal names embed the owner's CPF: drop it
    ddd, tel = row["ddd1"], row["telefone1"] or ""
    d = row["data_inicio_atividade"]
    opened = date(int(d[:4]), int(d[4:6]), int(d[6:8]))
    c = row["cnpj"]
    street = " ".join(x for x in [row["tipo_logradouro"], row["logradouro"], row["numero"]] if x)
    signals = []
    if (today - opened).days <= 120:
        signals.append("company opened less than 120 days ago")
    if sole:
        signals.append("sole proprietor: phone/email may be personal data (LGPD)")
    if not name:
        signals.append("no trade name: find the business name in the dossier step")
    return {
        "suggested_slug": slugify(f"{name} {city}") if name else f"lead-{row['cnpj'][:8]}",
        "name": name,
        "cnpj": f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}",
        "niche": row["cnae_desc"] or row["cnae_fiscal_principal"],
        "opened_on": opened.isoformat(),
        "address": f"{street} - {row['bairro'] or ''}, CEP {row['cep'] or ''}".strip(),
        "city": city,
        "state": row["uf"],
        "phone": f"({ddd}) {tel}" if ddd and tel else None,
        # WhatsApp only for 9-digit numbers starting with 9 (Brazilian mobile); never inferred otherwise
        "whatsapp": f"+55{ddd}{tel}" if ddd and len(tel) == 9 and tel[0] == "9" else None,
        "email": row["correio_eletronico"],  # usually the accountant's: NOT evidence of a website
        "has_site": "unknown: check with the dossier (web search by name + city)",
        "signals": signals,
        "origin": origin,
    }


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):  # JSON and errors always UTF-8, Windows included
        getattr(stream, "reconfigure", lambda **_: None)(encoding="utf-8")
    cap = settings().limits.max_leads_per_round
    ap = argparse.ArgumentParser(prog="squad leads", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="search", choices=["search", "cache", "cnae"])
    ap.add_argument("--state", help="two-letter state (UF)")
    ap.add_argument("--query", help="text for the cnae subcommand")
    ap.add_argument("--city")
    ap.add_argument("--cnae", help="CNAE codes (or prefixes), comma-separated")
    ap.add_argument("--days", type=int, default=365, help="opened in the last N days")
    ap.add_argument("--opened-since", help="YYYY-MM-DD (overrides --days)")
    ap.add_argument("--opened-before", help="YYYY-MM-DD: only companies opened before this date (established ones)")
    ap.add_argument("--any-name", action="store_true", help="include sole proprietors without a trade name")
    ap.add_argument("--limit", type=int, default=5)
    a = ap.parse_args(argv)
    if a.cmd == "cnae":
        if not a.query:
            sys.exit("cnae requires --query TEXT")
        rows = connect().execute(
            f"select codigo, descricao from read_parquet('{hf()}/resolve/main/cnaes/cnaes.parquet') "
            "where strip_accents(lower(descricao)) like ? order by codigo limit 40",
            [f"%{norm(a.query).lower()}%"]).fetchall()
        print(json.dumps([{"code": c, "description": d} for c, d in rows], ensure_ascii=False, indent=1))
        return
    if not a.state or not re.fullmatch(r"[A-Za-z]{2}", a.state):
        sys.exit("invalid --state")
    state = a.state.upper()
    if a.cmd == "cache":
        print(ensure_cache(state)[0])
        return
    if not (a.city and a.cnae):
        sys.exit("search requires --city and --cnae")
    cnaes = [x.strip() for x in a.cnae.split(",") if x.strip()]
    if not cnaes or not all(x.isdigit() for x in cnaes):
        sys.exit("--cnae: digits only, comma-separated")
    if not 1 <= a.limit <= cap:
        sys.exit(f"--limit must be 1 to {cap} (leads per round cap)")
    today = date.today()
    since = date.fromisoformat(a.opened_since) if a.opened_since else today - timedelta(days=a.days)
    until = date.fromisoformat(a.opened_before) if a.opened_before else None
    if until and until <= since:
        sys.exit("--opened-before must be after the window start (use a larger --days or --opened-since)")
    origin = f"cnpj-open-{month()}"
    out = [summarize(r, a.city, today, origin) for r in candidates(state, a.city, cnaes, since, a.limit, until, not a.any_name)]
    seen = {}
    for o in out:  # unique slug within the run
        seen[o["suggested_slug"]] = seen.get(o["suggested_slug"], 0) + 1
        if seen[o["suggested_slug"]] > 1:
            o["suggested_slug"] += f"-{seen[o['suggested_slug']]}"
    print(json.dumps({"params": {"state": state, "city": a.city, "cnae": cnaes, "opened_since": since.isoformat(),
                                 "opened_before": until and until.isoformat(), "named_only": not a.any_name,
                                 "limit": a.limit},
                      "source": "cnpj", "cnpj_month": month(), "candidates": out}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
