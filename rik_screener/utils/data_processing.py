import pandas as pd
import numpy as np
import re
from typing import List, Dict, Any, Optional, Union, Tuple
from .logging import log_info, log_warning, log_error

def convert_to_numeric(
    df: pd.DataFrame,
    columns: Union[str, List[str]],
    errors: str = 'coerce',
    fill_value: Optional[Union[int, float]] = None,
    log_conversions: bool = True
) -> pd.DataFrame:
    if isinstance(columns, str):
        columns = [columns]
    
    df_copy = df.copy()
    
    for col in columns:
        if col not in df_copy.columns:
            log_warning(f"Column '{col}' not found in DataFrame")
            continue
        
        original_count = df_copy[col].notna().sum()
        
        df_copy[col] = pd.to_numeric(df_copy[col], errors=errors)
        
        nan_count = df_copy[col].isna().sum()
        converted_count = df_copy[col].notna().sum()

        if fill_value is not None:
            df_copy[col] = df_copy[col].fillna(fill_value)
            if log_conversions and nan_count > 0:
                log_info(f"Column '{col}': {nan_count} NaN values filled with {fill_value}")
        
        if log_conversions:
            log_info(f"Column '{col}': {converted_count} values converted to numeric")
            if errors == 'coerce' and (original_count - converted_count) > 0:
                failed_count = original_count - converted_count
                log_warning(f"Column '{col}': {failed_count} values could not be converted")
    
    return df_copy

def validate_columns(
    df: pd.DataFrame,
    required_columns: List[str],
    raise_error: bool = False
) -> Tuple[bool, List[str]]:
    missing_columns = [col for col in required_columns if col not in df.columns]
    all_present = len(missing_columns) == 0
    
    if missing_columns:
        message = f"Missing required columns: {missing_columns}"
        if raise_error:
            raise ValueError(message)
        else:
            log_warning(message)
    
    return all_present, missing_columns

def clean_column_names(df: pd.DataFrame, inplace: bool = False) -> pd.DataFrame:
    if not inplace:
        df = df.copy()
    
    cleaned_columns = {}
    for col in df.columns:
        cleaned = str(col).strip()
        cleaned = re.sub(r'\s+', ' ', cleaned)
        if cleaned != col:
            cleaned_columns[col] = cleaned
    
    if cleaned_columns:
        df = df.rename(columns=cleaned_columns)
        log_info(f"Cleaned {len(cleaned_columns)} column names")
    
    return df

def handle_nan_values(
    df: pd.DataFrame,
    strategy: str = 'keep',
    fill_value: Any = None,
    columns: Optional[List[str]] = None
) -> pd.DataFrame:
    df_copy = df.copy()
    
    if columns is None:
        columns = df_copy.columns.tolist()
    
    for col in columns:
        if col not in df_copy.columns:
            continue
            
        nan_count = df_copy[col].isna().sum()
        if nan_count == 0:
            continue
        
        if strategy == 'fill':
            df_copy[col] = df_copy[col].fillna(fill_value)
            log_info(f"Column '{col}': filled {nan_count} NaN values with {fill_value}")
        elif strategy == 'drop':
            pass
        elif strategy == 'keep':
            log_info(f"Column '{col}': keeping {nan_count} NaN values")
    
    if strategy == 'drop':
        original_len = len(df_copy)
        df_copy = df_copy.dropna(subset=columns)
        dropped_count = original_len - len(df_copy)
        if dropped_count > 0:
            log_info(f"Dropped {dropped_count} rows with NaN values")
    
    return df_copy

def extract_quoted_columns(formula: str) -> List[str]:
    if not formula:
        return []
    
    pattern = r'"([^"]+)"|\'([^\']+)\''
    matches = re.findall(pattern, formula)
    
    columns = [m[0] or m[1] for m in matches]
    
    return columns
