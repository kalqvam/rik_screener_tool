import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rik_screener.api_workspace import data_processors, main_orchestrator


def _wrap_with_envelope(inner_xml: str) -> str:
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<soapenv:Envelope xmlns:soapenv='http://schemas.xmlsoap.org/soap/envelope/'"
        " xmlns:ns1='http://arireg.x-road.eu/producer/'>"
        "<soapenv:Body>"
        f"{inner_xml}"  # inner xml already contains ns1 tags
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    )


def _build_reports_list_xml(year_to_name_code):
    items = []
    for year, (name, code) in year_to_name_code.items():
        items.append(
            "<ns1:majandusaasta_aruanded>"
            f"<ns1:aruande_aasta>{year}</ns1:aruande_aasta>"
            f"<ns1:aruande_nimetus>{name}</ns1:aruande_nimetus>"
            f"<ns1:aruande_kood>{code}</ns1:aruande_kood>"
            "</ns1:majandusaasta_aruanded>"
        )
    inner_xml = "<ns1:majandusaastaAruanneteLoetelu_v1Response>" "<ns1:keha>" + "".join(items) + "</ns1:keha>" "</ns1:majandusaastaAruanneteLoetelu_v1Response>"
    return ET.fromstring(_wrap_with_envelope(inner_xml))


def _build_statement_xml(lines):
    rows_xml = []
    for line_code, line_name, columns in lines:
        cols_xml = []
        for column_code, column_name, value in columns:
            cols_xml.append(
                "<ns1:majandusaasta_aruanded_veerud>"
                f"<ns1:veeru_kood>{column_code}</ns1:veeru_kood>"
                f"<ns1:veeru_nimetus>{column_name}</ns1:veeru_nimetus>"
                f"<ns1:vaartus>{value}</ns1:vaartus>"
                "</ns1:majandusaasta_aruanded_veerud>"
            )
        rows_xml.append(
            "<ns1:majandusaasta_aruanded_read>"
            f"<ns1:rea_nr>{line_code}</ns1:rea_nr>"
            f"<ns1:rea_nimetus>{line_name}</ns1:rea_nimetus>"
            + "".join(cols_xml)
            + "</ns1:majandusaasta_aruanded_read>"
        )
    inner_xml = "<ns1:majandusaastaAruanneteKirjed_v1Response>" "<ns1:keha>" + "".join(rows_xml) + "</ns1:keha>" "</ns1:majandusaastaAruanneteKirjed_v1Response>"
    return ET.fromstring(_wrap_with_envelope(inner_xml))


def test_extract_statement_code_prefers_primary():
    xml_response = _build_reports_list_xml({
        2023: ("Bilanss", "101"),
        2021: ("Kasumiaruanne skeem 1", "555"),
    })

    code = data_processors.extract_statement_code(xml_response, [2023], "BS")

    assert code == "101"


def test_extract_statement_code_accepts_alternative():
    xml_response = _build_reports_list_xml({
        2023: ("Kasumiaruanne skeem 2", "202"),
    })

    code = data_processors.extract_statement_code(xml_response, [2023], "IS")

    assert code == "202"


def test_get_financial_statements_merges_companies(monkeypatch):
    company_reports = {
        "1111111": _build_reports_list_xml({2023: ("Bilanss", "900")}),
        "2222222": _build_reports_list_xml({2023: ("Bilanss", "900")}),
    }

    statement_payloads = {
        ("1111111", 2023): _build_statement_xml([
            ("10", "Assets", [("KR", "Kokku", "1000")]),
            ("20", "Equity", [("KR", "Kokku", "200")]),
        ]),
        ("2222222", 2023): _build_statement_xml([
            ("10", "Assets", [("KR", "Kokku", "1500")]),
            ("30", "Liabilities", [("KR", "Kokku", "300")]),
        ]),
    }

    def fake_get_list(company_code):
        return ET.fromstring(ET.tostring(company_reports[company_code]))

    def fake_get_statement(company_code, statement_code, year):
        assert statement_code == "900"
        return ET.fromstring(ET.tostring(statement_payloads[(company_code, year)]))

    monkeypatch.setattr(main_orchestrator, "get_annual_reports_list", fake_get_list)
    monkeypatch.setattr(main_orchestrator, "get_financial_statement_details", fake_get_statement)

    df = main_orchestrator.get_financial_statements(
        company_codes=["1111111", "2222222"],
        username="user",
        password="pass",
        statement_type="BS",
        starting_year=2023,
        num_requests=1,
        year_step=1,
    )

    assert not df.empty
    assert list(df.columns.get_level_values(0)[1:]) == ["1111111", "2222222"]
    assert ("line_name" in df.columns) or (df.columns[0] == "line_name")

    line_name_col = df["line_name"] if "line_name" in df.columns else df.iloc[:, 0]
    assert line_name_col.loc["10"] == "Assets"

    first_company_col = ("1111111", "2023_KR")
    second_company_col = ("2222222", "2023_KR")

    assert pytest.approx(df.loc["10", first_company_col]) == 1000
    assert pytest.approx(df.loc["10", second_company_col]) == 1500
    assert pd.isna(df.loc["20", second_company_col])
    assert pytest.approx(df.loc["30", second_company_col]) == 300
    assert pd.isna(df.loc["30", first_company_col])
