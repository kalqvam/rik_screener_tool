"""
Smoke test for rik_screener — exercises as many features as possible
using synthetic data (no SOAP API or real files needed).

Run: python test_smoke.py
"""

import sys
import traceback
import numpy as np
import pandas as pd

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
# Synthetic financial data used across tests
# ---------------------------------------------------------------------------
def make_financial_df():
    return pd.DataFrame({
        'company_code': [101, 102, 103, 104, 105],
        'Müügitulu_2023':                           [500000, 1,      300000, 0,      800000],
        'Müügitulu_2022':                           [450000, 400000, 280000, 100000, 750000],
        'Ärikasum (kahjum)_2023':                   [50000,  90000,  -5000,  10000,  120000],
        'Ärikasum (kahjum)_2022':                   [40000,  80000,  -2000,  8000,   100000],
        'Põhivarade kulum ja väärtuse langus_2023':  [10000,  5000,   8000,   3000,   15000],
        'Põhivarade kulum ja väärtuse langus_2022':  [9000,   4500,   7500,   2800,   14000],
        'Omakapital_2023':                           [200000, 300000, 150000, 50000,  400000],
        'Omakapital_2022':                           [180000, 280000, 140000, 45000,  370000],
        'Aruandeaasta kasum (kahjum)_2023':          [40000,  85000,  -8000,  7000,   100000],
        'Varad_2023':                                [600000, 700000, 400000, 100000, 900000],
        'Varad_2022':                                [550000, 650000, 380000, 90000,  850000],
        'Raha_2023':                                 [80000,  50000,  30000,  5000,   120000],
        'Lühiajalised kohustised_2023':              [150000, 100000, 120000, 30000,  200000],
        'Pikaajalised kohustised_2023':              [100000, 50000,  80000,  20000,  150000],
        'Käibevarad_2023':                           [250000, 200000, 150000, 40000,  350000],
        'Tööjõukulud_2023':                          [100000, 60000,  80000,  20000,  150000],
        'Töötajate keskmine arv taandatud täistööajale_2023': [25, 10, 20, 5, 40],
    })


# ===== 1. CONFIG VALIDATION =====

def test_config_validation():
    section("1. Config Validation")
    from rik_screener.workflow.config_validator import validate_config

    # Valid config
    try:
        validate_config({
            'years': [2023, 2022],
            'legal_forms': ['OÜ'],
            'use_dataframe_pipeline': True,
            'skip_steps': ['ownership'],
            'standard_formulas': {
                'ebitda_margin': {'years': [2023]},
                'revenue_growth': {'year_pairs': [[2022, 2023]]},
                'revenue_cagr': {'start_year': 2022, 'end_year': 2023},
            },
            'custom_formulas': {'my_ratio': '"Varad_2023" / "Müügitulu_2023"'},
            'financial_filters': [{'column': 'ebitda_margin_2023', 'min': 0.05}],
        })
        report("Valid config accepted", True)
    except Exception as e:
        report("Valid config accepted", False, str(e))

    # Missing years
    try:
        validate_config({'years': None})
        report("Rejects missing years", False, "no error raised")
    except ValueError:
        report("Rejects missing years", True)

    # Invalid legal form
    try:
        validate_config({'years': [2023], 'legal_forms': ['GmbH']})
        report("Rejects invalid legal form", False, "no error raised")
    except ValueError:
        report("Rejects invalid legal form", True)

    # Invalid skip step
    try:
        validate_config({'years': [2023], 'skip_steps': ['magic']})
        report("Rejects invalid skip step", False, "no error raised")
    except ValueError:
        report("Rejects invalid skip step", True)

    # Overlapping formula names
    try:
        validate_config({
            'years': [2023],
            'standard_formulas': {'ebitda_margin': {'years': [2023]}},
            'custom_formulas': {'ebitda_margin_2023': '"Varad_2023"'},
        })
        report("Rejects overlapping formula names", False, "no error raised")
    except ValueError:
        report("Rejects overlapping formula names", True)

    # Bad revenue_growth config
    try:
        validate_config({
            'years': [2023],
            'standard_formulas': {'revenue_growth': {'years': [2023]}},
        })
        report("Rejects bad revenue_growth config", False, "no error raised")
    except ValueError:
        report("Rejects bad revenue_growth config", True)


