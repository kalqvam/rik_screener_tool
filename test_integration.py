"""
Integration test for rik_screener — API calls + CSV pipeline.

Usage:
    python test_integration.py

You will be prompted for credentials and the path to your CSV data files.
"""

import os
import sys
import traceback
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd

PASS = 0
FAIL = 0
RESULTS_LOG = []  # Collects (part, name, passed, detail) for output files


def report(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
    else:
        FAIL += 1
    status = "PASS" if passed else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{status}] {name}{suffix}")
    RESULTS_LOG.append((name, passed, detail))


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# Known test companies (public Estonian registry data)
# ---------------------------------------------------------------------------
TEST_COMPANY = "12417834"
TEST_BATCH = [
    "12417834", "10364097", "10765896", "10102670", "10710010",
    "10068499", "10934695", "10055700", "16823962", "11458825",
    "99999999",
]
INVALID_COMPANY = "99999999"


def get_credentials():
    print("\nRIK API credentials (ariregxmlv6.rik.ee):")
    username = input("  Username: ").strip()
    password = input("  Password: ").strip()
    return username, password


def get_data_path():
    path = input("\nPath to CSV data files (general_data.csv, revenues.csv, etc.):\n  > ").strip().strip('"')
    if not os.path.isdir(path):
        print(f"  WARNING: '{path}' is not a valid directory")
    return path


def setup_config(data_path):
    """Configure the screener to use the given data path."""
    import rik_screener.utils.config as config_mod
    config_mod._config_instance = config_mod.ConfigManager(base_path=data_path)
    return config_mod._config_instance


# =====================================================================
#  PART A: API TESTS (sections 1-9, same as before)
# =====================================================================

def test_auth_and_connection(username, password):
    section("1. Auth & Connection")
    from rik_screener.api_workspace.config_auth import set_api_config, get_api_config

    config = set_api_config(username, password, rate_limit=20)
    report("set_api_config creates config", config is not None)
    report("Config stores username", config.username == username)
    report("Config rate limit", config.rate_limit == 20)
    report("get_api_config returns same instance", get_api_config() is config)


def test_soap_client(username, password):
    section("2. SOAP Client")
    from rik_screener.api_workspace.config_auth import set_api_config
    from rik_screener.api_workspace.soap_client import SOAPClient

    set_api_config(username, password)
    client = SOAPClient()

    envelope = client.build_envelope("testOp", "<prod:foo>bar</prod:foo>")
    report("build_envelope produces XML", "soapenv:Envelope" in envelope and "testOp" in envelope)
    report("Envelope contains credentials", username in envelope and password in envelope)

    result = client.call_endpoint("majandusaastaAruanneteLoetelu_v1", {
        "ariregistri_kood": TEST_COMPANY
    })
    report("call_endpoint returns XML Element",
           result is not None and isinstance(result, ET.Element),
           f"type={type(result).__name__}")

    client.call_endpoint("majandusaastaAruanneteLoetelu_v1", {
        "ariregistri_kood": INVALID_COMPANY
    })
    report("Invalid company does not crash", True)


def test_endpoints(username, password):
    section("3. Individual Endpoints")
    from rik_screener.api_workspace.config_auth import set_api_config
    from rik_screener.api_workspace.endpoints import (
        get_annual_reports_list, get_company_basic_info, get_financial_statement_details,
    )

    set_api_config(username, password)

    xml = get_annual_reports_list(TEST_COMPANY)
    report("get_annual_reports_list returns data", xml is not None)

    xml_info = get_company_basic_info(TEST_COMPANY)
    report("get_company_basic_info returns data", xml_info is not None)

    if xml is not None:
        from rik_screener.api_workspace.data_processors import extract_statement_code
        try:
            stmt_code = extract_statement_code(xml, [2023], "BS")
            report("extract_statement_code finds BS code",
                   stmt_code is not None, f"code={stmt_code}")
            xml_stmt = get_financial_statement_details(TEST_COMPANY, stmt_code, 2023)
            report("get_financial_statement_details returns data", xml_stmt is not None)
        except ValueError as e:
            report("extract_statement_code", False, str(e))


def test_data_processors(username, password):
    section("4. Data Processors (XML Parsing)")
    from rik_screener.api_workspace.config_auth import set_api_config
    from rik_screener.api_workspace.endpoints import (
        get_annual_reports_list, get_company_basic_info, get_financial_statement_details,
    )
    from rik_screener.api_workspace.data_processors import (
        parse_annual_reports_response, parse_company_info_response,
        parse_financial_statement_response, parse_statement_codes_by_year,
        parse_consolidation_status_by_year, extract_statement_code,
    )

    set_api_config(username, password)

    xml = get_annual_reports_list(TEST_COMPANY)
    if xml is None:
        report("(skipped - no XML response)", False)
        return

    report_info = parse_annual_reports_response(xml, TEST_COMPANY)
    report("parse_annual_reports_response returns dict",
           isinstance(report_info, dict) and 'company_code' in report_info,
           f"keys={list(report_info.keys()) if report_info else 'None'}")

    xml_info = get_company_basic_info(TEST_COMPANY)
    if xml_info is not None:
        name = parse_company_info_response(xml_info, TEST_COMPANY)
        report("parse_company_info_response returns name",
               name is not None and len(name) > 0, f"name={name}")

    codes = parse_statement_codes_by_year(xml, 2023, 2020, ["BS", "IS", "CF"])
    report("parse_statement_codes_by_year returns dict",
           isinstance(codes, dict) and len(codes) > 0, f"types={list(codes.keys())}")

    consolidation = parse_consolidation_status_by_year(xml, 2023, 2020)
    report("parse_consolidation_status_by_year returns dict",
           isinstance(consolidation, dict), f"years={list(consolidation.keys())}")

    try:
        stmt_code = extract_statement_code(xml, [2023], "BS")
        xml_stmt = get_financial_statement_details(TEST_COMPANY, stmt_code, 2023)
        if xml_stmt is not None:
            df = parse_financial_statement_response(xml_stmt)
            report("parse_financial_statement_response returns DataFrame",
                   isinstance(df, pd.DataFrame) and len(df) > 0,
                   f"{len(df)} rows, cols={list(df.columns)}")
    except ValueError as e:
        report("(skipped statement parse)", False, str(e))


