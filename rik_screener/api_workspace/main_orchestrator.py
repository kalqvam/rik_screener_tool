from datetime import datetime
from typing import Iterable, List, Optional, Tuple

import pandas as pd

from .config_auth import set_api_config
from .endpoints import (
    get_annual_reports_list,
    get_company_basic_info,
    get_financial_statement_details,
)
from .data_processors import (
    create_latest_reports_dataframe,
    extract_statement_code,
    parse_annual_reports_response,
    parse_company_info_response,
    parse_financial_statement_response,
)
from .utils import format_progress, validate_company_codes

def get_latest_reports_info(
    company_codes: List[str],
    username: str,
    password: str,
    include_names: bool = False,
    rate_limit: int = 20
) -> pd.DataFrame:
    
    set_api_config(username, password, rate_limit)
    
    valid_codes = validate_company_codes(company_codes)
    
    if not valid_codes:
        print("No valid company codes provided")
        return pd.DataFrame()
    
    print(f"Processing {len(valid_codes)} companies with rate limit {rate_limit}/min")
    
    reports_data = []
    
    for i, company_code in enumerate(valid_codes, 1):
        print(format_progress(i, len(valid_codes), "Fetching reports"))
        
        xml_response = get_annual_reports_list(company_code)
        
        if xml_response is not None:
            report_info = parse_annual_reports_response(xml_response, company_code)
            if report_info:
                reports_data.append(report_info)
        else:
            print(f"Failed to get data for company {company_code}")
    
    names_data = None
    
    if include_names and reports_data:
        print(f"\nFetching company names...")
        names_data = {}
        
        for i, report in enumerate(reports_data, 1):
            company_code = report['company_code']
            print(format_progress(i, len(reports_data), "Fetching names"))
            
            xml_response = get_company_basic_info(company_code)
            
            if xml_response is not None:
                company_name = parse_company_info_response(xml_response, company_code)
                if company_name:
                    names_data[company_code] = company_name
    
    df = create_latest_reports_dataframe(reports_data, names_data)

    print(f"\nCompleted: {len(df)} companies processed successfully")

    return df

def _generate_years(starting_year: int, num_requests: int, step: int) -> List[int]:
    return [starting_year - (step * i) for i in range(num_requests)]

def _merge_year_frames(existing: pd.DataFrame, new_frame: pd.DataFrame) -> pd.DataFrame:
    if existing is None:
        return new_frame

    union_index = existing.index.union(new_frame.index)

    existing_values = existing.drop(columns=['line_name'], errors='ignore').reindex(union_index)
    new_values = new_frame.drop(columns=['line_name'], errors='ignore').reindex(union_index)

    merged_values = pd.concat([existing_values, new_values], axis=1)

    existing_names = existing['line_name'] if 'line_name' in existing else pd.Series(dtype=object)
    new_names = new_frame['line_name'] if 'line_name' in new_frame else pd.Series(dtype=object)

    if existing_names.empty and not new_names.empty:
        merged_names = new_names.reindex(union_index)
    elif new_names.empty and not existing_names.empty:
        merged_names = existing_names.reindex(union_index)
    else:
        merged_names = existing_names.reindex(union_index).combine_first(new_names.reindex(union_index))

    if not merged_names.empty:
        merged_values.insert(0, 'line_name', merged_names)

    merged_values.index.name = 'line_code'

    return merged_values

def _collect_company_statements(
    company_code: str,
    years: Iterable[int],
    statement_type: str
) -> Optional[Tuple[pd.Series, pd.DataFrame]]:

    xml_list = get_annual_reports_list(company_code)
    if xml_list is None:
        print(f"Failed to retrieve annual reports list for {company_code}")
        return None

    try:
        statement_code = extract_statement_code(xml_list, years, statement_type)
    except ValueError as exc:
        print(f"{company_code}: {exc}")
        return None

    combined_frame: Optional[pd.DataFrame] = None

    for year in years:
        statement_xml = get_financial_statement_details(company_code, statement_code, year)

        if statement_xml is None:
            print(f"{company_code}: failed to retrieve {statement_type} for {year}")
            continue

        year_frame = parse_financial_statement_response(statement_xml)

        if year_frame.empty:
            continue

        year_frame = year_frame.set_index('line_code')

        value_columns = [col for col in year_frame.columns if col != 'line_name']
        rename_map = {col: f"{year}_{col}" for col in value_columns}
        year_frame = year_frame.rename(columns=rename_map)

        combined_frame = _merge_year_frames(combined_frame, year_frame)

    if combined_frame is None or combined_frame.empty:
        print(f"{company_code}: no statement data retrieved")
        return None

    value_frame = combined_frame.drop(columns=['line_name'], errors='ignore')

    if value_frame.empty:
        print(f"{company_code}: statement contains no numeric values")
        return None

    line_names = combined_frame['line_name'] if 'line_name' in combined_frame else pd.Series(dtype=object)
    line_names = line_names.reindex(value_frame.index)
    line_names.name = 'line_name'

    multi_columns = pd.MultiIndex.from_tuples(
        [(company_code, column) for column in value_frame.columns],
        names=['company_code', 'period']
    )

    value_frame.columns = multi_columns
    value_frame.index.name = 'line_code'

    return line_names, value_frame

def get_financial_statements(
    company_codes: List[str],
    username: str,
    password: str,
    statement_type: str = "BS",
    starting_year: Optional[int] = None,
    num_requests: int = 1,
    year_step: int = 2,
    rate_limit: int = 20
) -> pd.DataFrame:

    if num_requests < 1:
        raise ValueError("num_requests must be at least 1")

    if year_step < 1:
        raise ValueError("year_step must be at least 1")

    if starting_year is None:
        starting_year = datetime.utcnow().year

    years = _generate_years(starting_year, num_requests, year_step)

    set_api_config(username, password, rate_limit)

    valid_codes = validate_company_codes(company_codes)

    if not valid_codes:
        print("No valid company codes provided")
        return pd.DataFrame()

    print(f"Processing {len(valid_codes)} companies for {statement_type.upper()} statements")

    value_frames: List[pd.DataFrame] = []
    line_name_series: List[pd.Series] = []

    for index, company_code in enumerate(valid_codes, 1):
        print(format_progress(index, len(valid_codes), "Statements"))

        result = _collect_company_statements(company_code, years, statement_type)

        if result is None:
            continue

        names, values = result

        value_frames.append(values)
        line_name_series.append(names.rename(company_code))

    if not value_frames:
        print("No statement data collected")
        return pd.DataFrame()

    combined_values = pd.concat(value_frames, axis=1, sort=True)

    if line_name_series:
        names_frame = pd.concat(line_name_series, axis=1, sort=True)
        line_name_column = names_frame.bfill(axis=1).iloc[:, 0]
    else:
        line_name_column = pd.Series(index=combined_values.index, dtype=object)

    combined_values.insert(0, 'line_name', line_name_column)
    combined_values.index.name = 'line_code'

    return combined_values