# ===== 2. STANDARD FORMULAS =====

def test_standard_formulas():
    section("2. Standard Formula Generation")
    from rik_screener.criteria_setup.calculation_utils.standard_formulas import (
        get_standard_formulas, ebitda_margin, roe, roa, asset_turnover,
        employee_efficiency, cash_ratio, current_ratio, debt_to_equity,
        labour_ratio, revenue_growth, revenue_cagr
    )

    # Individual formula generators return strings
    for name, func, args in [
        ("ebitda_margin", ebitda_margin, (2023,)),
        ("roe (avg)", roe, (2023, 0)),
        ("roe (single)", roe, (2023, 1)),
        ("roa", roa, (2023, 0)),
        ("asset_turnover", asset_turnover, (2023, 0)),
        ("employee_efficiency", employee_efficiency, (2023, 0)),
        ("cash_ratio", cash_ratio, (2023,)),
        ("current_ratio", current_ratio, (2023,)),
        ("debt_to_equity", debt_to_equity, (2023,)),
        ("labour_ratio", labour_ratio, (2023,)),
        ("revenue_growth", revenue_growth, (2022, 2023)),
        ("revenue_cagr", revenue_cagr, (2021, 2023)),
    ]:
        result = func(*args)
        report(f"{name} returns formula string", isinstance(result, str) and len(result) > 5, result[:60])

    # Bulk generator
    formulas = get_standard_formulas([2023, 2022])
    report("get_standard_formulas returns dict", isinstance(formulas, dict) and len(formulas) > 0,
           f"{len(formulas)} formulas")


# ===== 3. FORMULA ENGINE =====

def test_formula_engine():
    section("3. Formula Engine (create, validate, apply)")
    from rik_screener.criteria_setup.calculation_utils.formula_engine import (
        create_formula, validate_formulas, apply_formulas, flag_investment_vehicles
    )

    df = make_financial_df()

    # create_formula — basic arithmetic
    result = create_formula('"Ärikasum (kahjum)_2023" / "Müügitulu_2023"', df)
    report("create_formula basic division", isinstance(result, np.ndarray) and len(result) == 5)

    # create_formula — division by zero -> NaN (not inf)
    result = create_formula('"Ärikasum (kahjum)_2023" / "Müügitulu_2023"', df)
    # company_code 104 has Müügitulu=0 -> should be NaN
    report("Div-by-zero -> NaN (not inf)", np.isnan(result[3]),
           f"value={result[3]}")

    # create_formula — abs()
    result = create_formula('abs("Ärikasum (kahjum)_2023")', df)
    report("abs() works", all(result >= 0))

    # create_formula — missing column raises ValueError
    try:
        create_formula('"NonExistent_2023" / "Müügitulu_2023"', df)
        report("Missing column raises ValueError", False)
    except ValueError:
        report("Missing column raises ValueError", True)

    # validate_formulas
    formulas = {
        'good': '"Müügitulu_2023" / "Varad_2023"',
        'bad': '"FakeColumn" + "Müügitulu_2023"',
    }
    valid, errors = validate_formulas(formulas, df)
    report("validate_formulas: good formula kept", 'good' in valid)
    report("validate_formulas: bad formula rejected", len(errors) > 0)

    # apply_formulas
    test_formulas = {
        'margin_2023': '"Ärikasum (kahjum)_2023" / "Müügitulu_2023"',
        'ebitda_margin_2023': '("Ärikasum (kahjum)_2023" + abs("Põhivarade kulum ja väärtuse langus_2023")) / "Müügitulu_2023"',
    }
    result_df = apply_formulas(df.copy(), test_formulas)
    report("apply_formulas adds columns", 'margin_2023' in result_df.columns and 'ebitda_margin_2023' in result_df.columns)
    report("apply_formulas NaN for div-by-zero rows",
           pd.isna(result_df.loc[result_df['company_code'] == 104, 'margin_2023'].iloc[0]))

    # flag_investment_vehicles — company 102 has Müügitulu=1
    flagged = flag_investment_vehicles(result_df, [2023], test_formulas)
    report("Investment vehicle flagged (revenue=1)",
           flagged.loc[flagged['company_code'] == 102, 'investment_vehicle'].iloc[0] == True)
    report("Investment vehicle ratios set to NaN",
           pd.isna(flagged.loc[flagged['company_code'] == 102, 'margin_2023'].iloc[0]))
    report("Normal company NOT flagged",
           flagged.loc[flagged['company_code'] == 101, 'investment_vehicle'].iloc[0] == False)


