import pandas as pd
import numpy as np
import json
from typing import List, Dict, Optional, Union

def _safe_float(val, default=0.0):
    try:
        return float(val) if val else default
    except (ValueError, TypeError):
        return default


from ..utils import (
    get_config,
    get_file_path,
    validate_file_exists,
    safe_read_csv,
    safe_write_csv,
    log_step,
    log_info,
    log_warning,
    log_error
)


def add_ownership_data(
    input_file: Optional[str] = "companies_with_industry.csv",
    input_data: Optional[pd.DataFrame] = None,
    output_file: Optional[str] = "companies_with_ownership.csv",
    shareholders_file: str = "shareholders.json",
    top_percentages: int = 3,
    top_names: int = 3,
    filters: dict = None,
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

    shareholders_path = get_file_path(shareholders_file)
    if not validate_file_exists(shareholders_file):
        log_error(f"Shareholders file {shareholders_file} not found")
        return companies_df

    log_info(f"Loading shareholders data from {shareholders_file}")

    try:
        with open(shareholders_path, 'r', encoding='utf-8') as f:
            shareholders_data = json.load(f)

        log_info(f"Loaded data for {len(shareholders_data)} companies from shareholders file")

        shareholders_dict = {str(company['ariregistri_kood']): company for company in shareholders_data}

        owner_counts = {}
        perc_json_map = {}
        names_json_map = {}

        for company_code, company_data in shareholders_dict.items():
            shareholders = company_data.get('osanikud', [])
            owner_count = len(shareholders)
            owner_counts[company_code] = owner_count

            if owner_count > 0:
                sorted_shareholders = sorted(
                    shareholders,
                    key=lambda x: _safe_float(x.get('osaluse_protsent', '0')),
                    reverse=True
                )

                top_perc = [float(s.get('osaluse_protsent', '0') or '0') for s in sorted_shareholders[:top_percentages]]
                top_perc.extend([0] * (top_percentages - len(top_perc)))
                perc_json_map[company_code] = json.dumps(top_perc)

                top_owner_names = []
                for s in sorted_shareholders[:top_names]:
                    first_name = s.get('eesnimi', '')
                    last_name = s.get('nimi_arinimi', '')
                    if first_name and last_name:
                        full_name = f"{first_name} {last_name}"
                    else:
                        full_name = last_name or first_name or 'Unknown'
                    top_owner_names.append(full_name)

                top_owner_names.extend([''] * (top_names - len(top_owner_names)))
                names_json_map[company_code] = json.dumps(top_owner_names)

        code_series = companies_df['company_code'].astype(str)
        companies_df['owner_count'] = code_series.map(owner_counts).fillna(0).astype(int)
        companies_df[f'top_{top_percentages}_percentages'] = code_series.map(perc_json_map)
        companies_df[f'top_{top_names}_owners'] = code_series.map(names_json_map)

        matched_count = int(code_series.isin(owner_counts).sum())
        log_info(f"Found ownership data for {matched_count} out of {len(companies_df)} companies")

        if filters:
            original_count = len(companies_df)

            if 'owner_count' in filters:
                count_filter = filters['owner_count']

                if 'exact' in count_filter and count_filter['exact']:
                    exact_values = count_filter['exact']
                    if not isinstance(exact_values, list):
                        exact_values = [exact_values]
                    companies_df = companies_df[companies_df['owner_count'].isin(exact_values)]
                    log_info(f"Filtered to companies with exactly {exact_values} owners: {len(companies_df)} remaining")

                elif 'min' in count_filter or 'max' in count_filter:
                    if 'min' in count_filter and count_filter['min'] is not None:
                        companies_df = companies_df[companies_df['owner_count'] >= count_filter['min']]
                        log_info(f"Filtered to companies with at least {count_filter['min']} owners: {len(companies_df)} remaining")

                    if 'max' in count_filter and count_filter['max'] is not None:
                        companies_df = companies_df[companies_df['owner_count'] <= count_filter['max']]
                        log_info(f"Filtered to companies with at most {count_filter['max']} owners: {len(companies_df)} remaining")

            if 'percentages' in filters and len(companies_df) > 0:
                percentage_filter = filters['percentages']

                def check_percentages(percentages_str, filter_config):
                    if not percentages_str or percentages_str == 'None':
                        return False

                    try:
                        percentages = json.loads(percentages_str)

                        if 'exact' in filter_config and filter_config['exact']:
                            exact_values = filter_config['exact']
                            if exact_values and not isinstance(exact_values[0], list):
                                exact_values = [exact_values]

                            for exact_pattern in exact_values:
                                if len(exact_pattern) <= len(percentages):
                                    match = True
                                    for i, target in enumerate(exact_pattern):
                                        if abs(percentages[i] - target) > 0.1:
                                            match = False
                                            break
                                    if match:
                                        return True
                            return False

                        if 'min' in filter_config and filter_config['min'] is not None:
                            min_val = filter_config['min']
                            if min_val is not None and percentages[0] < min_val:
                                return False

                        if 'max' in filter_config and filter_config['max'] is not None:
                            max_val = filter_config['max']
                            if max_val is not None and percentages[0] > max_val:
                                return False

                        return True

                    except (json.JSONDecodeError, IndexError, TypeError):
                        return False

                perc_col = f'top_{top_percentages}_percentages'
                mask = companies_df[perc_col].apply(lambda x: check_percentages(x, percentage_filter))
                filtered_df = companies_df[mask]

                if 'exact' in percentage_filter and percentage_filter['exact']:
                    exact_values = percentage_filter['exact']
                    log_info(f"Filtered to companies with ownership percentages matching {exact_values}: {len(filtered_df)} remaining")
                else:
                    log_info(f"Filtered by ownership percentage range: {len(filtered_df)} remaining")

                companies_df = filtered_df

            filtered_out = original_count - len(companies_df)
            log_info(f"Ownership filters removed {filtered_out} companies")

        perc_col = f'top_{top_percentages}_percentages'
        names_col = f'top_{top_names}_owners'

        def _fmt_percentages(s):
            if not s or s == 'None':
                return np.nan
            try:
                return ', '.join(f"{p:.2f}%" for p in json.loads(s) if p > 0) or np.nan
            except (json.JSONDecodeError, TypeError):
                return np.nan

        def _fmt_owners(s):
            if not s or s == 'None':
                return np.nan
            try:
                return ', '.join(o for o in json.loads(s) if o) or np.nan
            except (json.JSONDecodeError, TypeError):
                return np.nan

        companies_df[perc_col] = companies_df[perc_col].apply(_fmt_percentages)
        companies_df[names_col] = companies_df[names_col].apply(_fmt_owners)

        if output_file and not return_dataframe:
            if safe_write_csv(companies_df, output_file, encoding='utf-8'):
                log_info(f"Saved {len(companies_df)} companies with ownership data to {output_file}")
            else:
                log_error(f"Failed to save results to {output_file}")

        return companies_df

    except Exception as e:
        log_error(f"Error processing ownership data: {str(e)}")
        import traceback
        traceback.print_exc()
        return companies_df
