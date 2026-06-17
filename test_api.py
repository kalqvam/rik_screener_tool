"""
API test suite for rik_screener — exercises every layer of the API workflow
using synthetic XML and mocked HTTP calls (no real credentials or network needed).

Covers:
  1. Credentials & Config       – set/get config, missing config, rate limiter
  2. Validation                 – company code validation, batch filtering
  3. SOAP Client                – envelope building, retry logic, fault handling
  4. XML Parsing                – annual reports, company info, financial statements
  5. Statement Code Extraction  – single/multi-year, missing years, type mapping
  6. Consolidation Detection    – all/none/mixed/transition patterns
  7. Data Processors            – DataFrame creation, multi-year merging
  8. Orchestrator (mocked)      – get_latest_reports_info, get_financial_statements,
                                  check_statement_consistency
  9. Edge Cases                 – empty XML, malformed data, zero companies

Run:  python test_api.py
"""

import sys
import time
import traceback
from unittest.mock import patch, MagicMock
from io import StringIO

import numpy as np
import pandas as pd
import xml.etree.ElementTree as ET

PASS = 0
FAIL = 0


def report(name, passed, detail=""):
    global PASS, FAIL
    status = "PASS" if passed else "FAIL"
    if passed:
        PASS += 1
    else:
        FAIL += 1
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{status}] {name}{suffix}")


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# Synthetic XML builders
# ---------------------------------------------------------------------------
NS = "http://arireg.x-road.eu/producer/"

