from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

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
    parse_statement_codes_by_year,
    parse_consolidation_status_by_year,
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

def check_statement_consistency(
    company_codes: List[str],
    username: str,
    password: str,
    target_year: int,
    end_year: int,
    statement_types: List[str] = None,
    rate_limit: int = 20,
    output_file: Optional[str] = None
) -> Dict[str, Tuple[str, List[List[Optional[str]]], str]]:
    """
    Check if statement codes remained consistent across a range of years for multiple companies.

    Args:
        company_codes: List of company registry codes (or single code as list)
        username: RIK API username
        password: RIK API password
        target_year: Starting year (most recent)
        end_year: Ending year (oldest)
        statement_types: List of statement types to check (e.g., ["BS", "IS", "CF"]). Defaults to ["BS", "IS", "CF"]
        rate_limit: Requests per minute (default: 20)
        output_file: Optional CSV file path to save results

    Returns:
        Dictionary with company_code as key and tuple (answer, array, consolidation_status) as value:
        - answer: "Yes" if all codes are consistent, "No" if any changed or missing
        - array: List of up to 3 lists (one per statement type), each containing statement codes
                 ordered from target_year to end_year
        - consolidation_status: "Non-consolidated", "Consolidated", or "Consolidated since yyyy"
    """
    if statement_types is None:
        statement_types = ["BS", "IS", "CF"]

    if target_year < end_year:
        raise ValueError(f"target_year ({target_year}) must be >= end_year ({end_year})")

    set_api_config(username, password, rate_limit)

    valid_codes = validate_company_codes(company_codes)

    if not valid_codes:
        print("No valid company codes provided")
        return {}

    # Validate statement types
    valid_statement_types = []
    for st in statement_types:
        st_upper = st.upper()
        if st_upper not in ["BS", "IS", "CF"]:
            print(f"Warning: Invalid statement type '{st}', skipping. Valid types: BS, IS, CF")
            continue
        valid_statement_types.append(st_upper)

    if not valid_statement_types:
        print("No valid statement types provided")
        return {}

    print(f"Checking statement consistency for {len(valid_codes)} companies")
    print(f"Year range: {target_year} to {end_year}")
    print(f"Statement types: {', '.join(valid_statement_types)}")
    print(f"Rate limit: {rate_limit}/min")

    results = {}

    for i, company_code in enumerate(valid_codes, 1):
        print(format_progress(i, len(valid_codes), "Checking consistency"))

        xml_response = get_annual_reports_list(company_code)

        if xml_response is None:
            print(f"Failed to get data for company {company_code}")
            results[company_code] = ("No", [[] for _ in valid_statement_types], "Non-consolidated")
            continue

        # Parse statement codes by year
        codes_by_type = parse_statement_codes_by_year(
            xml_response,
            target_year,
            end_year,
            valid_statement_types
        )

        # Parse consolidation status by year
        consolidation_by_year = parse_consolidation_status_by_year(
            xml_response,
            target_year,
            end_year
        )

        # Check consistency for each statement type
        is_consistent = True
        result_arrays = []

        for st in valid_statement_types:
            codes = codes_by_type.get(st, [])

            # Filter out None values for consistency check
            non_none_codes = [c for c in codes if c is not None]

            # Check if all non-None codes are the same
            if len(non_none_codes) > 0:
                unique_codes = set(non_none_codes)
                if len(unique_codes) > 1:
                    is_consistent = False
                # If any code is missing (None), mark as inconsistent
                if None in codes:
                    is_consistent = False
            else:
                # No codes found for this statement type
                is_consistent = False

            result_arrays.append(codes)

        # Determine consolidation status
        consolidation_status = _determine_consolidation_status(
            consolidation_by_year,
            target_year,
            end_year
        )

        answer = "Yes" if is_consistent else "No"
        results[company_code] = (answer, result_arrays, consolidation_status)

    print(f"\nCompleted: {len(results)} companies processed")

    # Save to CSV if output_file is specified
    if output_file:
        _save_consistency_results_to_csv(
            results,
            valid_statement_types,
            target_year,
            end_year,
            output_file
        )
        print(f"Results saved to {output_file}")

    return results

