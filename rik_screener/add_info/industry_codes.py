import pandas as pd
import numpy as np
from typing import List, Dict, Set, Optional, Union

from ..utils import (
    get_config,
    safe_read_csv,
    safe_write_csv,
    log_step,
    log_info,
    log_warning,
    log_error
)


def add_industry_classifications(
    input_file: Optional[str] = "companies_with_ratios.csv",
    input_data: Optional[pd.DataFrame] = None,
    output_file: Optional[str] = "companies_with_industry.csv",
    revenues_file: str = "revenues.csv",
    years: list = None,
    return_dataframe: bool = False
) -> Union[pd.DataFrame, None]:
    
    if input_data is not None:
        log_info(f"Using provided DataFrame with {len(input_data)} companies")
        companies_df = input_data.copy()
    else:
        log_info(f"Loading companies from {input_file}")
        companies_df = safe_read_csv(input_file)
        if companies_df is None:
            log_error(f"Failed to load input file {input_file}")
            return None

    log_info(f"Loaded {len(companies_df)} companies")

    config = get_config()
    if years is None:
        years = config.get_years()

    years = sorted(years, reverse=True)

    revenues_header = safe_read_csv(revenues_file, nrows=0)
    if revenues_header is None:
        log_error(f"Revenue file {revenues_file} not found")
        return companies_df

    log_info(f"Loading industry revenue data from {revenues_file}")
    log_info(f"Available columns in revenues file: {revenues_header.columns.tolist()}")

    all_report_ids = set()
    year_report_id_mapping = {}
    
    for year in years:
        report_id_col = f"report_id_{year}"
        if report_id_col in companies_df.columns:
            year_report_ids = set(companies_df[report_id_col].dropna().astype(int))
            year_report_id_mapping[year] = year_report_ids
            all_report_ids.update(year_report_ids)
            log_info(f"Year {year}: {len(year_report_ids)} report IDs")

    if not all_report_ids:
        log_warning("No report IDs found for any year")
        return companies_df

    log_info(f"Total unique report IDs across all years: {len(all_report_ids)}")

    try:
        log_info("Reading revenues file once for all years...")
        
        chunk_size = config.get_default('chunk_size', 500000)
        all_industry_data = []
        
        for chunk in safe_read_csv(
            revenues_file,
            chunk_size=chunk_size,
            dtype={"emtak": str}
        ):
            filtered_chunk = chunk[
                (chunk["report_id"].isin(all_report_ids)) &
                (chunk["põhitegevusala"] == "jah")
            ]
            
            if not filtered_chunk.empty:
                filtered_chunk = filtered_chunk[["report_id", "emtak"]].copy()
                all_industry_data.append(filtered_chunk)

        if not all_industry_data:
            log_warning("No industry data found for any companies")
            return companies_df

        industry_data = pd.concat(all_industry_data, ignore_index=True)
        log_info(f"Found {len(industry_data)} total industry records")

        duplicates = industry_data["report_id"].duplicated()
        if duplicates.any():
            dupe_count = duplicates.sum()
            log_warning(f"Found {dupe_count} duplicate main industry codes. Using the first occurrence")
            industry_data = industry_data.drop_duplicates(subset="report_id", keep="first")

        industry_dict = dict(zip(industry_data["report_id"], industry_data["emtak"]))
        log_info(f"Created industry lookup dictionary with {len(industry_dict)} entries")

        for year in years:
            if year not in year_report_id_mapping:
                log_warning(f"No report IDs found for year {year}, skipping")
                continue

            report_id_col = f"report_id_{year}"
            industry_col = f"industry_code_{year}"
            
            log_info(f"Assigning industry codes for year {year}")
            
            companies_df[industry_col] = companies_df[report_id_col].map(industry_dict)
            
            assigned_count = companies_df[industry_col].notna().sum()
            total_count = companies_df[report_id_col].notna().sum()
            
            log_info(f"Year {year}: assigned industry codes to {assigned_count} out of {total_count} companies")

    except Exception as e:
        log_error(f"Error processing industry data: {str(e)}")
        import traceback
        traceback.print_exc()
        return companies_df

    if output_file and not return_dataframe:
        if safe_write_csv(companies_df, output_file, encoding='utf-8'):
            log_info(f"Saved {len(companies_df)} companies with industry codes to {output_file}")
        else:
            log_error(f"Failed to save results to {output_file}")

    for year in years:
        industry_col = f"industry_code_{year}"
        if industry_col in companies_df.columns:
            unique_codes = companies_df[industry_col].dropna().unique()
            log_info(f"Year {year}: {len(unique_codes)} unique industry codes")
            if len(unique_codes) > 0:
                log_info(f"Year {year} sample codes: {unique_codes[:10]}")

    return companies_df
