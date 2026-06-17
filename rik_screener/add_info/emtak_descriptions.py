import pandas as pd
from typing import List, Optional, Union

from ..utils import (
    get_config,
    safe_read_csv,
    safe_write_csv,
    log_step,
    log_info,
    log_warning,
    log_error
)


def add_emtak_descriptions(
    input_file: Optional[str] = "companies_with_industry.csv",
    input_data: Optional[pd.DataFrame] = None,
    output_file: Optional[str] = "companies_with_emtak_descriptions.csv",
    emtak_file: str = "emtak_2025.csv",
    years: List[int] = None,
    create_combined_columns: bool = True,
    return_dataframe: bool = False
) -> Union[pd.DataFrame, None]:
    config = get_config()
    if years is None:
        years = config.get_years()
    
    if input_data is not None:
        log_info(f"Using provided DataFrame with {len(input_data)} companies")
        companies_df = input_data.copy()
    else:
        log_info(f"Loading company data from {input_file}")
        companies_df = safe_read_csv(input_file)
        if companies_df is None:
            log_error(f"Failed to load input file {input_file}")
            return None
    
    log_info(f"Loaded {len(companies_df)} companies")
    log_info(f"Available columns: {companies_df.columns.tolist()}")
    
    log_info(f"Loading EMTAK codes from {emtak_file}")
    emtak_df = safe_read_csv(emtak_file, header=None, separator=',')
    if emtak_df is None:
        log_error(f"Failed to load EMTAK file {emtak_file}")
        return companies_df
    
    if emtak_df.shape[1] != 2:
        log_error(f"EMTAK file must have exactly 2 columns, found {emtak_df.shape[1]}")
        return companies_df
    
    emtak_df.columns = ['code', 'description']
    log_info(f"Loaded {len(emtak_df)} EMTAK codes")
    
    emtak_df['code'] = emtak_df['code'].astype(str).str.strip()
    emtak_df['description'] = emtak_df['description'].astype(str).str.strip()
    
    if emtak_df['code'].duplicated().any():
        duplicate_count = emtak_df['code'].duplicated().sum()
        log_warning(f"Found {duplicate_count} duplicate EMTAK codes, keeping first occurrence")
        emtak_df = emtak_df.drop_duplicates(subset='code', keep='first')
    
    emtak_dict = dict(zip(emtak_df['code'], emtak_df['description']))
    log_info(f"Created EMTAK lookup dictionary with {len(emtak_dict)} entries")
    
    processed_years = []
    total_mapped = 0
    total_unmapped = 0
    
    for year in years:
        industry_code_col = f"industry_code_{year}"
        
        if industry_code_col not in companies_df.columns:
            log_warning(f"Industry code column '{industry_code_col}' not found, skipping year {year}")
            continue
        
        log_info(f"Processing EMTAK descriptions for year {year}")
        
        companies_df[industry_code_col] = companies_df[industry_code_col].fillna('').astype(str).str.strip()
        companies_df[industry_code_col] = companies_df[industry_code_col].apply(
            lambda x: x.split('.')[0] if x and '.' in x and x != 'nan' else x
        )
        
        description_col = f"industry_description_{year}"
        companies_df[description_col] = companies_df[industry_code_col].map(emtak_dict)
        
        year_mapped = companies_df[description_col].notna().sum()
        year_total = (companies_df[industry_code_col] != '').sum()
        year_unmapped = year_total - year_mapped
        
        total_mapped += year_mapped
        total_unmapped += year_unmapped
        
        log_info(f"Year {year}: mapped {year_mapped} out of {year_total} industry codes")
        
        if create_combined_columns:
            combined_col = f"industry_combined_{year}"
            companies_df[combined_col] = companies_df.apply(
                lambda row: f"{row[industry_code_col]} - {row[description_col]}"
                if pd.notna(row[description_col]) and row[industry_code_col] != '' and row[industry_code_col] != 'nan'
                else row[industry_code_col] if row[industry_code_col] != '' and row[industry_code_col] != 'nan'
                else '',
                axis=1
            )
            log_info(f"Created combined column: {combined_col}")
        
        unmapped_codes = companies_df.loc[
            companies_df[description_col].isna() & 
            (companies_df[industry_code_col] != '') & 
            (companies_df[industry_code_col] != 'nan'),
            industry_code_col
        ].unique()
        
        if len(unmapped_codes) > 0:
            log_warning(f"Year {year}: {len(unmapped_codes)} unique codes could not be mapped")
            if len(unmapped_codes) <= 10:
                log_warning(f"Unmapped codes: {unmapped_codes.tolist()}")
            else:
                log_warning(f"First 10 unmapped codes: {unmapped_codes[:10].tolist()}")
        
        processed_years.append(year)
    
    if not processed_years:
        log_warning("No industry code columns found for any of the specified years")
        return companies_df
    
    log_info(f"=== EMTAK MAPPING SUMMARY ===")
    log_info(f"Processed years: {processed_years}")
    log_info(f"Total codes mapped: {total_mapped}")
    log_info(f"Total codes unmapped: {total_unmapped}")
    if total_mapped + total_unmapped > 0:
        mapping_rate = (total_mapped / (total_mapped + total_unmapped)) * 100
        log_info(f"Overall mapping rate: {mapping_rate:.1f}%")
    
    if output_file and not return_dataframe:
        if safe_write_csv(companies_df, output_file, encoding='utf-8'):
            log_info(f"Saved {len(companies_df)} companies with EMTAK descriptions to {output_file}")
        else:
            log_error(f"Failed to save results to {output_file}")
            return None
    
    return companies_df


def get_industry_summary(
    data: pd.DataFrame,
    year: int,
    top_n: int = 20
) -> pd.DataFrame:
    industry_col = f"industry_code_{year}"
    description_col = f"industry_description_{year}"
    
    if industry_col not in data.columns:
        log_error(f"Industry code column '{industry_col}' not found")
        return pd.DataFrame()
    
    valid_data = data[
        (data[industry_col] != '') & 
        (data[industry_col] != 'nan') & 
        data[industry_col].notna()
    ].copy()
    
    if len(valid_data) == 0:
        log_warning(f"No valid industry codes found for year {year}")
        return pd.DataFrame()
    
    industry_counts = valid_data[industry_col].value_counts().head(top_n)
    
    summary = pd.DataFrame({
        'industry_code': industry_counts.index,
        'company_count': industry_counts.values,
        'percentage': (industry_counts.values / len(valid_data) * 100).round(2)
    })
    
    if description_col in data.columns:
        code_to_desc = dict(zip(valid_data[industry_col], valid_data[description_col]))
        summary['industry_description'] = summary['industry_code'].map(code_to_desc)
    
    log_info(f"Industry summary for {year}: {len(summary)} industries covering {summary['company_count'].sum()} companies")
    
    return summary