def test_get_latest_reports(username, password):
    section("5. get_latest_reports_info (single + batch)")
    from rik_screener.api_workspace.main_orchestrator import get_latest_reports_info

    df = get_latest_reports_info([TEST_COMPANY], username, password, include_names=True)
    report("Single company returns DataFrame",
           isinstance(df, pd.DataFrame) and len(df) > 0, f"{len(df)} rows")
    if len(df) > 0:
        report("Has company_code column", 'company_code' in df.columns)
        has_name = 'company_name' in df.columns and df['company_name'].notna().any()
        report("include_names adds company_name", has_name,
               f"name={df['company_name'].iloc[0] if has_name else 'N/A'}")

    df_invalid = get_latest_reports_info([INVALID_COMPANY], username, password)
    report("Invalid company returns empty/graceful", isinstance(df_invalid, pd.DataFrame))

    df_batch = get_latest_reports_info(TEST_BATCH, username, password, include_names=False)
    report("Batch returns DataFrame", isinstance(df_batch, pd.DataFrame),
           f"{len(df_batch)} rows for {len(TEST_BATCH)} companies")


def test_get_financial_statements(username, password):
    section("6. get_financial_statements (BS + IS)")
    from rik_screener.api_workspace.main_orchestrator import get_financial_statements

    df_bs = get_financial_statements(
        [TEST_COMPANY], username, password,
        statement_type="BS", starting_year=2023, num_requests=1
    )
    report("BS statement returns DataFrame",
           isinstance(df_bs, pd.DataFrame) and len(df_bs) > 0, f"{len(df_bs)} line items")
    if len(df_bs) > 0:
        report("Has line_name column", 'line_name' in df_bs.columns)

    df_is = get_financial_statements(
        [TEST_COMPANY], username, password,
        statement_type="IS", starting_year=2023, num_requests=1
    )
    report("IS statement returns DataFrame",
           isinstance(df_is, pd.DataFrame) and len(df_is) > 0, f"{len(df_is)} line items")

    df_multi = get_financial_statements(
        [TEST_COMPANY], username, password,
        statement_type="BS", starting_year=2023, num_requests=2, year_step=1
    )
    report("Multi-year BS returns data",
           isinstance(df_multi, pd.DataFrame) and len(df_multi) > 0, f"{len(df_multi)} line items")


def test_check_consistency(username, password):
    section("7. check_statement_consistency")
    from rik_screener.api_workspace.main_orchestrator import check_statement_consistency

    results = check_statement_consistency(
        [TEST_COMPANY], username, password,
        target_year=2023, end_year=2020, statement_types=["BS", "IS"]
    )
    report("Returns results dict", isinstance(results, dict) and TEST_COMPANY in results)

    if TEST_COMPANY in results:
        answer, arrays, consolidation = results[TEST_COMPANY]
        report("Answer is Yes or No", answer in ("Yes", "No"), f"answer={answer}")
        report("Arrays match statement types", len(arrays) == 2,
               f"BS codes={arrays[0]}, IS codes={arrays[1]}")
        report("Consolidation status returned",
               isinstance(consolidation, str) and len(consolidation) > 0, f"status={consolidation}")


def test_error_handling(username, password):
    section("8. Error Handling & Edge Cases")
    from rik_screener.api_workspace.main_orchestrator import (
        get_latest_reports_info, get_financial_statements, check_statement_consistency,
    )

    df = get_latest_reports_info([], username, password)
    report("Empty company list -> empty DataFrame",
           isinstance(df, pd.DataFrame) and len(df) == 0)

    try:
        check_statement_consistency(
            [TEST_COMPANY], username, password, target_year=2020, end_year=2023
        )
        report("Reversed year range raises error", False)
    except ValueError:
        report("Reversed year range raises ValueError", True)

    try:
        get_financial_statements([TEST_COMPANY], username, password, num_requests=0)
        report("num_requests=0 raises error", False)
    except ValueError:
        report("num_requests=0 raises ValueError", True)

    from rik_screener.api_workspace.config_auth import set_api_config
    set_api_config("invalid_user", "invalid_pass")
    from rik_screener.api_workspace.endpoints import get_annual_reports_list
    get_annual_reports_list(TEST_COMPANY)
    report("Bad credentials handled gracefully (no crash)", True)
    set_api_config(username, password)


def test_rate_limiter(username, password):
    section("9. Rate Limiter")
    import time
    from rik_screener.api_workspace.config_auth import set_api_config

    config = set_api_config(username, password, rate_limit=20)
    config.last_request_time = time.time()
    start = time.time()
    config.wait_for_rate_limit()
    elapsed = time.time() - start
    expected_wait = 60.0 / 20
    report("Rate limiter enforces delay",
           elapsed >= (expected_wait - 0.5), f"waited {elapsed:.1f}s, expected ~{expected_wait:.1f}s")


# =====================================================================
#  PART B: CSV PIPELINE TESTS (sections 10-18)
# =====================================================================

