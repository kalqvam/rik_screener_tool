import pandas as pd
import numpy as np
from typing import Dict, List, Tuple

from ...utils import (
    extract_quoted_columns,
    log_info,
    log_warning,
    log_error
)


def validate_formulas(formulas: Dict[str, str], data: pd.DataFrame) -> Tuple[Dict[str, str], List[str]]:
    valid_formulas = {}
    errors = []
    
    for formula_name, formula_expr in formulas.items():
        try:
            columns = extract_quoted_columns(formula_expr)
            missing_columns = [col for col in columns if col not in data.columns]
            
            if missing_columns:
                error_msg = f"Formula '{formula_name}' references missing columns: {missing_columns}"
                errors.append(error_msg)
                log_warning(error_msg)
                continue
            
            test_result = create_formula(formula_expr, data.head(1))
            if test_result is not None:
                valid_formulas[formula_name] = formula_expr
            else:
                error_msg = f"Formula '{formula_name}' failed validation test"
                errors.append(error_msg)
                log_warning(error_msg)
                
        except Exception as e:
            error_msg = f"Formula '{formula_name}' validation error: {str(e)}"
            errors.append(error_msg)
            log_warning(error_msg)
    
    return valid_formulas, errors


def apply_formulas(data: pd.DataFrame, formulas: Dict[str, str]) -> pd.DataFrame:
    result = data.copy()
    
    for formula_name, formula_expr in formulas.items():
        try:
            log_info(f"Calculating formula: {formula_name}")
            result[formula_name] = create_formula(formula_expr, result)
            log_info(f"Successfully calculated formula: {formula_name}")
        except Exception as e:
            log_error(f"Error calculating formula {formula_name}: {str(e)}")
            result[formula_name] = np.nan
    
    return result


def create_formula(formula_expr: str, data: pd.DataFrame):
    log_info(f"Processing formula: {formula_expr}")
    
    columns = extract_quoted_columns(formula_expr)
    log_info(f"Columns referenced in formula: {columns}")
    
    namespace = {}
    
    for col in columns:
        if col in data.columns:
            values = pd.to_numeric(data[col], errors='coerce').values
            nan_count = np.isnan(values).sum()
            if nan_count > 0:
                log_warning(f"Column '{col}' has {nan_count} NaN values that will propagate through formula")
            namespace[col] = values
        else:
            raise ValueError(f"Column '{col}' not found in the data")
    
    namespace.update({
        'abs': np.abs,
        'min': np.minimum,
        'max': np.maximum,
        'sqrt': np.sqrt,
        'log': np.log,
        'log10': np.log10,
        'exp': np.exp,
        'round': np.round,
        'pow': np.power
    })
    
    for col in columns:
        formula_expr = formula_expr.replace(f'"{col}"', f'namespace["{col}"]')
        formula_expr = formula_expr.replace(f"'{col}'", f'namespace["{col}"]')
    
    try:
        eval_globals = {"__builtins__": {}, "namespace": namespace}
        with np.errstate(divide='ignore', invalid='ignore'):
            result = eval(formula_expr, eval_globals, namespace)
        if isinstance(result, np.ndarray):
            inf_count = np.isinf(result).sum()
            if inf_count > 0:
                log_warning(f"Formula produced {inf_count} infinite values (division by zero) — replacing with NaN")
                result = np.where(np.isinf(result), np.nan, result)
        return result
    except Exception as e:
        raise ValueError(f"Error evaluating formula '{formula_expr}': {str(e)}")

def flag_investment_vehicles(data: pd.DataFrame, years: List[int], formulas: Dict[str, str]) -> pd.DataFrame:
    result = data.copy()
    
    result['investment_vehicle'] = False
    
    for year in years:
        revenue_col = f'Müügitulu_{year}'
        ebitda_margin_col = f'ebitda_margin_{year}'
        
        mask = pd.Series(False, index=result.index)
        
        if revenue_col in result.columns:
            mask |= (result[revenue_col] == 1)
        
        if ebitda_margin_col in result.columns:
            mask |= (result[ebitda_margin_col] >= 0.99)
        
        result.loc[mask, 'investment_vehicle'] = True
        
        revenue_dependent_ratios = [
            name for name, formula in formulas.items() 
            if f'Müügitulu_{year}' in formula
        ]
        
        for ratio in revenue_dependent_ratios:
            if ratio in result.columns:
                result.loc[mask, ratio] = np.nan
    
    return result