# ===== 4. DATA PROCESSING UTILITIES =====

def test_data_processing():
    section("4. Data Processing Utilities")
    from rik_screener.utils.data_processing import (
        convert_to_numeric, validate_columns, clean_column_names,
        handle_nan_values, extract_quoted_columns
    )

    # extract_quoted_columns
    cols = extract_quoted_columns('"Revenue_2023" / "Assets_2023" + abs("Debt_2023")')
    report("extract_quoted_columns", set(cols) == {'Revenue_2023', 'Assets_2023', 'Debt_2023'},
           f"got {cols}")

    # convert_to_numeric
    df = pd.DataFrame({'a': ['1', '2', 'bad', '4'], 'b': [10, 20, 30, 40]})
    result = convert_to_numeric(df, ['a', 'b'])
    report("convert_to_numeric coerces errors to NaN",
           pd.isna(result['a'].iloc[2]) and result['b'].dtype in [np.int64, np.float64])

    # convert_to_numeric with missing column (should warn, not crash)
    result = convert_to_numeric(df.copy(), ['nonexistent'])
    report("convert_to_numeric handles missing column gracefully", True)

    # validate_columns
    df2 = pd.DataFrame({'x': [1], 'y': [2]})
    ok, missing_cols = validate_columns(df2, ['x', 'y'])
    report("validate_columns: all present", ok == True and missing_cols == [])
    ok2, missing_cols2 = validate_columns(df2, ['x', 'z'])
    report("validate_columns: detects missing", ok2 == False and 'z' in missing_cols2)

    # clean_column_names
    df3 = pd.DataFrame({' Name ': [1], 'Revenue  Total': [2]})
    cleaned = clean_column_names(df3)
    report("clean_column_names strips whitespace", 'Name' in cleaned.columns)

    # handle_nan_values
    df4 = pd.DataFrame({'a': [1, np.nan, 3], 'b': [np.nan, 2, 3]})
    filled = handle_nan_values(df4.copy(), strategy='fill', fill_value=0)
    report("handle_nan_values fill", filled.isna().sum().sum() == 0)

    dropped = handle_nan_values(df4.copy(), strategy='drop')
    report("handle_nan_values drop", len(dropped) == 1)  # only row 2 has no NaN


# ===== 5. FILTERING & RANKING =====

def test_filtering():
    section("5. Filtering & Ranking")
    from rik_screener.post_processing.filtering import filter_and_rank

    df = pd.DataFrame({
        'company_code': [1, 2, 3, 4, 5],
        'ebitda_margin_2023': [0.15, 0.25, 0.05, 0.30, 0.12],
        'roe_2023': [0.10, 0.20, 0.03, 0.25, 0.08],
        'revenue': [100, 200, 50, 300, 80],
    })

    # Basic sort
    result = filter_and_rank(
        input_data=df, sort_column='ebitda_margin_2023',
        ascending=False, return_dataframe=True
    )
    report("Sort descending by margin",
           result is not None and result.iloc[0]['company_code'] == 4)

    # Filter: min margin
    result = filter_and_rank(
        input_data=df, sort_column='ebitda_margin_2023',
        filters=[{'column': 'ebitda_margin_2023', 'min': 0.10}],
        ascending=False, return_dataframe=True
    )
    report("Filter min=0.10 removes low-margin companies",
           result is not None and 3 not in result['company_code'].values,
           f"{len(result)} companies remain")

    # Filter: max
    result = filter_and_rank(
        input_data=df, sort_column='ebitda_margin_2023',
        filters=[{'column': 'ebitda_margin_2023', 'max': 0.20}],
        ascending=False, return_dataframe=True
    )
    report("Filter max=0.20",
           result is not None and 4 not in result['company_code'].values)

    # top_n
    result = filter_and_rank(
        input_data=df, sort_column='ebitda_margin_2023',
        top_n=2, ascending=False, return_dataframe=True
    )
    report("top_n=2 limits results",
           result is not None and len(result) == 2)

    # export_columns
    result = filter_and_rank(
        input_data=df, sort_column='ebitda_margin_2023',
        export_columns=['company_code', 'ebitda_margin_2023'],
        ascending=False, return_dataframe=True
    )
    report("export_columns selects subset",
           result is not None and list(result.columns) == ['company_code', 'ebitda_margin_2023'])

    # Empty after filter
    result = filter_and_rank(
        input_data=df, sort_column='ebitda_margin_2023',
        filters=[{'column': 'ebitda_margin_2023', 'min': 99.0}],
        ascending=False, return_dataframe=True
    )
    report("Empty result after aggressive filter",
           result is not None and len(result) == 0)