def test_csv_file_discovery(data_path):
    """Check which expected CSV files exist."""
    section("10. CSV File Discovery")

    expected_files = {
        'general_data.csv': 'Company registry data',
        'revenues.csv': 'Industry revenue codes',
        'legal_data.csv': 'Legal registry (names, dates)',
        'emtak_2025.csv': 'EMTAK activity code descriptions',
        'shareholders.json': 'Shareholder ownership data',
    }

    from rik_screener.utils.file_operations import resolve_filename

    found_files = {}
    for fname, desc in expected_files.items():
        resolved = resolve_filename(fname)
        exists = os.path.exists(os.path.join(data_path, resolved))
        found_files[fname] = exists
        detail = f"{desc} -> {resolved}" if resolved != fname else desc
        report(f"{fname} exists", exists, detail)

    # Check for financial year files (use resolver to find real names)
    financial_years = []
    for year in range(2018, 2026):
        resolved = resolve_filename(f"financials_{year}.csv")
        if os.path.exists(os.path.join(data_path, resolved)):
            financial_years.append(year)

    report("Financial year files found",
           len(financial_years) > 0,
           f"years: {financial_years}")

    return found_files, financial_years


def test_csv_read_general_data(data_path):
    """Read general_data.csv and verify structure."""
    section("11. general_data.csv - Read & Validate")
    from rik_screener.utils.file_operations import safe_read_csv

    df = safe_read_csv('general_data.csv')
    report("File reads successfully", df is not None)
    if df is None:
        return None

    report(f"Row count", True, f"{len(df)} rows")

    expected_cols = ['report_id', 'registrikood', 'aruandeaast', 'staatus']
    for col in expected_cols:
        report(f"Has column '{col}'", col in df.columns)

    # Check for legal form column (may have whitespace variations)
    legal_form_col = None
    for col in df.columns:
        if 'iguslik' in col.lower():
            legal_form_col = col
            break
    report("Has legal form column", legal_form_col is not None,
           f"found as '{legal_form_col}'" if legal_form_col else "missing")

    # Data quality
    report("report_id has no nulls", df['report_id'].notna().all() if 'report_id' in df.columns else False)

    if 'aruandeaast' in df.columns:
        years = sorted(df['aruandeaast'].dropna().unique())
        report("Year range", True, f"{min(years)}-{max(years)}, {len(years)} distinct years")

    if legal_form_col:
        forms = df[legal_form_col].dropna().unique().tolist()
        report("Legal forms found", len(forms) > 0, f"{forms[:5]}")

    return df


def test_csv_read_financials(data_path, financial_years):
    """Read a financial year file and verify structure."""
    if not financial_years:
        section("12. financials_YYYY.csv - SKIPPED (no files)")
        return None

    year = financial_years[-1]  # Use most recent
    section(f"12. financials_{year}.csv - Read & Validate")
    from rik_screener.utils.file_operations import safe_read_csv

    df = safe_read_csv(f'financials_{year}.csv')
    report("File reads successfully", df is not None)
    if df is None:
        return None

    report(f"Row count", True, f"{len(df)} rows")

    expected_cols = ['report_id', 'elemendi_label', 'vaartus']
    for col in expected_cols:
        report(f"Has column '{col}'", col in df.columns)

    if 'vaartus' in df.columns:
        numeric_count = pd.to_numeric(df['vaartus'], errors='coerce').notna().sum()
        total = len(df)
        report("'vaartus' column is numeric",
               numeric_count > total * 0.5,
               f"{numeric_count}/{total} parseable as numbers")

    if 'elemendi_label' in df.columns:
        labels = df['elemendi_label'].dropna().unique()
        # Check if any standard financial items are present
        standard_items = ["Müügitulu", "Varad", "Omakapital"]
        found = [item for item in standard_items if any(item in label for label in labels)]
        report("Contains standard financial items",
               len(found) > 0, f"found: {found}")

    return df


def test_csv_read_revenues(data_path):
    """Read revenues.csv and verify structure."""
    section("13. revenues.csv - Read & Validate")
    from rik_screener.utils.file_operations import safe_read_csv

    df = safe_read_csv('revenues.csv')
    report("File reads successfully", df is not None)
    if df is None:
        return None

    report(f"Row count", True, f"{len(df)} rows")

    expected_cols = ['report_id', 'emtak']
    for col in expected_cols:
        report(f"Has column '{col}'", col in df.columns)

    # Check for main activity column
    main_activity_col = None
    for col in df.columns:
        if 'hitegevusala' in col.lower():
            main_activity_col = col
            break
    report("Has main activity column", main_activity_col is not None,
           f"found as '{main_activity_col}'" if main_activity_col else "missing")

    if main_activity_col:
        vals = df[main_activity_col].dropna().unique().tolist()
        report("Main activity values", True, f"{vals[:5]}")

    if 'emtak' in df.columns:
        sample = df['emtak'].dropna().head(5).tolist()
        report("EMTAK codes sample", True, f"{sample}")

    return df


def test_csv_read_legal_data(data_path):
    """Read legal_data.csv and verify structure."""
    section("14. legal_data.csv - Read & Validate")
    from rik_screener.utils.file_operations import safe_read_csv

    df = safe_read_csv('legal_data.csv', separator=';')
    report("File reads successfully", df is not None)
    if df is None:
        return None

    report(f"Row count", True, f"{len(df)} rows")

    expected_cols = ['ariregistri_kood', 'nimi']
    for col in expected_cols:
        report(f"Has column '{col}'", col in df.columns)

    # Check for date column
    date_col = None
    for col in df.columns:
        if 'esmakande' in col.lower():
            date_col = col
            break
    report("Has registration date column", date_col is not None,
           f"found as '{date_col}'" if date_col else "missing")

    if 'ariregistri_kood' in df.columns:
        sample = df['ariregistri_kood'].dropna().head(3).tolist()
        report("Registry code sample", True, f"{sample}")

    if 'nimi' in df.columns:
        sample = df['nimi'].dropna().head(3).tolist()
        report("Company name sample", True, f"{sample}")

    if date_col:
        sample = df[date_col].dropna().head(3).tolist()
        report("Date format sample", True, f"{sample}")

    return df


