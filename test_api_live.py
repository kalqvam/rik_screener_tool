"""
Live API test for rik_screener — makes real calls to ariregxmlv6.rik.ee
and saves all results to the CSV folder for manual review.

Usage:
    python test_api_live.py

You will be prompted for your RIK API credentials.
Output CSV files are written to ../CSV/api_test_<timestamp>_*.csv
"""

import os
import sys
import traceback
from datetime import datetime

import pandas as pd
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_DIR = os.environ.get(
    'RIK_SCREENER_PATH',
    os.path.join(SCRIPT_DIR, 'test_output'),
)
TIMESTAMP = datetime.now().strftime('%Y%m%d_%H%M%S')

def out(name):
    return os.path.join(CSV_DIR, f"api_test_{TIMESTAMP}_{name}.csv")

# ---------------------------------------------------------------------------
# Test companies — public Estonian registry
# ---------------------------------------------------------------------------
# Primary company used for detailed checks
TEST_COMPANY = "12417834"

# Batch used for multi-company orchestrator tests
TEST_BATCH = [
    "12417834",  # primary
    "10364097",
    "10765896",
    "10102670",
    "10710010",
    "10068499",
    "10934695",
    "10055700",
    "16823962",
    "11458825",
]

PASS = 0
FAIL = 0
RESULTS_LOG = []


def report(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
    else:
        FAIL += 1
    status = "PASS" if passed else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{status}] {name}{suffix}")
    RESULTS_LOG.append({"test": name, "result": status, "detail": detail})


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def get_credentials():
    print("\nRIK API credentials (ariregxmlv6.rik.ee):")
    username = input("  Username: ").strip()
    password = input("  Password: ").strip()
    if not username or not password:
        print("  [ERROR] Credentials cannot be empty.")
        sys.exit(1)
    return username, password


# ===== 1. CREDENTIALS & CONFIG =====

def test_credentials(username, password):
    section("1. Credentials & Config")
    import rik_screener.api_workspace.config_auth as auth

    # Uninitialised state raises
    auth._config_instance = None
    try:
        auth.get_api_config()
        report("Uninitialized config raises ValueError", False)
    except ValueError:
        report("Uninitialized config raises ValueError", True)

    # set_api_config
    config = auth.set_api_config(username, password, rate_limit=20)
    report("set_api_config stores username", config.username == username)
    report("set_api_config stores password", config.password == password)
    report("set_api_config default rate limit", config.rate_limit == 20)
    report("Default base_url is RIK endpoint", "ariregxmlv6.rik.ee" in config.base_url)

    # get_api_config returns same instance
    retrieved = auth.get_api_config()
    report("get_api_config returns stored config", retrieved is config)

    return config


# ===== 2. VALIDATION =====

def test_validation():
    section("2. Company Code Validation")
    from rik_screener.api_workspace.utils import validate_company_code, validate_company_codes, format_progress

    report("7-digit code valid",           validate_company_code("1234567"))
    report("8-digit code valid",           validate_company_code("12345678"))
    report("Whitespace trimmed",           validate_company_code(" 1234567 "))
    report("6-digit rejected",             not validate_company_code("123456"))
    report("9-digit rejected",             not validate_company_code("123456789"))
    report("Letters rejected",             not validate_company_code("123ABC78"))
    report("Empty string rejected",        not validate_company_code(""))
    report("None rejected",                not validate_company_code(None))
    report("Integer rejected",             not validate_company_code(1234567))
    report("Special chars rejected",       not validate_company_code("1234-567"))

    mixed = ["1234567", "BAD", "12345678", "", "123"]
    valid = validate_company_codes(mixed)
    report("Batch keeps valid codes",      set(valid) == {"1234567", "12345678"})

    report("format_progress output",       "3/10 (30.0%)" in format_progress(3, 10))
    report("format_progress zero total",   "0.0%" in format_progress(0, 0))


# ===== 3. SOAP CLIENT =====

