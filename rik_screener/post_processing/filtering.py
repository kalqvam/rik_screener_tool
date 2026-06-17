import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Union

from ..utils import (
    get_config,
    safe_read_csv,
    safe_write_csv,
    validate_file_exists,
    log_info,
    log_warning,
    log_error,
    validate_columns
)


def filter_and_rank(
    input_file: Optional[str] = "companies_with_ratios.csv",
    input_data: Optional[pd.DataFrame] = None,
    output_file: Optional[str] = "ranked_companies.csv",
    sort_column: str = "EBITDA_Margin",
    filters: list = None,
    ascending: bool = False,
    top_n: int = None,
    export_columns: list = None,
    return_dataframe: bool = False
) -> Union[pd.DataFrame, None]:
    
    if input_data is not None:
        log_info(f"Using provided DataFrame with {len(input_data)} companies")
        companies_df = input_data.copy()
    else:
        log_info(f"Loading companies with ratios from {input_file}")
        companies_df = safe_read_csv(input_file)
        if companies_df is None:
            log_error(f"Failed to load input file {input_file}")
            return None

    original_count = len(companies_df)
    log_info(f"Loaded {original_count} companies")

    if filters:
        for filter_idx, filter_dict in enumerate(filters):
            column = filter_dict.get("column")
            min_val = filter_dict.get("min")
            max_val = filter_dict.get("max")

            if column not in companies_df.columns:
                log_warning(f"Filter column '{column}' not found in data. Skipping this filter")
                continue

            if min_val is not None and max_val is not None and min_val > max_val:
                log_warning(f"Filter on '{column}': min ({min_val}) > max ({max_val}) — will produce empty results")

            before_filter = len(companies_df)

            if min_val is not None:
                companies_df = companies_df[companies_df[column] >= min_val]

            if max_val is not None:
                companies_df = companies_df[companies_df[column] <= max_val]

            after_filter = len(companies_df)
            filtered_out = before_filter - after_filter

            filter_desc = []
            if min_val is not None:
                filter_desc.append(f"min={min_val}")
            if max_val is not None:
                filter_desc.append(f"max={max_val}")

            log_info(f"Filter {filter_idx+1}: {column} {' '.join(filter_desc)} removed {filtered_out} companies")

            if companies_df.empty:
                log_warning(f"No companies remain after applying filter {filter_idx+1} on {column}")
                return companies_df

    total_filtered = original_count - len(companies_df)
    log_info(f"Total filtered: {total_filtered} companies")
    log_info(f"Remaining companies: {len(companies_df)}")

    if sort_column is not None:
        if sort_column not in companies_df.columns:
            log_warning(f"Sort column '{sort_column}' not found in data — skipping sort")
        else:
            companies_df = companies_df.sort_values(by=sort_column, ascending=ascending)
            log_info(f"Sorted companies by {sort_column} ({'ascending' if ascending else 'descending'})")

    if top_n is not None and top_n > 0:
        companies_df = companies_df.head(top_n)
        log_info(f"Limited results to top {top_n} companies")

    if export_columns is not None:
        missing_columns = [col for col in export_columns if col not in companies_df.columns]
        if missing_columns:
            log_warning(f"These export columns were not found: {missing_columns}")
            export_columns = [col for col in export_columns if col in companies_df.columns]

        companies_df = companies_df[export_columns]
        log_info(f"Selected {len(export_columns)} columns for export")

    if output_file and not return_dataframe:
        if safe_write_csv(companies_df, output_file, encoding='utf-8-sig'):
            log_info(f"Saved {len(companies_df)} ranked companies to {output_file}")
        else:
            log_error(f"Failed to save results to {output_file}")

    return companies_df