def test_csv_read_emtak(data_path):
    """Read emtak_2025.csv and verify structure."""
    section("15. emtak_2025.csv - Read & Validate")
    from rik_screener.utils.file_operations import safe_read_csv

    df = safe_read_csv('emtak_2025.csv', header=None, separator=',')
    report("File reads successfully", df is not None)
    if df is None:
        return None

    report(f"Row count", True, f"{len(df)} rows")
    report("Has exactly 2 columns", df.shape[1] == 2, f"found {df.shape[1]} columns")

    if df.shape[1] >= 2:
        df.columns = ['code', 'description']
        sample_codes = df['code'].dropna().head(5).tolist()
        sample_descs = df['description'].dropna().head(3).tolist()
        report("Code sample", True, f"{sample_codes}")
        report("Description sample", True, f"{sample_descs[:2]}")

    return df


def test_filter_companies(data_path, financial_years):
    """Test the filter_companies function on real data."""
    if not financial_years:
        section("16. filter_companies - SKIPPED (no financial years)")
        return None

    year = financial_years[-1]
    section(f"16. filter_companies (year={year})")
    from rik_screener.df_prep.general_filter import filter_companies

    df = filter_companies(year=year, legal_forms=["AS", "OÜ"], return_dataframe=True)
    report("filter_companies returns DataFrame",
           isinstance(df, pd.DataFrame) and len(df) > 0 if df is not None else False,
           f"{len(df)} companies" if df is not None and len(df) > 0 else "empty")

    if df is not None and len(df) > 0:
        report("Has company_code column", 'company_code' in df.columns)
        report("Has report_id column", 'report_id' in df.columns)
        report("No duplicate company codes",
               not df['company_code'].duplicated().any() if 'company_code' in df.columns else False)

    return df


def test_multi_year_merge(data_path, financial_years):
    """Test multi-year merge on real data."""
    if len(financial_years) < 2:
        section("17. merge_multiple_years - SKIPPED (need >= 2 years)")
        return None

    years = financial_years[-2:]  # Use last 2 years
    section(f"17. merge_multiple_years (years={years})")
    from rik_screener.df_prep.multi_year_merger import merge_multiple_years

    df = merge_multiple_years(years=years, legal_forms=["AS", "OÜ"], return_dataframe=True)
    report("merge_multiple_years returns DataFrame",
           isinstance(df, pd.DataFrame) and len(df) > 0 if df is not None else False,
           f"{len(df)} companies" if df is not None and len(df) > 0 else "empty")

    if df is not None and len(df) > 0:
        report("Has company_code column", 'company_code' in df.columns)
        for yr in years:
            report(f"Has report_id_{yr} column", f'report_id_{yr}' in df.columns)
        report("No duplicate company codes",
               not df['company_code'].duplicated().any() if 'company_code' in df.columns else False)

    return df


def test_financial_data_loading(data_path, financial_years):
    """Test loading and pivoting financial data."""
    if not financial_years:
        section("18. load_financial_data - SKIPPED")
        return None

    year = financial_years[-1]
    section(f"18. load_financial_data (year={year})")
    from rik_screener.criteria_setup.calculation_utils.data_loaders import load_financial_data
    from rik_screener.utils.config import get_config

    config = get_config()
    financial_items = config.get_default('financial_items', [])
    report("Config has financial_items", len(financial_items) > 0,
           f"{len(financial_items)} items")

    df = load_financial_data(year, financial_items)
    report("load_financial_data returns DataFrame",
           isinstance(df, pd.DataFrame) and len(df) > 0 if df is not None else False,
           f"{len(df)} companies" if df is not None and len(df) > 0 else "empty/None")

    if df is not None and len(df) > 0:
        report("Has report_id column", 'report_id' in df.columns)

        # Check that financial item columns are present and numeric
        found_items = []
        numeric_items = []
        for item in financial_items:
            col = f"{item}_{year}"
            if col in df.columns:
                found_items.append(item)
                if df[col].dtype in [np.float64, np.int64, float, int]:
                    numeric_items.append(item)

        report("Financial item columns found",
               len(found_items) > 0,
               f"{len(found_items)}/{len(financial_items)}: {found_items[:3]}...")

        report("Financial columns are numeric",
               len(numeric_items) == len(found_items),
               f"{len(numeric_items)}/{len(found_items)} numeric")

        # Check for NaN ratio (are values actually populated?)
        if found_items:
            sample_col = f"{found_items[0]}_{year}"
            non_null = df[sample_col].notna().sum()
            report(f"'{found_items[0]}' has values",
                   non_null > 0,
                   f"{non_null}/{len(df)} non-null")

    return df