def test_soap_client(username, password):
    section("3. SOAP Client — Envelope & Real Request")
    import rik_screener.api_workspace.config_auth as auth
    from rik_screener.api_workspace.soap_client import SOAPClient

    auth.set_api_config(username, password, rate_limit=20)
    client = SOAPClient()

    # Envelope structure checks (no network)
    envelope = client.build_envelope("testOp", "<prod:param>value</prod:param>")
    report("Envelope contains operation name",   "testOp" in envelope)
    report("Envelope contains username",         username in envelope)
    report("Envelope contains body content",     "<prod:param>value</prod:param>" in envelope)
    report("Envelope has XML declaration",       '<?xml version="1.0"' in envelope)
    report("Envelope has SOAP namespace",        "schemas.xmlsoap.org/soap/envelope/" in envelope)

    # XML-escape in call_endpoint
    import unittest.mock as mock
    with mock.patch.object(client, 'send_request', return_value=None) as m:
        client.call_endpoint("op", {"k": "A & B <x>"})
        built = m.call_args[0][0]
        report("call_endpoint escapes & and <",  "A &amp; B &lt;x&gt;" in built)

    # Real network call — annual reports list for test company
    print(f"\n  Making live call for company {TEST_COMPANY}...")
    xml_result = client.call_endpoint(
        "majandusaastaAruanneteLoetelu_v1",
        {"ariregistri_kood": TEST_COMPANY}
    )
    report("Live call returns XML element",      xml_result is not None and isinstance(xml_result, ET.Element))

    # Invalid company code — should return element with no data (not crash)
    xml_invalid = client.call_endpoint(
        "majandusaastaAruanneteLoetelu_v1",
        {"ariregistri_kood": "99999999"}
    )
    report("Invalid company: does not crash",    True)  # if we reached here, no exception

    return xml_result


# ===== 4. ENDPOINTS =====

def test_endpoints(username, password):
    section("4. Endpoints — Raw XML Responses")
    import rik_screener.api_workspace.config_auth as auth
    from rik_screener.api_workspace.endpoints import (
        get_annual_reports_list,
        get_company_basic_info,
        get_financial_statement_details,
    )

    auth.set_api_config(username, password, rate_limit=20)

    # Annual reports list
    print(f"  Fetching annual reports list for {TEST_COMPANY}...")
    reports_xml = get_annual_reports_list(TEST_COMPANY)
    report("get_annual_reports_list returns XML", reports_xml is not None)

    # Company basic info
    print(f"  Fetching company basic info for {TEST_COMPANY}...")
    info_xml = get_company_basic_info(TEST_COMPANY)
    report("get_company_basic_info returns XML",  info_xml is not None)

    # Financial statement details — need a statement code first
    statement_xml = None
    if reports_xml is not None:
        try:
            from rik_screener.api_workspace.data_processors import extract_statement_code
            stmt_code = extract_statement_code(reports_xml, [2023], "BS")
            print(f"  Fetching BS statement (code: {stmt_code}) for 2023...")
            statement_xml = get_financial_statement_details(TEST_COMPANY, stmt_code, 2023)
            report("get_financial_statement_details returns XML", statement_xml is not None)
        except ValueError as e:
            report("get_financial_statement_details returns XML", False, str(e))
    else:
        report("get_financial_statement_details returns XML", False, "skipped — no reports XML")

    return reports_xml, info_xml, statement_xml


# ===== 5. XML PARSING =====

