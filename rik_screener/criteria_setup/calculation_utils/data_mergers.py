import pandas as pd
from typing import List

from .data_loaders import load_financial_data
from ...utils import (
    convert_to_numeric,
    log_info,
    log_warning,
    log_error
)


def merge_financial_data(
    result: pd.DataFrame, 
    years: List[int], 
    financial_items: List[str]
) -> pd.DataFrame:
    for year in years:
        report_id_col = f"report_id_{year}"
        
        if report_id_col not in result.columns:
            log_warning(f"{report_id_col} not found in merged data. Skipping year {year}")
            continue
        
        financial_wide = load_financial_data(year, financial_items)
        if financial_wide is None:
            continue
        
        try:
            matched = result[report_id_col].isin(financial_wide['report_id']).sum()
            log_info(f"Year {year}: {matched}/{len(result)} companies matched financial data")

            result = pd.merge(
                result,
                financial_wide,
                left_on=report_id_col,
                right_on='report_id',
                how='left'
            )

            if 'report_id' in result.columns:
                result = result.drop(columns=['report_id'])

            log_info(f"Merged financial data for {year}. Current columns: {len(result.columns)}")
            
        except Exception as e:
            log_error(f"Error processing financial data for {year}: {str(e)}")
    
    all_financial_columns = []
    for item in financial_items:
        for year in years:
            col = f"{item}_{year}"
            if col in result.columns:
                all_financial_columns.append(col)
    
    result = convert_to_numeric(result, all_financial_columns)

    log_info("Converting financial columns to numeric (NaN values preserved)")
    sample_cols = ['company_code'] + all_financial_columns[:3]
    if len(result) > 0:
        log_info(str(result[sample_cols].head()))
    
    return result
