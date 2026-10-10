"""Lead source (open CNPJ data). Offline: argument refusals and the candidate summary. `-m real`: queries the real
Hugging Face mirror and the state cache ([cnpj] cache_dir; the SP cache is built on first run, ~3 GB)."""
import json
import re
from datetime import date

import pytest

from agent_squad.adapters.leads import cnpj

CITY = "Cubatão"


def run(capsys, *args) -> tuple[int, str]:
    try:
        cnpj.main(list(args))
        code = 0
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    return code, capsys.readouterr().out


def check_candidate(c: dict) -> None:
    assert c["has_site"].startswith("unknown"), "never claim a site without checking"
    assert c["city"] == CITY and c["state"] == "SP" and c["cnpj"].count("/") == 1
    assert c["phone"] or c["email"], "candidate without any contact"
    assert not re.search(r"\d{11}", c["name"]), "CPF in the name"
    if c["whatsapp"]:  # only 9-digit numbers starting with 9
        assert len(c["whatsapp"]) == 14 and c["whatsapp"][5] == "9", c["whatsapp"]


@pytest.mark.parametrize("args, reason", [
    (["--state", "SP", "--city", CITY, "--cnae", "8630504", "--limit", "11"], "cap of 10"),
    (["--state", "SP", "--city", CITY, "--cnae", "86a", "--limit", "2"], "cnae digits only"),
    (["--state", "S1", "--city", CITY, "--cnae", "8630504"], "invalid state"),
    (["cnae"], "cnae without --query"),
])
def test_refusals(capsys, args, reason):
    assert run(capsys, *args)[0] != 0, f"should refuse: {reason}"


def test_summary_minimizes_and_never_guesses():
    row = {"cnpj": "12345678000190", "nome_fantasia": None, "razao_social": "JOAO DA SILVA 12345678901",
           "data_inicio_atividade": "20260901", "cnae_fiscal_principal": "8630504", "cnae_desc": "Dentist",
           "tipo_logradouro": "RUA", "logradouro": "EXEMPLO", "numero": "100", "bairro": "CENTRO", "cep": "11500000",
           "uf": "SP", "ddd1": "13", "telefone1": "991234567", "correio_eletronico": None, "natureza_juridica": "2135"}
    c = cnpj.summarize(row, CITY, date(2026, 10, 1), "cnpj-open-2026-09")
    check_candidate(c)
    assert c["name"] == "" and c["whatsapp"] == "+5513991234567"      # a sole proprietor's legal name is personal
    assert c["cnpj"] == "12.345.678/0001-90" and c["suggested_slug"] == "lead-12345678"
    assert len(c["signals"]) == 3
    company = cnpj.summarize({**row, "natureza_juridica": "2062", "razao_social": "CLINICA SORRISO LTDA"}, CITY,
                             date(2026, 10, 1), "x")
    assert company["name"] == "Clinica Sorriso Ltda" and company["suggested_slug"] == "clinica-sorriso-ltda-cubatao"
    landline = cnpj.summarize({**row, "telefone1": "33334444"}, CITY, date(2026, 10, 1), "x")
    assert landline["whatsapp"] is None and landline["phone"] == "(13) 33334444"


@pytest.mark.real
def test_real_search(capsys):
    code, out = run(capsys, "--state", "SP", "--city", CITY, "--cnae", "8630504,8630505", "--days", "730",
                    "--limit", "3")
    assert code == 0
    d = json.loads(out)
    assert 1 <= len(d["candidates"]) <= 3, d["params"]
    for c in d["candidates"]:
        check_candidate(c)
    assert len({c["suggested_slug"] for c in d["candidates"]}) == len(d["candidates"]), "repeated slugs"


@pytest.mark.real
def test_real_cnae_lookup(capsys):
    code, out = run(capsys, "cnae", "--query", "odontol")
    assert code == 0 and "8630504" in out


@pytest.mark.real
def test_real_unknown_city(capsys):
    assert run(capsys, "--state", "SP", "--city", "Nao Existe City", "--cnae", "8630504")[0] != 0