def test_parsing(reports_xml, info_xml, statement_xml):
    section("5. XML Parsing — Annual Reports, Company Info, Financials")
    from rik_screener.api_workspace.data_processors import (
        parse_annual_reports_response,
        parse_company_info_response,
        parse_financial_statement_response,
    )

    # Parse annual reports
    parsed_report = None
    if reports_xml is not None:
        parsed_report = parse_annual_reports_response(reports_xml, TEST_COMPANY)
        report("Parse annual reports: returns dict",     isinstance(parsed_report, dict))
        report("Parse annual reports: has company_code", parsed_report is not None and parsed_report.get('company_code') == TEST_COMPANY)
        report("Parse annual reports: has latest_year",  parsed_report is not None and parsed_report.get('latest_year') is not None)
        report("Parse annual reports: has period_start", parsed_report is not None and parsed_report.get('period_start') is not None)
        report("Parse annual reports: has period_end",   parsed_report is not None and parsed_report.get('period_end') is not None)
        if parsed_report:
            print(f"    → latest_year={parsed_report['latest_year']}, "
                  f"period={parsed_report['period_start']} – {parsed_report['period_end']}")
    else:
        report("Parse annual reports: skipped (no XML)", False)

    # Parse company info
    company_name = None
    if info_xml is not None:
        company_name = parse_company_info_response(info_xml, TEST_COMPANY)
        report("Parse company info: returns string",     isinstance(company_name, str))
        report("Parse company info: non-empty name",     company_name is not None and len(company_name) > 0)
        if company_name:
            print(f"    → company_name='{company_name}'")
    else:
        report("Parse company info: skipped (no XML)", False)

    # Parse financial statement
    stmt_df = None
    if statement_xml is not None:
        stmt_df = parse_financial_statement_response(statement_xml)
        report("Parse financial statement: returns DataFrame",  isinstance(stmt_df, pd.DataFrame))
        report("Parse financial statement: non-empty",          not stmt_df.empty)
        report("Parse financial statement: has line_code col",  'line_code' in stmt_df.columns)
        report("Parse financial statement: has line_name col",  'line_name' in stmt_df.columns)
        report("Parse financial statement: numeric values",
               stmt_df.select_dtypes(include='number').shape[1] > 0)
        print(f"    → {len(stmt_df)} lines, columns: {list(stmt_df.columns)}")
    else:
        report("Parse financial statement: skipped (no XML)", False)

    return parsed_report, company_name, stmt_df


# ===== 6. STATEMENT CODE EXTRACTION =====

def test_statement_codes(reports_xml):
    section("6. Statement Code Extraction & Consistency")
    from rik_screener.api_workspace.data_processors import (
        extract_statement_code,
        parse_statement_codes_by_year,
        parse_consolidation_status_by_year,
    )

    if reports_xml is None:
        report("Skipped — no reports XML", False)
        return

    # extract_statement_code for each type
    for stmt_type in ["BS", "IS", "CF"]:
        try:
            code = extract_statement_code(reports_xml, [2023], stmt_type)
            report(f"extract_statement_code {stmt_type}",
                   isinstance(code, str) and len(code) > 0, f"code={code}")
        except ValueError as e:
            report(f"extract_statement_code {stmt_type}", False, str(e))

    # Consistency check across two years
    try:
        code_2y = extract_statement_code(reports_xml, [2023, 2022], "BS")
        report("BS code consistent across 2022-2023",
               isinstance(code_2y, str), f"code={code_2y}")
    except ValueError as e:
        report("BS code consistent across 2022-2023", False, str(e))

    # parse_statement_codes_by_year
    codes = parse_statement_codes_by_year(reports_xml, 2023, 2021, ["BS", "IS", "CF"])
    report("Codes by year: BS list returned",  isinstance(codes.get("BS"), list))
    report("Codes by year: IS list returned",  isinstance(codes.get("IS"), list))
    report("Codes by year: CF list returned",  isinstance(codes.get("CF"), list))
    print(f"    → BS codes 2023→2021: {codes.get('BS')}")

    # parse_consolidation_status_by_year
    con_status = parse_consolidation_status_by_year(reports_xml, 2023, 2021)
    report("Consolidation status by year returned", isinstance(con_status, dict))
    print(f"    → consolidation by year: {con_status}")


# ===== 7. ORCHESTRATOR — get_latest_reports_info =====