def test_calculate_ratios(data_path, financial_years):
    """Test full ratio calculation pipeline."""
    if len(financial_years) < 2:
        section("19. calculate_ratios - SKIPPED (need >= 2 years)")
        return None

    years = financial_years[-2:]
    section(f"19. calculate_ratios (years={years})")
    from rik_screener.df_prep.multi_year_merger import merge_multiple_years
    from rik_screener.criteria_setup.calculations import calculate_ratios
    from rik_screener.criteria_setup.calculation_utils.standard_formulas import get_standard_formulas

    merged_df = merge_multiple_years(years=years, legal_forms=["AS", "OÜ"], return_dataframe=True)
    if merged_df is None or merged_df.empty:
        report("(skipped - no merged data)", False)
        return None

    formulas = get_standard_formulas(years)
    report("Standard formulas generated", len(formulas) > 0, f"{len(formulas)} formulas")

    result = calculate_ratios(
        input_data=merged_df,
        years=years,
        formulas=formulas,
        use_standard_formulas=False,
        return_dataframe=True
    )
    report("calculate_ratios returns DataFrame",
           isinstance(result, pd.DataFrame) and len(result) > 0 if result is not None else False,
           f"{len(result)} companies" if result is not None and len(result) > 0 else "empty")

    if result is not None and len(result) > 0:
        ratio_cols = [c for c in result.columns if 'ebitda_margin' in c or 'roe' in c or 'roa' in c]
        report("Ratio columns created", len(ratio_cols) > 0,
               f"{ratio_cols[:5]}")

        if ratio_cols:
            col = ratio_cols[0]
            non_null = result[col].notna().sum()
            report(f"Ratio '{col}' has values",
                   non_null > 0,
                   f"{non_null}/{len(result)} non-null")

        report("Has investment_vehicle flag", 'investment_vehicle' in result.columns)

    return result


def test_industry_codes(data_path, financial_years):
    """Test industry code enrichment."""
    if not financial_years:
        section("20. add_industry_classifications - SKIPPED")
        return None

    years = financial_years[-2:] if len(financial_years) >= 2 else financial_years
    section(f"20. add_industry_classifications (years={years})")
    from rik_screener.df_prep.multi_year_merger import merge_multiple_years
    from rik_screener.add_info.industry_codes import add_industry_classifications

    merged_df = merge_multiple_years(years=years, legal_forms=["AS", "OÜ"], return_dataframe=True)
    if merged_df is None or merged_df.empty:
        report("(skipped - no merged data)", False)
        return None

    result = add_industry_classifications(
        input_data=merged_df,
        revenues_file='revenues.csv',
        years=years,
        return_dataframe=True
    )
    report("add_industry_classifications returns DataFrame",
           isinstance(result, pd.DataFrame) and len(result) > 0 if result is not None else False,
           f"{len(result)} companies" if result is not None and len(result) > 0 else "empty")

    if result is not None and len(result) > 0:
        for yr in years:
            col = f'industry_code_{yr}'
            if col in result.columns:
                non_null = result[col].notna().sum()
                report(f"industry_code_{yr} populated",
                       non_null > 0, f"{non_null}/{len(result)} non-null")

    return result


def test_company_age(data_path):
    """Test company age enrichment."""
    section("21. add_company_age")
    from rik_screener.add_info.company_age import add_company_age

    # Use the full test batch (minus invalid)
    valid_codes = [int(c) for c in TEST_BATCH if c != INVALID_COMPANY]
    test_df = pd.DataFrame({
        'company_code': valid_codes,
        'report_id_2023': list(range(1, len(valid_codes) + 1))
    })

    result = add_company_age(
        input_data=test_df,
        legal_data_file='legal_data.csv',
        return_dataframe=True
    )
    report("add_company_age returns DataFrame",
           isinstance(result, pd.DataFrame) and len(result) > 0 if result is not None else False)

    if result is not None and len(result) > 0:
        report("Has company_age_years column", 'company_age_years' in result.columns)
        if 'company_age_years' in result.columns:
            ages = result['company_age_years'].dropna()
            report("Ages are positive numbers",
                   len(ages) > 0 and (ages > 0).all(),
                   f"ages: {ages.tolist()}")


def test_company_names(data_path):
    """Test company name enrichment."""
    section("22. add_company_names")
    from rik_screener.post_processing.company_names import add_company_names

    valid_codes = [int(c) for c in TEST_BATCH if c != INVALID_COMPANY]
    test_df = pd.DataFrame({
        'company_code': valid_codes,
        'some_value': list(range(100, 100 + len(valid_codes)))
    })

    result = add_company_names(
        input_data=test_df,
        legal_data_file='legal_data.csv',
        return_dataframe=True
    )
    report("add_company_names returns DataFrame",
           isinstance(result, pd.DataFrame) and len(result) > 0 if result is not None else False)

    if result is not None and len(result) > 0:
        report("Has company_name column", 'company_name' in result.columns)
        if 'company_name' in result.columns:
            names = result['company_name'].dropna()
            report("Names populated",
                   len(names) > 0,
                   f"names: {names.tolist()}")
            report("company_name is first column",
                   result.columns[0] == 'company_name')


def test_emtak_descriptions(data_path, financial_years):
    """Test EMTAK description enrichment."""
    if not financial_years:
        section("23. add_emtak_descriptions - SKIPPED")
        return

    years = financial_years[-1:]
    section(f"23. add_emtak_descriptions (year={years[0]})")
    from rik_screener.add_info.emtak_descriptions import add_emtak_descriptions

    # Create test data with an industry code
    test_df = pd.DataFrame({
        'company_code': [1, 2, 3],
        f'industry_code_{years[0]}': ['62', '10', '47']
    })

    result = add_emtak_descriptions(
        input_data=test_df,
        emtak_file='emtak_2025.csv',
        years=years,
        return_dataframe=True
    )
    report("add_emtak_descriptions returns DataFrame",
           isinstance(result, pd.DataFrame) and len(result) > 0 if result is not None else False)

    if result is not None and len(result) > 0:
        desc_col = f'industry_description_{years[0]}'
        report(f"Has {desc_col} column", desc_col in result.columns)
        if desc_col in result.columns:
            descs = result[desc_col].dropna()
            report("Descriptions populated",
                   len(descs) > 0,
                   f"sample: {descs.head(2).tolist()}")