def _determine_consolidation_status(
    consolidation_by_year: Dict[int, bool],
    target_year: int,
    end_year: int
) -> str:
    """
    Determine consolidation status based on year-by-year consolidation flags.

    Args:
        consolidation_by_year: Dictionary with year as key and consolidation flag as value
        target_year: Starting year (most recent)
        end_year: Ending year (oldest)

    Returns:
        One of: "Non-consolidated", "Consolidated", "Consolidated since yyyy", "Non-consolidated since yyyy"
    """
    if not consolidation_by_year:
        return "Non-consolidated"

    # Build ordered list from target_year to end_year
    years = list(range(target_year, end_year - 1, -1))
    consolidation_flags = [consolidation_by_year.get(year, False) for year in years]

    # Count consolidated vs non-consolidated years
    consolidated_years = [y for y, flag in zip(years, consolidation_flags) if flag]
    non_consolidated_years = [y for y, flag in zip(years, consolidation_flags) if not flag]

    # All years consolidated
    if len(consolidated_years) == len(years):
        return "Consolidated"

    # No years consolidated
    if len(non_consolidated_years) == len(years):
        return "Non-consolidated"

    # Mixed - need to determine pattern
    # Check if there's a transition point
    # Note: iteration goes from newest to oldest, but "since" refers to chronological transitions

    # Find the first year where consolidation status changes
    first_consolidated_idx = None
    first_non_consolidated_after_consolidated_idx = None

    for i, flag in enumerate(consolidation_flags):
        if flag and first_consolidated_idx is None:
            first_consolidated_idx = i
        if not flag and first_consolidated_idx is not None and first_non_consolidated_after_consolidated_idx is None:
            first_non_consolidated_after_consolidated_idx = i

    # Recent years non-consolidated, older years consolidated
    # Chronologically: was consolidated, then STOPPED consolidating
    if first_consolidated_idx is not None and first_consolidated_idx > 0:
        # Check if all subsequent years (going back in time) are consolidated
        all_subsequent_consolidated = all(consolidation_flags[first_consolidated_idx:])
        if all_subsequent_consolidated:
            # years[first_consolidated_idx - 1] is the first non-consolidated year chronologically
            return f"Non-consolidated since {years[first_consolidated_idx - 1]}"

    # Recent years consolidated, older years non-consolidated
    # Chronologically: was non-consolidated, then STARTED consolidating
    if first_non_consolidated_after_consolidated_idx is not None:
        # Check if all subsequent years (going back in time) are non-consolidated
        all_subsequent_non_consolidated = all(not flag for flag in consolidation_flags[first_non_consolidated_after_consolidated_idx:])
        if all_subsequent_non_consolidated:
            # years[first_non_consolidated_after_consolidated_idx - 1] is the first consolidated year chronologically
            return f"Consolidated since {years[first_non_consolidated_after_consolidated_idx - 1]}"

    # Mixed pattern with no clear transition - default to showing most recent status
    if consolidation_flags[0]:  # Most recent year
        return "Consolidated"
    else:
        return "Non-consolidated"

def _save_consistency_results_to_csv(
    results: Dict[str, Tuple[str, List[List[Optional[str]]], str]],
    statement_types: List[str],
    target_year: int,
    end_year: int,
    output_file: str
) -> None:
    """
    Save consistency check results to a CSV file.

    Args:
        results: Dictionary from check_statement_consistency
        statement_types: List of statement types checked
        target_year: Starting year
        end_year: Ending year
        output_file: Path to output CSV file
    """
    rows = []

    for company_code, (answer, code_arrays, consolidation_status) in results.items():
        row = {
            'company_code': company_code,
            'consistent': answer,
            'year_range': f"{target_year}-{end_year}",
            'consolidation_status': consolidation_status
        }

        # Add columns for each statement type
        for i, st in enumerate(statement_types):
            if i < len(code_arrays):
                codes = code_arrays[i]
                # Convert list to string representation
                codes_str = ','.join([str(c) if c is not None else 'N/A' for c in codes])
                row[f'{st}_codes'] = codes_str

                # Check if this specific statement type is consistent
                non_none_codes = [c for c in codes if c is not None]
                if len(non_none_codes) > 0 and None not in codes:
                    unique_codes = set(non_none_codes)
                    row[f'{st}_consistent'] = 'Yes' if len(unique_codes) == 1 else 'No'
                else:
                    row[f'{st}_consistent'] = 'No'
            else:
                row[f'{st}_codes'] = ''
                row[f'{st}_consistent'] = 'No'

        rows.append(row)

    df = pd.DataFrame(rows)

    # Reorder columns
    base_cols = ['company_code', 'consistent', 'consolidation_status', 'year_range']
    st_cols = []
    for st in statement_types:
        st_cols.extend([f'{st}_codes', f'{st}_consistent'])

    df = df[base_cols + st_cols]

    df.to_csv(output_file, index=False, encoding='utf-8-sig')

    return None