def test_orchestrator_reports(username, password):
    section("7. Orchestrator — get_latest_reports_info")
    from rik_screener.api_workspace.main_orchestrator import get_latest_reports_info

    # Single company without names
    print(f"  Single company, no names...")
    df = get_latest_reports_info([TEST_COMPANY], username, password, include_names=False)
    report("Single company: returns DataFrame",    isinstance(df, pd.DataFrame))
    report("Single company: one row",              len(df) == 1)
    report("Single company: has latest_year",      'latest_year' in df.columns)
    report("Single company: has period columns",   'period_start' in df.columns and 'period_end' in df.columns)

    # Single company with names
    print(f"  Single company, with names...")
    df_named = get_latest_reports_info([TEST_COMPANY], username, password, include_names=True)
    report("With names: has company_name col",     'company_name' in df_named.columns)
    report("With names: name is non-empty string",
           isinstance(df_named['company_name'].iloc[0], str) and len(df_named['company_name'].iloc[0]) > 0)

    # Batch
    print(f"  Batch of {len(TEST_BATCH)} companies...")
    df_batch = get_latest_reports_info(TEST_BATCH, username, password, include_names=False)
    report("Batch: returns DataFrame",             isinstance(df_batch, pd.DataFrame))
    report("Batch: at least one company returned", len(df_batch) >= 1)
    report("Batch: all codes are strings",
           df_batch['company_code'].dtype == object or df_batch['company_code'].apply(lambda x: isinstance(x, str)).all())

    # Save
    df_batch_named = get_latest_reports_info(TEST_BATCH, username, password, include_names=True)
    df_batch_named.to_csv(out("latest_reports"), index=False, encoding='utf-8-sig')
    report("Batch with names saved to CSV",        os.path.exists(out("latest_reports")))
    print(f"    → saved to api_test_{TIMESTAMP}_latest_reports.csv  ({len(df_batch_named)} rows)")

    return df_batch


# ===== 8. ORCHESTRATOR — get_financial_statements =====

def test_orchestrator_statements(username, password):
    section("8. Orchestrator — get_financial_statements")
    from rik_screener.api_workspace.main_orchestrator import get_financial_statements

    results = {}

    for stmt_type in ["BS", "IS"]:
        print(f"  Fetching {stmt_type} for {TEST_COMPANY}, 2023-2022...")
        df = get_financial_statements(
            [TEST_COMPANY], username, password,
            statement_type=stmt_type,
            starting_year=2023,
            num_requests=2,
            year_step=1,
        )
        report(f"{stmt_type}: returns DataFrame",    isinstance(df, pd.DataFrame))
        report(f"{stmt_type}: non-empty",            not df.empty)
        report(f"{stmt_type}: has line_name col",    'line_name' in df.columns)
        report(f"{stmt_type}: multi-year columns",
               sum(1 for c in df.columns if str(c).startswith('2023') or str(c).startswith('2022')) >= 1)
        print(f"    → {len(df)} rows, {len(df.columns)} columns")

        df_save = df.reset_index()
        df_save.to_csv(out(f"statements_{stmt_type.lower()}"), index=False, encoding='utf-8-sig')
        report(f"{stmt_type}: saved to CSV",
               os.path.exists(out(f"statements_{stmt_type.lower()}")))
        results[stmt_type] = df

    return results


# ===== 9. ORCHESTRATOR — check_statement_consistency =====