def test_filter_and_rank(data_path, financial_years):
    """Test filtering and ranking."""
    section("24. filter_and_rank")
    from rik_screener.post_processing.filtering import filter_and_rank

    # Create synthetic data with ratios
    test_df = pd.DataFrame({
        'company_code': [1, 2, 3, 4, 5],
        'company_name': ['A', 'B', 'C', 'D', 'E'],
        'ebitda_margin_2023': [0.15, 0.25, 0.05, 0.30, 0.12],
        'roe_2023': [0.10, 0.20, 0.03, 0.25, 0.08],
    })

    # Filter + sort + limit
    result = filter_and_rank(
        input_data=test_df,
        sort_column='ebitda_margin_2023',
        filters=[{'column': 'ebitda_margin_2023', 'min': 0.10}],
        ascending=False,
        top_n=3,
        export_columns=['company_code', 'company_name', 'ebitda_margin_2023'],
        return_dataframe=True
    )
    report("filter_and_rank returns DataFrame",
           isinstance(result, pd.DataFrame) and len(result) > 0 if result is not None else False)

    if result is not None and len(result) > 0:
        report("Filtered out low-margin companies",
               3 not in result['company_code'].values if 'company_code' in result.columns else False,
               f"{len(result)} remaining")
        report("Sorted descending by margin",
               result.iloc[0]['company_code'] == 4 if len(result) > 0 else False)
        report("Limited to top_n=3", len(result) <= 3)
        report("Export columns applied",
               list(result.columns) == ['company_code', 'company_name', 'ebitda_margin_2023'])


def test_csv_round_trip(data_path):
    """Test that write -> read preserves data with config separator and encoding."""
    section("25. CSV Round-Trip (separator + encoding)")
    from rik_screener.utils.file_operations import safe_read_csv, safe_write_csv
    import tempfile

    df = pd.DataFrame({
        'company_code': [10257326, 10223439],
        'Müügitulu_2023': [500000.50, 300000.75],
        'Ärikasum (kahjum)_2023': [-5000.25, 12000.00],
        'name': ['AKTSIASELTS TALLINNA VESI', 'Test OÜ']
    })

    with tempfile.TemporaryDirectory() as tmpdir:
        import rik_screener.utils.config as config_mod
        old_config = config_mod._config_instance
        config_mod._config_instance = config_mod.ConfigManager(base_path=tmpdir)

        # Write with default settings (should use ; separator, utf-8-sig)
        ok = safe_write_csv(df, 'roundtrip_test.csv', base_path=tmpdir)
        report("Write succeeds", ok)

        # Read back
        result = safe_read_csv('roundtrip_test.csv', base_path=tmpdir)
        report("Read back succeeds", result is not None)

        if result is not None:
            report("Row count preserved", len(result) == len(df))
            report("Column count preserved", len(result.columns) == len(df.columns))

            # Check numeric values survived round-trip
            if 'Müügitulu_2023' in result.columns:
                val = result['Müügitulu_2023'].iloc[0]
                report("Numeric values preserved",
                       abs(val - 500000.50) < 0.01,
                       f"expected 500000.50, got {val}")

            # Check Estonian characters survived
            if 'name' in result.columns:
                report("Estonian characters preserved",
                       result['name'].iloc[0] == 'AKTSIASELTS TALLINNA VESI')

            # Check negative values
            if 'Ärikasum (kahjum)_2023' in result.columns:
                val = result['Ärikasum (kahjum)_2023'].iloc[0]
                report("Negative values preserved",
                       abs(val - (-5000.25)) < 0.01,
                       f"expected -5000.25, got {val}")

        # Verify file uses semicolon separator
        with open(os.path.join(tmpdir, 'roundtrip_test.csv'), 'r', encoding='utf-8-sig') as f:
            header = f.readline()
        report("File uses semicolon separator", ';' in header, f"header: {header.strip()[:60]}")

        # Restore original config
        config_mod._config_instance = old_config


# =====================================================================
#  PART C: FULL PIPELINE OUTPUT
# =====================================================================

