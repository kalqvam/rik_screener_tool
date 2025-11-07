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
    return_dataframe: bool = False
) -> Union[pd.DataFrame, None]:
    config = get_config()
    
    if years is None:
        years = config.get_years()
    
    years = sorted(years, reverse=True)
    
    if formulas is None:
        if use_standard_formulas:
            formulas = get_standard_formulas(years)
        
        if not formulas:
            formulas = {
                f"EBITDA_Margin_{years[0]}": f'("Ärikasum (kahjum)_{years[0]}" + abs("Põhivarade kulum ja väärtuse langus_{years[0]}")) / "Müügitulu_{years[0]}"',
                "Revenue_Growth": f'("Müügitulu_{years[0]}" - "Müügitulu_{years[1]}") / "Müügitulu_{years[1]}"',
            }
    
    all_formula_columns = []
    for formula in formulas.values():
        formula_cols = extract_quoted_columns(formula)
        log_info(f"Columns from formula: {formula_cols}")
        all_formula_columns.extend(formula_cols)
    
    if financial_items is None:
        financial_items = set()
        for col in all_formula_columns:
            if '_20' in col:
                base_col = col.split('_20')[0]
                financial_items.add(base_col)
            else:
                financial_items.add(col)
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
    
    result = merge_financial_data(result, years, financial_items)
    
    valid_formulas, formula_errors = validate_formulas(formulas, result)
    if formula_errors:
        log_warning(f"Found {len(formula_errors)} formula validation errors")
    
    result = apply_formulas(result, valid_formulas)

    result = flag_investment_vehicles(result, years, valid_formulas)
    
    if output_file and not return_dataframe:
        if safe_write_csv(result, output_file):
            log_info(f"Saved {len(result)} companies with ratios to {output_file}")
        else:
            log_error(f"Failed to save results to {output_file}")
    
    return result


def create_formula(formula_expr: str, data: pd.DataFrame):
    from .calculation_utils.formula_engine import create_formula as _create_formula
    return _create_formula(formula_expr, data)