def test_orchestrator_consistency(username, password):
    section("9. Orchestrator — check_statement_consistency")
    from rik_screener.api_workspace.main_orchestrator import check_statement_consistency

    output_path = out("consistency")
    print(f"  Checking consistency 2023→2021 for {len(TEST_BATCH)} companies...")

    results = check_statement_consistency(
        TEST_BATCH, username, password,
        target_year=2023,
        end_year=2021,
        statement_types=["BS", "IS", "CF"],
        output_file=output_path,
    )

    report("Consistency: returns dict",              isinstance(results, dict))
    report("Consistency: primary company present",   TEST_COMPANY in results)
    report("Consistency: CSV written",               os.path.exists(output_path))

    if TEST_COMPANY in results:
        answer, arrays, cons_status = results[TEST_COMPANY]
        report("Consistency: answer is Yes or No",   answer in ("Yes", "No"))
        report("Consistency: 3 statement arrays",    len(arrays) == 3)
        report("Consistency: consolidation string",  isinstance(cons_status, str) and len(cons_status) > 0)
        print(f"    → {TEST_COMPANY}: consistent={answer}, consolidation='{cons_status}'")
        print(f"    → BS codes: {arrays[0]}")

    # Summary of all companies
    summary_rows = []
    for code, (answer, arrays, cons) in results.items():
        summary_rows.append({
            "company_code": code,
            "consistent": answer,
            "consolidation": cons,
            "BS_codes": str(arrays[0]) if arrays else "",
            "IS_codes": str(arrays[1]) if len(arrays) > 1 else "",
            "CF_codes": str(arrays[2]) if len(arrays) > 2 else "",
        })

    summary_df = pd.DataFrame(summary_rows)
    print(f"\n  Consistency summary ({len(summary_df)} companies):")
    for _, row in summary_df.iterrows():
        print(f"    {row['company_code']}: {row['consistent']}  [{row['consolidation']}]")

    return results


# ===== 10. FINAL OUTPUT SUMMARY =====

def save_test_log():
    section("10. Test Log")
    log_df = pd.DataFrame(RESULTS_LOG)
    log_path = out("test_log")
    log_df.to_csv(log_path, index=False, encoding='utf-8-sig')
    print(f"  Full test log saved → api_test_{TIMESTAMP}_test_log.csv")
    print(f"  Output folder: {os.path.abspath(CSV_DIR)}")


# ===== RUN =====

if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("  RIK SCREENER — LIVE API TEST SUITE")
    print("=" * 60)
    print("  This script makes real calls to ariregxmlv6.rik.ee")
    print(f"  Output will be saved to: {os.path.abspath(CSV_DIR)}")

    username, password = get_credentials()

    # Run all sections — collect crashes without stopping the whole run
    sections = [
        ("credentials",    lambda: test_credentials(username, password)),
        ("validation",     test_validation),
        ("soap_client",    lambda: test_soap_client(username, password)),
        ("endpoints",      lambda: test_endpoints(username, password)),
    ]

    ctx = {}

    for key, fn in sections:
        try:
            ctx[key] = fn()
        except Exception as e:
            FAIL += 1
            print(f"\n  [CRASH] {key}: {e}")
            traceback.print_exc()
            ctx[key] = None

    # Parsing depends on endpoint results
    reports_xml, info_xml, statement_xml = (None, None, None)
    if ctx.get("endpoints"):
        reports_xml, info_xml, statement_xml = ctx["endpoints"]

    try:
        test_parsing(reports_xml, info_xml, statement_xml)
    except Exception as e:
        FAIL += 1
        print(f"\n  [CRASH] parsing: {e}")
        traceback.print_exc()

    try:
        test_statement_codes(reports_xml)
    except Exception as e:
        FAIL += 1
        print(f"\n  [CRASH] statement_codes: {e}")
        traceback.print_exc()

    try:
        test_orchestrator_reports(username, password)
    except Exception as e:
        FAIL += 1
        print(f"\n  [CRASH] orchestrator_reports: {e}")
        traceback.print_exc()

    try:
        test_orchestrator_statements(username, password)
    except Exception as e:
        FAIL += 1
        print(f"\n  [CRASH] orchestrator_statements: {e}")
        traceback.print_exc()

    try:
        test_orchestrator_consistency(username, password)
    except Exception as e:
        FAIL += 1
        print(f"\n  [CRASH] orchestrator_consistency: {e}")
        traceback.print_exc()

    save_test_log()

    print(f"\n{'='*60}")
    print(f"  RESULTS: {PASS} passed, {FAIL} failed, {PASS + FAIL} total")
    print(f"{'='*60}\n")

    sys.exit(0 if FAIL == 0 else 1)