def test_full_pipeline_output(data_path, financial_years, timestamp):
    """Run the full screening pipeline and produce real output files."""
    if len(financial_years) < 2:
        section("26. Full Pipeline Output - SKIPPED (need >= 2 years)")
        return

    section("26. Full Pipeline Output")
    from rik_screener.workflow.orchestrator import run_company_screening
    from rik_screener.utils.file_operations import safe_write_csv

    years = sorted(financial_years[-3:], reverse=True)  # Use up to 3 most recent years
    output_base = f"test_output_{timestamp}"

    print(f"  Running full pipeline for years {years}...")
    print(f"  Legal forms: AS, OU")

    config = {
        'years': years,
        'legal_forms': ['AS', 'OÜ'],
        'use_dataframe_pipeline': True,
        'save_final_output': False,
        'standard_formulas': {
            'ebitda_margin': {'years': years},
            'roe': {'years': years, 'use_averages': True},
            'roe': {'years': years, 'use_averages': False},
            'roa': {'years': years, 'use_averages': True},
            'roa': {'years': years, 'use_averages': False},
            'asset_turnover': {'years': years, 'use_averages': False},
            'employee_efficiency': {'years': years, 'use_averages': False},
            'cash_ratio': {'years': years},
            'current_ratio': {'years': years},
            'debt_to_equity': {'years': years},
            'labour_ratio': {'years': years},
        },
        'custom_formulas': {},
    }

    # Add revenue growth for consecutive year pairs
    year_pairs = [[years[i+1], years[i]] for i in range(len(years)-1)]
    if year_pairs:
        config['standard_formulas']['revenue_growth'] = {'year_pairs': year_pairs}

    # Add CAGR if 3+ years
    if len(years) >= 3:
        config['standard_formulas']['revenue_cagr'] = {
            'start_year': years[-1],
            'end_year': years[0]
        }

    try:
        result_df = run_company_screening(config)
        report("Full pipeline completed",
               isinstance(result_df, pd.DataFrame) and len(result_df) > 0,
               f"{len(result_df)} companies, {len(result_df.columns)} columns")
    except Exception as e:
        report("Full pipeline completed", False, str(e))
        traceback.print_exc()
        return

    if result_df is None or result_df.empty:
        report("(no output to save)", False)
        return

    # --- Output 1: Full results ---
    full_output = f"{output_base}_full_results.csv"
    safe_write_csv(result_df, full_output, encoding='utf-8-sig')
    report("Full results saved", os.path.exists(os.path.join(data_path, full_output)),
           f"{full_output} ({len(result_df)} rows x {len(result_df.columns)} cols)")

    # --- Output 2: Financial ratios only ---
    ratio_cols = ['company_code']
    if 'company_name' in result_df.columns:
        ratio_cols.append('company_name')
    ratio_cols += [c for c in result_df.columns if any(r in c for r in [
        'ebitda_margin', 'roe', 'roa', 'asset_turnover', 'employee_efficiency',
        'cash_ratio', 'current_ratio', 'debt_to_equity', 'labour_ratio',
        'revenue_growth', 'revenue_cagr', 'investment_vehicle'
    ])]
    ratio_cols = [c for c in ratio_cols if c in result_df.columns]

    if len(ratio_cols) > 2:
        ratios_df = result_df[ratio_cols].copy()
        ratios_output = f"{output_base}_ratios.csv"
        safe_write_csv(ratios_df, ratios_output, encoding='utf-8-sig')
        report("Ratios output saved", True,
               f"{ratios_output} ({len(ratios_df)} rows x {len(ratio_cols)} ratio cols)")
    else:
        report("Ratios output saved", False, "no ratio columns found")

    # --- Output 3: Industry & EMTAK summary ---
    industry_cols = ['company_code']
    if 'company_name' in result_df.columns:
        industry_cols.append('company_name')
    industry_cols += [c for c in result_df.columns if any(r in c for r in [
        'industry_code', 'industry_description', 'industry_combined'
    ])]
    industry_cols = [c for c in industry_cols if c in result_df.columns]

    if len(industry_cols) > 2:
        industry_df = result_df[industry_cols].copy()
        industry_output = f"{output_base}_industry.csv"
        safe_write_csv(industry_df, industry_output, encoding='utf-8-sig')
        report("Industry output saved", True,
               f"{industry_output} ({len(industry_df)} rows)")
    else:
        report("Industry output saved", False, "no industry columns found")

    # --- Output 4: Company overview (age, ownership, names) ---
    overview_cols = ['company_code']
    if 'company_name' in result_df.columns:
        overview_cols.append('company_name')
    overview_cols += [c for c in result_df.columns if any(r in c for r in [
        'company_age', 'legal_form', 'owner_count', 'top_'
    ])]
    overview_cols = [c for c in overview_cols if c in result_df.columns]

    if len(overview_cols) > 2:
        overview_df = result_df[overview_cols].copy()
        overview_output = f"{output_base}_overview.csv"
        safe_write_csv(overview_df, overview_output, encoding='utf-8-sig')
        report("Company overview output saved", True,
               f"{overview_output} ({len(overview_df)} rows)")
    else:
        report("Company overview output saved", False, "no overview columns found")

    # --- Output 5: Test companies subset ---
    valid_test_codes = [int(c) for c in TEST_BATCH if c != INVALID_COMPANY]
    if 'company_code' in result_df.columns:
        test_subset = result_df[result_df['company_code'].isin(valid_test_codes)].copy()
        if len(test_subset) > 0:
            subset_output = f"{output_base}_test_companies.csv"
            safe_write_csv(test_subset, subset_output, encoding='utf-8-sig')
            report("Test companies subset saved", True,
                   f"{subset_output} ({len(test_subset)}/{len(valid_test_codes)} found)")

            # Report which test companies were found/missing
            found_codes = set(test_subset['company_code'].tolist())
            for code in valid_test_codes:
                in_result = code in found_codes
                report(f"  Company {code} in results", in_result,
                       "present" if in_result else "MISSING - filtered out or no data")
        else:
            report("Test companies subset saved", False, "none of the test companies found in results")
    else:
        report("Test companies subset saved", False, "no company_code column")

    # Print column summary
    print(f"\n  Output columns in full results:")
    for i, col in enumerate(result_df.columns):
        non_null = result_df[col].notna().sum()
        print(f"    {i+1:3d}. {col:<50s} ({non_null}/{len(result_df)} non-null)")


# =====================================================================
#  RUNNER
# =====================================================================

def write_results_csv(filepath, results, title):
    """Write test results to a CSV file."""
    with open(filepath, 'w', encoding='utf-8-sig') as f:
        f.write(f"# {title}\n")
        f.write("test_name;status;detail\n")
        for name, passed, detail in results:
            status = "PASS" if passed else "FAIL"
            # Escape semicolons in detail
            safe_detail = str(detail).replace(';', ',') if detail else ""
            f.write(f"{name};{status};{safe_detail}\n")
    print(f"  -> Results saved to {filepath}")


