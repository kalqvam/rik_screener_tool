import pandas as pd
from typing import List, Union, Optional

from ..utils import (
    get_config,
    safe_write_csv,
    cleanup_temp_files,
    log_info,
    log_warning,
    log_error
)


def merge_multiple_years(
    years: List[int],
    legal_forms: List[str] = ["AS", "OÜ"],
    output_file: Optional[str] = "merged_companies_multiyear.csv",
    require_all_years: bool = True,
    filter_companies_func=None,
    return_dataframe: bool = False
) -> Union[pd.DataFrame, None]:

    if filter_companies_func is None:
        from .general_filter import filter_companies
        filter_companies_func = filter_companies
        
    if not years:
        log_error("No years specified")
        return None

    log_info(f"Processing data for years: {years}")

    year_dfs = {}

    for year in years:
        log_info(f"Processing year {year}")
        
        if return_dataframe:
            year_df = filter_companies_func(
                year=year,
                legal_forms=legal_forms,
                output_file=None,
                return_dataframe=True
            )
        else:
            year_df = filter_companies_func(
                year=year,
                legal_forms=legal_forms,
                output_file=f"temp_filtered_companies_{year}.csv"
            )

        if year_df is None or year_df.empty:
            log_warning(f"No data available for year {year}")
            if require_all_years:
                log_error("Since require_all_years=True, cannot continue without data for all years")
                return None
            continue

        suffix = f"_{year}"
        rename_cols = {col: f"{col}{suffix}" for col in year_df.columns
                       if col != 'company_code'}
        year_df = year_df.rename(columns=rename_cols)

        year_dfs[year] = year_df

    if len(year_dfs) < len(years) and require_all_years:
        log_error(f"Not all years have data ({len(year_dfs)} out of {len(years)})")
        return None

    if not year_dfs:
        log_error("No data available for any of the specified years")
        return None

    if require_all_years and len(year_dfs) > 1:
        common_companies = set(year_dfs[years[0]]['company_code'])
        for year in years[1:]:
            if year in year_dfs:
                common_companies &= set(year_dfs[year]['company_code'])

        log_info(f"Found {len(common_companies)} companies with data for all specified years")

        if not common_companies:
            log_error("No companies have data for all specified years")
            return None

        for year in years:
            if year in year_dfs:
                year_dfs[year] = year_dfs[year][year_dfs[year]['company_code'].isin(common_companies)]

    merged_data = year_dfs[years[0]]

    for year in years[1:]:
        if year in year_dfs:
            merged_data = pd.merge(
                merged_data,
                year_dfs[year],
                on='company_code',
                how='inner',
                suffixes=('', f'_dup_{year}')
            )

            dup_cols = [col for col in merged_data.columns if f'_dup_{year}' in col]
            if dup_cols:
                log_warning(f"Dropping {len(dup_cols)} duplicate columns from the merge")
                merged_data = merged_data.drop(columns=dup_cols)

    if not merged_data.empty:
        if output_file and not return_dataframe:
            if safe_write_csv(merged_data, output_file, encoding='utf-8'):
                log_info(f"Saved {len(merged_data)} companies with multi-year data to {output_file}")
            else:
                log_error(f"Failed to save merged data to {output_file}")
        else:
            log_info(f"Created merged dataset with {len(merged_data)} companies")

    if not return_dataframe:
        cleanup_temp_files(pattern="temp_filtered_companies_*.csv")

    return merged_data
