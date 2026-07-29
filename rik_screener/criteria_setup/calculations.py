import re
import pandas as pd
from typing import Dict, List, Optional, Union

from ..utils import (
    get_config,
    safe_read_csv,
    safe_write_csv,
    extract_quoted_columns,
    log_info,
    log_warning,
    log_error
)

from .calculation_utils import (
    load_financial_data,
    merge_financial_data,
    apply_formulas,
    validate_formulas,
    get_standard_formulas,
    flag_investment_vehicles
)


def calculate_ratios(
    input_file: Optional[str] = "merged_companies_multi_year.csv",
    input_data: Optional[pd.DataFrame] = None,
    output_file: Optional[str] = "companies_with_ratios.csv",
    years: List[int] = None,
    financial_items: List[str] = None,
    formulas: Dict[str, str] = None,
    use_standard_formulas: bool = True,
    return_dataframe: bool = False,
    early_filters: Optional[List[Dict]] = None,
) -> Union[pd.DataFrame, None]:
    config = get_config()
    
    if years is None:
        years = config.get_years()
    
    years = sorted(years, reverse=True)
    
    if formulas is None:
        if use_standard_formulas:
            formulas = get_standard_formulas(years)
        
        if not formulas:
            default_formulas = {
                f"EBITDA_Margin_{years[0]}": f'("Ärikasum (kahjum)_{years[0]}" + abs("Põhivarade kulum ja väärtuse langus_{years[0]}")) / "Müügitulu_{years[0]}"',
            }
            if len(years) >= 2:
                default_formulas["Revenue_Growth"] = f'("Müügitulu_{years[0]}" - "Müügitulu_{years[1]}") / "Müügitulu_{years[1]}"'
            formulas = default_formulas
    
    all_formula_columns = []
    for formula in formulas.values():
        formula_cols = extract_quoted_columns(formula)
        log_info(f"Columns from formula: {formula_cols}")
        all_formula_columns.extend(formula_cols)
    
    if financial_items is None:
        financial_items = set()
        for col in all_formula_columns:
            base_col = re.sub(r'_\d{4}$', '', col)
            financial_items.add(base_col)
        financial_items = list(financial_items)
    
    log_info(f"Financial items to retrieve: {financial_items}")
    
    if input_data is not None:
        log_info(f"Using provided DataFrame with {len(input_data)} companies")
        result = input_data.copy()
    else:
        log_info(f"Loading merged companies from {input_file}")
        result = safe_read_csv(input_file)
        if result is None:
            log_error(f"Failed to load input file {input_file}")
            return None
    
    log_info(f"Loaded {len(result)} companies from merged data")
    log_info(f"Columns in merged data: {sorted(result.columns.tolist())}")
    
    ref_year = years[0]
    hist_years = years[1:]

    # Split formulas: those that reference only the reference year vs those that need
    # historical year columns (growth, CAGR, anything with _{hist_year} in the expression).
    if hist_years:
        ref_formulas = {
            k: v for k, v in formulas.items()
            if all(f'_{hy}' not in v for hy in hist_years)
        }
        hist_formulas = {k: v for k, v in formulas.items() if k not in ref_formulas}
    else:
        ref_formulas = formulas
        hist_formulas = {}

    # Phase 1: reference year
    result = merge_financial_data(result, [ref_year], financial_items)

    valid_ref, ref_errors = validate_formulas(ref_formulas, result)
    if ref_errors:
        log_warning(f"Found {len(ref_errors)} reference-year formula validation errors")
    result = apply_formulas(result, valid_ref)

    # Apply any filter whose column now exists — shrinks result before historical loads
    if early_filters:
        for f in early_filters:
            col = f.get("column")
            if col not in result.columns:
                continue
            min_val = f.get("min")
            max_val = f.get("max")
            before = len(result)
            if min_val is not None:
                result = result[result[col] >= min_val]
            if max_val is not None:
                result = result[result[col] <= max_val]
            after = len(result)
            if after < before:
                log_info(f"Early filter on {col}: {before} -> {after} companies")

    # Phase 2: historical years — report_id_filter derived from filtered result automatically
    if hist_years:
        result = merge_financial_data(result, hist_years, financial_items)
        valid_hist, hist_errors = validate_formulas(hist_formulas, result)
        if hist_errors:
            log_warning(f"Found {len(hist_errors)} historical-year formula validation errors")
        result = apply_formulas(result, valid_hist)
    else:
        valid_hist = {}

    all_valid = {**valid_ref, **valid_hist}
    result = flag_investment_vehicles(result, years, all_valid)
    
    if output_file and not return_dataframe:
        if safe_write_csv(result, output_file, encoding='utf-8'):
            log_info(f"Saved {len(result)} companies with ratios to {output_file}")
        else:
            log_error(f"Failed to save results to {output_file}")
    
    return result


def create_formula(formula_expr: str, data: pd.DataFrame):
    from .calculation_utils.formula_engine import create_formula as _create_formula
    return _create_formula(formula_expr, data)