# ===== 6. EDGE CASES & ROBUSTNESS =====

def test_edge_cases():
    section("6. Edge Cases & Robustness")
    from rik_screener.criteria_setup.calculation_utils.formula_engine import create_formula, apply_formulas

    # All-NaN column
    df = pd.DataFrame({'a_2023': [np.nan, np.nan, np.nan], 'b_2023': [1, 2, 3]})
    result = create_formula('"a_2023" + "b_2023"', df)
    report("All-NaN column propagates NaN", all(np.isnan(result)))

    # Very large values (no overflow crash)
    df2 = pd.DataFrame({'big_2023': [1e308, 1e308, 1e-308], 'one_2023': [1, 1, 1]})
    result = create_formula('"big_2023" * "one_2023"', df2)
    report("Large values don't crash", isinstance(result, np.ndarray))

    # Empty DataFrame
    df3 = pd.DataFrame({'x_2023': pd.Series([], dtype=float), 'y_2023': pd.Series([], dtype=float)})
    result = create_formula('"x_2023" + "y_2023"', df3)
    report("Empty DataFrame produces empty result", len(result) == 0)

    # Single row
    df4 = pd.DataFrame({'v_2023': [42.0], 'w_2023': [0.0]})
    result = create_formula('"v_2023" / "w_2023"', df4)
    report("Single row div-by-zero -> NaN", np.isnan(result[0]))

    # apply_formulas with a formula that fails validation
    df5 = make_financial_df()
    mixed = {
        'good': '"Müügitulu_2023" + "Varad_2023"',
        'bad': '"NoSuchColumn" * 2',
    }
    result_df = apply_formulas(df5.copy(), mixed)
    report("apply_formulas: good formula survives bad sibling",
           'good' in result_df.columns)


# ===== 7. FILE OPERATIONS =====

def test_file_operations():
    section("7. File Operations")
    import tempfile, os
    from rik_screener.utils.file_operations import safe_read_csv, safe_write_csv
    import rik_screener.utils.config as config_mod

    df = pd.DataFrame({'a': [1, 2, 3], 'b': [4, 5, 6]})

    with tempfile.TemporaryDirectory() as tmpdir:
        # Reset config singleton and point it at our temp dir
        config_mod._config_instance = config_mod.ConfigManager(base_path=tmpdir)
        # Write then read round-trip
        safe_write_csv(df, 'test_out.csv', base_path=tmpdir)
        exists = os.path.exists(os.path.join(tmpdir, 'test_out.csv'))
        report("safe_write_csv creates file", exists)

        result = safe_read_csv('test_out.csv', base_path=tmpdir)
        report("safe_read_csv round-trip",
               result is not None and len(result) == 3 and 'a' in result.columns)

        # Read non-existent file returns None
        result = safe_read_csv('no_such_file.csv', base_path=tmpdir)
        report("safe_read_csv missing file -> None", result is None)

        # Write with encoding
        safe_write_csv(df, 'test_enc.csv', base_path=tmpdir, encoding='utf-8-sig')
        result = safe_read_csv('test_enc.csv', base_path=tmpdir, encoding='utf-8-sig')
        report("Write/read with encoding", result is not None and len(result) == 3)


# ===== RUN =====

if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("  RIK SCREENER — SMOKE TEST SUITE")
    print("=" * 60)

    tests = [
        test_config_validation,
        test_standard_formulas,
        test_formula_engine,
        test_data_processing,
        test_filtering,
        test_edge_cases,
        test_file_operations,
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