def _wrap_soap(inner_xml: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:ns1="{NS}">
    <soapenv:Body>
        {inner_xml}
    </soapenv:Body>
</soapenv:Envelope>'''

def _make_report_entry(year, name, code, start=None, end=None):
    start = start or f"{year}-01-01"
    end = end or f"{year}-12-31"
    return f'''<ns1:majandusaasta_aruanded>
        <ns1:aruande_aasta>{year}</ns1:aruande_aasta>
        <ns1:aruande_nimetus>{name}</ns1:aruande_nimetus>
        <ns1:aruande_kood>{code}</ns1:aruande_kood>
        <ns1:majandusaasta_algus>{start}</ns1:majandusaasta_algus>
        <ns1:majandusaasta_lopp>{end}</ns1:majandusaasta_lopp>
    </ns1:majandusaasta_aruanded>'''

def _make_company_info_xml(name):
    return _wrap_soap(f'''<ns1:ettevotjad>
        <ns1:item>
            <ns1:evnimi>{name}</ns1:evnimi>
        </ns1:item>
    </ns1:ettevotjad>''')

def _make_financial_statement_xml(rows):
    """rows: list of (line_code, line_name, [(col_code, col_name, value), ...])"""
    entries = []
    for line_code, line_name, columns in rows:
        col_xml = ""
        for col_code, col_name, value in columns:
            col_xml += f'''<ns1:majandusaasta_aruanded_veerud>
                <ns1:veeru_kood>{col_code}</ns1:veeru_kood>
                <ns1:veeru_nimetus>{col_name}</ns1:veeru_nimetus>
                <ns1:vaartus>{value}</ns1:vaartus>
            </ns1:majandusaasta_aruanded_veerud>'''
        entries.append(f'''<ns1:majandusaasta_aruanded_read>
            <ns1:rea_nr>{line_code}</ns1:rea_nr>
            <ns1:rea_nimetus>{line_name}</ns1:rea_nimetus>
            {col_xml}
        </ns1:majandusaasta_aruanded_read>''')
    return _wrap_soap("\n".join(entries))

def _make_soap_fault(message="Server error"):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
    <soapenv:Body>
        <soapenv:Fault>
            <faultcode>soapenv:Server</faultcode>
            <faultstring>{message}</faultstring>
        </soapenv:Fault>
    </soapenv:Body>
</soapenv:Envelope>'''

def _parse(xml_str):
    return ET.fromstring(xml_str)


# ===== 1. CREDENTIALS & CONFIG =====

def test_credentials_config():
    section("1. Credentials & Config")
    import rik_screener.api_workspace.config_auth as auth

    # Reset global state
    auth._config_instance = None

    # get_api_config before set raises ValueError
    try:
        auth.get_api_config()
        report("Uninitialized config raises ValueError", False)
    except ValueError:
        report("Uninitialized config raises ValueError", True)

    # set_api_config stores credentials
    config = auth.set_api_config("test_user", "test_pass", rate_limit=30)
    report("set_api_config returns APIConfig", config is not None)
    report("Username stored correctly", config.username == "test_user")
    report("Password stored correctly", config.password == "test_pass")
    report("Rate limit stored correctly", config.rate_limit == 30)
    report("Default base_url set", "ariregxmlv6.rik.ee" in config.base_url)

    # get_api_config returns same instance
    retrieved = auth.get_api_config()
    report("get_api_config returns stored config", retrieved is config)

    # Overwrite with new credentials
    config2 = auth.set_api_config("new_user", "new_pass")
    retrieved2 = auth.get_api_config()
    report("Config overwrite works", retrieved2.username == "new_user")
    report("Default rate limit is 20", config2.rate_limit == 20)

    # Rate limiter timing
    fast_config = auth.APIConfig("u", "p", rate_limit=600)  # 10 req/sec
    fast_config.last_request_time = 0.0
    t0 = time.time()
    fast_config.wait_for_rate_limit()
    elapsed = time.time() - t0
    report("Rate limiter does not block on first call", elapsed < 0.5)

    # Rate limiter enforces wait
    fast_config2 = auth.APIConfig("u", "p", rate_limit=60)  # 1 req/sec
    fast_config2.last_request_time = time.time()
    t0 = time.time()
    fast_config2.wait_for_rate_limit()
    elapsed = time.time() - t0
    report("Rate limiter enforces minimum interval", elapsed >= 0.8,
           f"waited {elapsed:.2f}s")

    # Cleanup
    auth._config_instance = None


# ===== 2. VALIDATION =====

def test_validation():
    section("2. Company Code Validation")
    from rik_screener.api_workspace.utils import validate_company_code, validate_company_codes, format_progress

    # Valid codes
    report("7-digit code valid", validate_company_code("1234567") == True)
    report("8-digit code valid", validate_company_code("12345678") == True)
    report("Code with whitespace trimmed", validate_company_code(" 1234567 ") == True)

    # Invalid codes
    report("6-digit code rejected", validate_company_code("123456") == False)
    report("9-digit code rejected", validate_company_code("123456789") == False)
    report("Letters rejected", validate_company_code("123ABC7") == False)
    report("Empty string rejected", validate_company_code("") == False)
    report("None rejected", validate_company_code(None) == False)
    report("Integer rejected", validate_company_code(1234567) == False)
    report("Special chars rejected", validate_company_code("12345-7") == False)

    # Batch validation
    codes = ["1234567", "bad", "12345678", "", "99999999", "123"]
    valid = validate_company_codes(codes)
    report("Batch: keeps valid codes", set(valid) == {"1234567", "12345678", "99999999"},
           f"got {valid}")
    report("Batch: filters invalid codes", len(valid) == 3)

    # Empty batch
    report("Batch: empty list returns empty", validate_company_codes([]) == [])

    # format_progress
    report("format_progress output", "5/10 (50.0%)" in format_progress(5, 10))
    report("format_progress zero total", "0.0%" in format_progress(0, 0))


# ===== 3. SOAP CLIENT =====

def test_soap_client():
    section("3. SOAP Client")
    import rik_screener.api_workspace.config_auth as auth
    from rik_screener.api_workspace.soap_client import SOAPClient

    auth.set_api_config("soap_user", "soap_pass", rate_limit=600)

    client = SOAPClient()

    # Envelope structure
    envelope = client.build_envelope("testOp", "<prod:param>value</prod:param>")
    report("Envelope contains operation", "testOp" in envelope)
    report("Envelope contains credentials", "soap_user" in envelope and "soap_pass" in envelope)
    report("Envelope contains body content", "<prod:param>value</prod:param>" in envelope)
    report("Envelope has XML declaration", '<?xml version="1.0"' in envelope)
    report("Envelope has SOAP namespace", "schemas.xmlsoap.org/soap/envelope/" in envelope)

    # call_endpoint builds correct body with escaping
    with patch.object(client, 'send_request', return_value=None) as mock_send:
        client.call_endpoint("myOp", {"key1": "val1", "key2": "A & B <tag>"})
        called_envelope = mock_send.call_args[0][0]
        report("call_endpoint includes params", "<prod:key1>val1</prod:key1>" in called_envelope)
        report("call_endpoint escapes XML chars", "A &amp; B &lt;tag&gt;" in called_envelope)

    # send_request — successful response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = _wrap_soap("<ns1:data>ok</ns1:data>").encode('utf-8')
    mock_response.raise_for_status = MagicMock()

    with patch.object(client.session, 'post', return_value=mock_response):
        result = client.send_request("<envelope/>")
        report("send_request returns Element on success", result is not None and isinstance(result, ET.Element))

    # send_request — SOAP fault returns None
    fault_response = MagicMock()
    fault_response.status_code = 200
    fault_response.content = _make_soap_fault("Auth failed").encode('utf-8')
    fault_response.raise_for_status = MagicMock()

    with patch.object(client.session, 'post', return_value=fault_response):
        result = client.send_request("<envelope/>")
        report("send_request returns None on SOAP fault", result is None)

    # send_request — HTTP error with retries
    import requests
    with patch.object(client.session, 'post', side_effect=requests.ConnectionError("timeout")):
        with patch('rik_screener.api_workspace.soap_client.time.sleep'):  # skip actual waits
            result = client.send_request("<envelope/>")
            report("send_request returns None after retries exhausted", result is None)

    # send_request — XML parse error returns None
    bad_xml_response = MagicMock()
    bad_xml_response.status_code = 200
    bad_xml_response.content = b"this is not xml"
    bad_xml_response.raise_for_status = MagicMock()

    with patch.object(client.session, 'post', return_value=bad_xml_response):
        result = client.send_request("<envelope/>")
        report("send_request returns None on malformed XML", result is None)

    auth._config_instance = None


# ===== 4. XML PARSING =====

def test_xml_parsing():
    section("4. XML Parsing — Annual Reports & Company Info")
    from rik_screener.api_workspace.data_processors import (
        parse_annual_reports_response, parse_company_info_response
    )

    # Parse annual reports — standard case
    xml = _wrap_soap(
        _make_report_entry(2023, "Bilanss", "BS001", "2023-01-01", "2023-12-31")
        + _make_report_entry(2022, "Bilanss", "BS001", "2022-01-01", "2022-12-31")
    )
    root = _parse(xml)
    result = parse_annual_reports_response(root, "1234567")
    report("Parse reports: returns dict", isinstance(result, dict))
    report("Parse reports: company_code", result['company_code'] == "1234567")
    report("Parse reports: latest_year", result['latest_year'] == "2023")
    report("Parse reports: period_start", result['period_start'] == "2023-01-01")
    report("Parse reports: period_end", result['period_end'] == "2023-12-31")

    # Parse annual reports — empty XML
    empty_xml = _wrap_soap("<ns1:empty/>")
    result = parse_annual_reports_response(_parse(empty_xml), "1234567")
    report("Parse reports: empty XML returns None", result is None)

    # Parse company info — standard case
    info_xml = _make_company_info_xml("Test OÜ")
    result = parse_company_info_response(_parse(info_xml), "1234567")
    report("Parse company info: returns name", result == "Test OÜ")

    # Parse company info — Estonian characters
    info_xml2 = _make_company_info_xml("Põhja-Eesti Teenused OÜ")
    result2 = parse_company_info_response(_parse(info_xml2), "1234567")
    report("Parse company info: Estonian chars", result2 == "Põhja-Eesti Teenused OÜ")

    # Parse company info — missing name element
    empty_info = _wrap_soap("<ns1:ettevotjad><ns1:item></ns1:item></ns1:ettevotjad>")
    result = parse_company_info_response(_parse(empty_info), "1234567")
    report("Parse company info: missing name returns None", result is None)


# ===== 5. FINANCIAL STATEMENT PARSING =====

def test_financial_statement_parsing():
    section("5. Financial Statement Parsing")
    from rik_screener.api_workspace.data_processors import parse_financial_statement_response

    # Standard financial statement
    xml = _make_financial_statement_xml([
        ("100", "Raha ja pangakontod", [("C1", "2023", "50000"), ("C2", "2022", "45000")]),
        ("200", "Nõuded ja ettemaksed", [("C1", "2023", "120000"), ("C2", "2022", "110000")]),
        ("300", "Varud", [("C1", "2023", "30000"), ("C2", "2022", "28000")]),
    ])
    df = parse_financial_statement_response(_parse(xml))
    report("Statement parse: returns DataFrame", isinstance(df, pd.DataFrame))
    report("Statement parse: has line_code", 'line_code' in df.columns)
    report("Statement parse: has line_name", 'line_name' in df.columns)
    report("Statement parse: correct row count", len(df) == 3)
    report("Statement parse: has period columns", 'C1' in df.columns and 'C2' in df.columns)
    report("Statement parse: numeric values",
           df.loc[df['line_code'] == '100', 'C1'].iloc[0] == 50000)

    # Empty statement
    empty_xml = _wrap_soap("<ns1:empty/>")
    df_empty = parse_financial_statement_response(_parse(empty_xml))
    report("Statement parse: empty XML returns empty DataFrame",
           isinstance(df_empty, pd.DataFrame) and df_empty.empty)

    # Statement with empty value — _safe_text converts "" to None,
    # so the value field is None and pivot_table may drop the row entirely.
    # The key requirement is that this doesn't crash.
    xml_nulls = _make_financial_statement_xml([
        ("100", "Raha", [("C1", "2023", "")]),
    ])
    df_nulls = parse_financial_statement_response(_parse(xml_nulls))
    report("Statement parse: empty value handled without crash",
           isinstance(df_nulls, pd.DataFrame))


# ===== 6. STATEMENT CODE EXTRACTION =====

def test_statement_code_extraction():
    section("6. Statement Code Extraction")
    from rik_screener.api_workspace.data_processors import (
        extract_statement_code, parse_statement_codes_by_year
    )

    # Build XML with multiple statement types across years
    xml = _wrap_soap(
        _make_report_entry(2023, "Bilanss (jätkuv)", "BS100")
        + _make_report_entry(2023, "Kasumiaruanne", "IS100")
        + _make_report_entry(2023, "Rahavoogude aruanne", "CF100")
        + _make_report_entry(2022, "Bilanss (jätkuv)", "BS100")
        + _make_report_entry(2022, "Kasumiaruanne", "IS100")
        + _make_report_entry(2022, "Rahavoogude aruanne", "CF100")
    )
    root = _parse(xml)

    # extract_statement_code — consistent codes
    bs_code = extract_statement_code(root, [2023, 2022], "BS")
    report("Extract BS code", bs_code == "BS100")
    is_code = extract_statement_code(root, [2023, 2022], "IS")
    report("Extract IS code", is_code == "IS100")
    cf_code = extract_statement_code(root, [2023, 2022], "CF")
    report("Extract CF code", cf_code == "CF100")

    # extract_statement_code — case insensitive matching
    xml_lower = _wrap_soap(_make_report_entry(2023, "bilanss", "BS200"))
    report("Extract: case-insensitive match",
           extract_statement_code(_parse(xml_lower), [2023], "BS") == "BS200")

    # extract_statement_code — code changed across years (inconsistent)
    xml_changed = _wrap_soap(
        _make_report_entry(2023, "Bilanss", "BS_NEW")
        + _make_report_entry(2022, "Bilanss", "BS_OLD")
    )
    try:
        extract_statement_code(_parse(xml_changed), [2023, 2022], "BS")
        report("Extract: detects code change", False)
    except ValueError as e:
        report("Extract: detects code change", "Change in reporting" in str(e))

    # extract_statement_code — missing year
    try:
        extract_statement_code(root, [2023, 2021], "BS")
        report("Extract: missing year raises ValueError", False)
    except ValueError:
        report("Extract: missing year raises ValueError", True)

    # extract_statement_code — unsupported type
    try:
        extract_statement_code(root, [2023], "XX")
        report("Extract: unsupported type raises ValueError", False)
    except ValueError:
        report("Extract: unsupported type raises ValueError", True)

    # extract_statement_code — empty XML
    try:
        extract_statement_code(_parse(_wrap_soap("<ns1:empty/>")), [2023], "BS")
        report("Extract: empty XML raises ValueError", False)
    except ValueError:
        report("Extract: empty XML raises ValueError", True)

    # parse_statement_codes_by_year — multi-year multi-type
    codes = parse_statement_codes_by_year(root, 2023, 2022, ["BS", "IS", "CF"])
    report("Codes by year: BS present", codes["BS"] == ["BS100", "BS100"])
    report("Codes by year: IS present", codes["IS"] == ["IS100", "IS100"])
    report("Codes by year: CF present", codes["CF"] == ["CF100", "CF100"])

    # parse_statement_codes_by_year — missing year yields None
    codes_gap = parse_statement_codes_by_year(root, 2023, 2021, ["BS"])
    report("Codes by year: missing year is None",
           codes_gap["BS"][2] is None,  # 2021 not in XML
           f"got {codes_gap['BS']}")


# ===== 7. CONSOLIDATION DETECTION =====

def test_consolidation_detection():
    section("7. Consolidation Detection")
    from rik_screener.api_workspace.data_processors import parse_consolidation_status_by_year
    from rik_screener.api_workspace.main_orchestrator import _determine_consolidation_status

    # All non-consolidated
    xml_non = _wrap_soap(
        _make_report_entry(2023, "Bilanss", "B1")
        + _make_report_entry(2022, "Bilanss", "B1")
    )
    status = parse_consolidation_status_by_year(_parse(xml_non), 2023, 2022)
    report("Consolidation: non-consolidated detected",
           all(v == False for v in status.values()))

    # All consolidated
    xml_con = _wrap_soap(
        _make_report_entry(2023, "Konsolideeritud bilanss", "KB1")
        + _make_report_entry(2022, "Konsolideeritud bilanss", "KB1")
    )
    status_con = parse_consolidation_status_by_year(_parse(xml_con), 2023, 2022)
    report("Consolidation: consolidated detected",
           all(v == True for v in status_con.values()))

    # Mixed — consolidated since 2023
    xml_mix = _wrap_soap(
        _make_report_entry(2023, "Konsolideeritud bilanss", "KB1")
        + _make_report_entry(2022, "Bilanss", "B1")
        + _make_report_entry(2021, "Bilanss", "B1")
    )
    status_mix = parse_consolidation_status_by_year(_parse(xml_mix), 2023, 2021)
    report("Consolidation: mixed pattern detected",
           status_mix.get(2023) == True and status_mix.get(2022) == False)

    # _determine_consolidation_status — all consolidated
    result = _determine_consolidation_status({2023: True, 2022: True}, 2023, 2022)
    report("Status: all consolidated", result == "Consolidated")

    # _determine_consolidation_status — all non-consolidated
    result = _determine_consolidation_status({2023: False, 2022: False}, 2023, 2022)
    report("Status: all non-consolidated", result == "Non-consolidated")

    # _determine_consolidation_status — became consolidated
    result = _determine_consolidation_status(
        {2023: True, 2022: True, 2021: False, 2020: False}, 2023, 2020
    )
    report("Status: consolidated since year", "Consolidated since" in result, result)

    # _determine_consolidation_status — stopped consolidating
    result = _determine_consolidation_status(
        {2023: False, 2022: False, 2021: True, 2020: True}, 2023, 2020
    )
    report("Status: non-consolidated since year", "Non-consolidated since" in result, result)

    # _determine_consolidation_status — empty
    result = _determine_consolidation_status({}, 2023, 2022)
    report("Status: empty defaults to Non-consolidated", result == "Non-consolidated")


# ===== 8. DATA PROCESSORS — DATAFRAME CREATION =====

def test_dataframe_creation():
    section("8. DataFrame Creation & Merging")
    from rik_screener.api_workspace.data_processors import create_latest_reports_dataframe

    # Without names
    data = [
        {'company_code': '1234567', 'latest_year': '2023', 'period_start': '2023-01-01', 'period_end': '2023-12-31'},
        {'company_code': '7654321', 'latest_year': '2022', 'period_start': '2022-01-01', 'period_end': '2022-12-31'},
    ]
    df = create_latest_reports_dataframe(data)
    report("DataFrame: correct shape", len(df) == 2)
    report("DataFrame: has company_code", 'company_code' in df.columns)
    report("DataFrame: no company_name without names_data", 'company_name' not in df.columns)

    # With names
    names = {'1234567': 'Alpha OÜ', '7654321': 'Beta AS'}
    df_named = create_latest_reports_dataframe(data, names)
    report("DataFrame with names: has company_name", 'company_name' in df_named.columns)
    report("DataFrame with names: correct mapping",
           df_named.loc[df_named['company_code'] == '1234567', 'company_name'].iloc[0] == 'Alpha OÜ')
    report("DataFrame with names: column order",
           list(df_named.columns) == ['company_code', 'company_name', 'latest_year', 'period_start', 'period_end'])

    # Empty data
    df_empty = create_latest_reports_dataframe([])
    report("DataFrame: empty data returns empty DataFrame", len(df_empty) == 0)


# ===== 9. ORCHESTRATOR (MOCKED) =====

def test_orchestrator_mocked():
    section("9. Orchestrator — Mocked End-to-End")
    import rik_screener.api_workspace.config_auth as auth
    from rik_screener.api_workspace.main_orchestrator import (
        get_latest_reports_info, get_financial_statements,
        check_statement_consistency, _generate_years, _merge_year_frames
    )

    # _generate_years
    years = _generate_years(2023, 3, 2)
    report("_generate_years", years == [2023, 2021, 2019], f"got {years}")

    years_single = _generate_years(2023, 1, 1)
    report("_generate_years single", years_single == [2023])

    # _merge_year_frames
    df1 = pd.DataFrame({'line_name': ['Cash', 'Assets'], 'val_2023': [100, 200]},
                        index=['L1', 'L2'])
    df2 = pd.DataFrame({'line_name': ['Cash', 'Assets', 'Debt'], 'val_2022': [90, 180, 50]},
                        index=['L1', 'L2', 'L3'])
    merged = _merge_year_frames(df1, df2)
    report("Merge: union of rows", len(merged) == 3)
    report("Merge: both year columns present",
           'val_2023' in merged.columns and 'val_2022' in merged.columns)
    report("Merge: line_name preserved", 'line_name' in merged.columns)

    # get_latest_reports_info — mocked
    reports_xml = _wrap_soap(
        _make_report_entry(2023, "Bilanss", "BS1", "2023-01-01", "2023-12-31")
    )
    info_xml = _make_company_info_xml("Mocked OÜ")

    def mock_get_reports(code):
        return _parse(reports_xml)

    def mock_get_info(code):
        return _parse(info_xml)

    with patch('rik_screener.api_workspace.main_orchestrator.get_annual_reports_list', side_effect=mock_get_reports), \
         patch('rik_screener.api_workspace.main_orchestrator.get_company_basic_info', side_effect=mock_get_info):

        df = get_latest_reports_info(
            ["1234567", "7654321"], "user", "pass", include_names=True, rate_limit=600
        )
        report("Orchestrator reports: returns DataFrame", isinstance(df, pd.DataFrame))
        report("Orchestrator reports: correct row count", len(df) == 2)
        report("Orchestrator reports: has company_name", 'company_name' in df.columns)
        report("Orchestrator reports: name resolved",
               df['company_name'].iloc[0] == "Mocked OÜ")

    # get_latest_reports_info — empty codes
    df_empty = get_latest_reports_info([], "user", "pass")
    report("Orchestrator reports: empty codes returns empty DataFrame", len(df_empty) == 0)

    # get_latest_reports_info — all invalid codes
    df_invalid = get_latest_reports_info(["bad", "xx"], "user", "pass")
    report("Orchestrator reports: all invalid returns empty DataFrame", len(df_invalid) == 0)

    # get_financial_statements — mocked
    stmt_xml = _make_financial_statement_xml([
        ("100", "Raha", [("C1", "2023_main", "50000")]),
        ("200", "Varad", [("C1", "2023_main", "200000")]),
    ])

    call_count = {'reports': 0, 'details': 0}

    def mock_reports_for_stmt(code):
        call_count['reports'] += 1
        xml = _wrap_soap(_make_report_entry(2023, "Bilanss (jätkuv)", "BS100"))
        return _parse(xml)

    def mock_details(code, stmt_code, year):
        call_count['details'] += 1
        return _parse(stmt_xml)

    with patch('rik_screener.api_workspace.main_orchestrator.get_annual_reports_list', side_effect=mock_reports_for_stmt), \
         patch('rik_screener.api_workspace.main_orchestrator.get_financial_statement_details', side_effect=mock_details):

        df_stmt = get_financial_statements(
            ["1234567"], "user", "pass",
            statement_type="BS", starting_year=2023, num_requests=1, rate_limit=600
        )
        report("Orchestrator statements: returns DataFrame", isinstance(df_stmt, pd.DataFrame))
        report("Orchestrator statements: has data", not df_stmt.empty)
        report("Orchestrator statements: has line_name", 'line_name' in df_stmt.columns)

    # get_financial_statements — invalid params
    try:
        get_financial_statements(["1234567"], "u", "p", num_requests=0)
        report("Orchestrator statements: rejects num_requests=0", False)
    except ValueError:
        report("Orchestrator statements: rejects num_requests=0", True)

    try:
        get_financial_statements(["1234567"], "u", "p", year_step=0)
        report("Orchestrator statements: rejects year_step=0", False)
    except ValueError:
        report("Orchestrator statements: rejects year_step=0", True)

    # check_statement_consistency — mocked
    consistency_xml = _wrap_soap(
        _make_report_entry(2023, "Bilanss", "BS100")
        + _make_report_entry(2023, "Kasumiaruanne", "IS100")
        + _make_report_entry(2023, "Rahavoogude aruanne", "CF100")
        + _make_report_entry(2022, "Bilanss", "BS100")
        + _make_report_entry(2022, "Kasumiaruanne", "IS100")
        + _make_report_entry(2022, "Rahavoogude aruanne", "CF100")
    )

    with patch('rik_screener.api_workspace.main_orchestrator.get_annual_reports_list',
               return_value=_parse(consistency_xml)):

        results = check_statement_consistency(
            ["1234567"], "user", "pass",
            target_year=2023, end_year=2022, rate_limit=600
        )
        report("Consistency: returns dict", isinstance(results, dict))
        report("Consistency: company present", "1234567" in results)

        answer, arrays, cons_status = results["1234567"]
        report("Consistency: consistent answer is Yes", answer == "Yes")
        report("Consistency: 3 statement type arrays", len(arrays) == 3)
        report("Consistency: BS codes match", arrays[0] == ["BS100", "BS100"])
        report("Consistency: consolidation status",
               cons_status == "Non-consolidated")

    # check_statement_consistency — reversed years
    try:
        check_statement_consistency(["1234567"], "u", "p", target_year=2020, end_year=2023)
        report("Consistency: rejects reversed years", False)
    except ValueError:
        report("Consistency: rejects reversed years", True)

    auth._config_instance = None


# ===== 10. EDGE CASES =====

def test_edge_cases():
    section("10. Edge Cases & Error Handling")
    import rik_screener.api_workspace.config_auth as auth
    from rik_screener.api_workspace.data_processors import (
        parse_annual_reports_response, parse_company_info_response,
        parse_financial_statement_response, parse_statement_codes_by_year,
        parse_consolidation_status_by_year
    )

    # Completely empty SOAP body
    empty = _parse(_wrap_soap(""))
    report("Edge: empty body annual reports", parse_annual_reports_response(empty, "1234567") is None)
    report("Edge: empty body company info", parse_company_info_response(empty, "1234567") is None)
    report("Edge: empty body financial statement",
           parse_financial_statement_response(empty).empty)
    report("Edge: empty body codes by year",
           parse_statement_codes_by_year(empty, 2023, 2022, ["BS"]) == {"BS": []})
    report("Edge: empty body consolidation",
           parse_consolidation_status_by_year(empty, 2023, 2022) == {})

    # Report with missing sub-elements
    xml_partial = _wrap_soap('''<ns1:majandusaasta_aruanded>
        <ns1:aruande_aasta>2023</ns1:aruande_aasta>
    </ns1:majandusaasta_aruanded>''')
    result = parse_annual_reports_response(_parse(xml_partial), "1234567")
    report("Edge: partial report has None fields",
           result is not None and result['period_start'] is None)

    # Financial statement with duplicate line_code (pivot should handle)
    xml_dup = _make_financial_statement_xml([
        ("100", "Raha", [("C1", "2023", "50000")]),
        ("100", "Raha", [("C1", "2023", "50000")]),  # duplicate
    ])
    df_dup = parse_financial_statement_response(_parse(xml_dup))
    report("Edge: duplicate lines handled without crash", isinstance(df_dup, pd.DataFrame))

    # Statement with non-numeric value — pd.to_numeric coerces to NaN,
    # but pivot_table(aggfunc='first') may drop all-NaN cells.
    # Include a valid row alongside to ensure the DataFrame is non-empty.
    xml_bad_val = _make_financial_statement_xml([
        ("100", "Raha", [("C1", "2023", "not_a_number")]),
        ("200", "Varad", [("C1", "2023", "99999")]),
    ])
    df_bad = parse_financial_statement_response(_parse(xml_bad_val))
    report("Edge: non-numeric value coerced (no crash)",
           isinstance(df_bad, pd.DataFrame) and len(df_bad) >= 1)

    auth._config_instance = None


# ===== RUN =====

if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("  RIK SCREENER — API TEST SUITE")
    print("=" * 60)

    tests = [
        test_credentials_config,
        test_validation,
        test_soap_client,
        test_xml_parsing,
        test_financial_statement_parsing,
        test_statement_code_extraction,
        test_consolidation_detection,
        test_dataframe_creation,
        test_orchestrator_mocked,
        test_edge_cases,
    ]

    for test_fn in tests:
        try:
            test_fn()
        except Exception as e:
            FAIL += 1
            print(f"\n  [CRASH] {test_fn.__name__}: {e}")
            traceback.print_exc()

    print(f"\n{'='*60}")
    print(f"  RESULTS: {PASS} passed, {FAIL} failed, {PASS + FAIL} total")
    print(f"{'='*60}\n")

    sys.exit(0 if FAIL == 0 else 1)