if __name__ == '__main__':
    from datetime import datetime

    print("\n" + "=" * 60)
    print("  RIK SCREENER - FULL INTEGRATION TEST SUITE")
    print("  Part A: API calls | Part B: CSV pipeline")
    print("=" * 60)

    username, password = get_credentials()
    data_path = get_data_path()

    # Set up config with data path
    cfg = setup_config(data_path)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # --- PART A: API tests ---
    print("\n" + "#" * 60)
    print("  PART A: API TESTS")
    print("#" * 60)

    api_pass = 0
    api_fail = 0
    api_start_idx = len(RESULTS_LOG)

    api_tests = [
        (test_auth_and_connection, (username, password)),
        (test_soap_client, (username, password)),
        (test_endpoints, (username, password)),
        (test_data_processors, (username, password)),
        (test_get_latest_reports, (username, password)),
        (test_get_financial_statements, (username, password)),
        (test_check_consistency, (username, password)),
        (test_error_handling, (username, password)),
        (test_rate_limiter, (username, password)),
    ]

    for test_fn, args in api_tests:
        try:
            test_fn(*args)
        except Exception as e:
            FAIL += 1
            print(f"\n  [CRASH] {test_fn.__name__}: {e}")
            traceback.print_exc()
            RESULTS_LOG.append((f"CRASH: {test_fn.__name__}", False, str(e)))

    api_results = RESULTS_LOG[api_start_idx:]
    api_pass = sum(1 for _, p, _ in api_results if p)
    api_fail = sum(1 for _, p, _ in api_results if not p)

    print(f"\n  Part A subtotal: {api_pass} passed, {api_fail} failed")

    api_output = os.path.join(data_path, f"test_results_api_{timestamp}.csv")
    write_results_csv(api_output, api_results, f"API Test Results - {timestamp}")

    # --- PART B: CSV pipeline tests ---
    print("\n" + "#" * 60)
    print("  PART B: CSV PIPELINE TESTS")
    print("#" * 60)

    csv_start_idx = len(RESULTS_LOG)

    # Discovery
    try:
        found_files, financial_years = test_csv_file_discovery(data_path)
    except Exception as e:
        FAIL += 1
        print(f"\n  [CRASH] test_csv_file_discovery: {e}")
        traceback.print_exc()
        RESULTS_LOG.append(("CRASH: test_csv_file_discovery", False, str(e)))
        found_files, financial_years = {}, []

    csv_tests = [
        (test_csv_read_general_data, (data_path,)),
        (test_csv_read_financials, (data_path, financial_years)),
        (test_csv_read_revenues, (data_path,)),
        (test_csv_read_legal_data, (data_path,)),
        (test_csv_read_emtak, (data_path,)),
        (test_filter_companies, (data_path, financial_years)),
        (test_multi_year_merge, (data_path, financial_years)),
        (test_financial_data_loading, (data_path, financial_years)),
        (test_calculate_ratios, (data_path, financial_years)),
        (test_industry_codes, (data_path, financial_years)),
        (test_company_age, (data_path,)),
        (test_company_names, (data_path,)),
        (test_emtak_descriptions, (data_path, financial_years)),
        (test_filter_and_rank, (data_path, financial_years)),
        (test_csv_round_trip, (data_path,)),
    ]

    for test_fn, args in csv_tests:
        try:
            test_fn(*args)
        except Exception as e:
            FAIL += 1
            print(f"\n  [CRASH] {test_fn.__name__}: {e}")
            traceback.print_exc()
            RESULTS_LOG.append((f"CRASH: {test_fn.__name__}", False, str(e)))

    csv_results = RESULTS_LOG[csv_start_idx:]
    csv_pass = sum(1 for _, p, _ in csv_results if p)
    csv_fail = sum(1 for _, p, _ in csv_results if not p)

    print(f"\n  Part B subtotal: {csv_pass} passed, {csv_fail} failed")

    csv_output = os.path.join(data_path, f"test_results_csv_{timestamp}.csv")
    write_results_csv(csv_output, csv_results, f"CSV Pipeline Test Results - {timestamp}")

    # --- PART C: Full pipeline output ---
    print("\n" + "#" * 60)
    print("  PART C: FULL PIPELINE OUTPUT")
    print("#" * 60)

    pipeline_start_idx = len(RESULTS_LOG)

    try:
        test_full_pipeline_output(data_path, financial_years, timestamp)
    except Exception as e:
        FAIL += 1
        print(f"\n  [CRASH] test_full_pipeline_output: {e}")
        traceback.print_exc()
        RESULTS_LOG.append(("CRASH: test_full_pipeline_output", False, str(e)))

    pipeline_results = RESULTS_LOG[pipeline_start_idx:]
    pipeline_pass = sum(1 for _, p, _ in pipeline_results if p)
    pipeline_fail = sum(1 for _, p, _ in pipeline_results if not p)

    print(f"\n  Part C subtotal: {pipeline_pass} passed, {pipeline_fail} failed")

    # --- Summary ---
    print(f"\n{'='*60}")
    print(f"  OVERALL: {PASS} passed, {FAIL} failed, {PASS + FAIL} total")
    print(f"  Part A (API):      {api_pass}/{api_pass + api_fail}")
    print(f"  Part B (CSV):      {csv_pass}/{csv_pass + csv_fail}")
    print(f"  Part C (Pipeline): {pipeline_pass}/{pipeline_pass + pipeline_fail}")
    print(f"{'='*60}")
    print(f"  Test result files:")
    print(f"    {api_output}")
    print(f"    {csv_output}")
    print(f"  Pipeline output files (in {data_path}):")
    print(f"    test_output_{timestamp}_full_results.csv")
    print(f"    test_output_{timestamp}_ratios.csv")
    print(f"    test_output_{timestamp}_industry.csv")
    print(f"    test_output_{timestamp}_overview.csv")
    print(f"    test_output_{timestamp}_test_companies.csv")
    print(f"{'='*60}\n")

    sys.exit(0 if FAIL == 0 else 1)
