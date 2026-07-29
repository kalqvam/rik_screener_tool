import pandas as pd
from typing import List, Optional, Set

from ...utils import (
    safe_read_csv,
    log_info,
    log_warning
)

_USECOLS = ["report_id", "tabel", "elemendi_label", "vaartus"]


def load_financial_data(
    year: int,
    financial_items: List[str],
    report_id_filter: Optional[Set] = None,
) -> Optional[pd.DataFrame]:
    financial_file = f"financials_{year}.csv"

    if report_id_filter is not None:
        if not report_id_filter:
            return None
        reader = safe_read_csv(financial_file, usecols=_USECOLS, chunk_size=200_000)
        if reader is None:
            log_warning(f"Financial file for {year} not found")
            return None
        chunks = []
        for chunk in reader:
            chunk.columns = chunk.columns.str.strip()
            filtered = chunk[chunk["report_id"].isin(report_id_filter)]
            if not filtered.empty:
                chunks.append(filtered)
        if not chunks:
            log_info(f"No financial data found for year {year} with provided report_ids")
            return None
        financial_data = pd.concat(chunks, ignore_index=True)
    else:
        financial_data = safe_read_csv(financial_file, usecols=_USECOLS)
        if financial_data is None:
            log_warning(f"Financial file for {year} not found")
            return None

    log_info(f"Reading financial data for {year}")
    
    financial_data['clean_elemendi_label'] = financial_data['elemendi_label'].str.replace(' Konsolideeritud$', '', regex=True)
    financial_data = financial_data[financial_data["clean_elemendi_label"].isin(financial_items)]
    log_info(f"Found {len(financial_data)} financial records for year {year}")
    
    financial_data['is_consolidated'] = (
        financial_data['tabel'].str.contains('Konsolideeritud', case=False, na=False) |
        financial_data['elemendi_label'].str.contains(' Konsolideeritud$', regex=True, na=False)
    )
    
    consolidated_reports = financial_data.groupby('report_id')['is_consolidated'].any()
    financial_data['has_consolidated'] = financial_data['report_id'].map(consolidated_reports)
    
    financial_data = financial_data[
        (~financial_data['has_consolidated']) |
        (financial_data['has_consolidated'] & financial_data['is_consolidated'])
    ]
    
    log_info(f"After consolidated filtering: {len(financial_data)} records")
    
    financial_wide = financial_data.pivot_table(
        index="report_id",
        columns="clean_elemendi_label",
        values="vaartus",
        aggfunc='first'
    ).reset_index()
    
    financial_cols = financial_wide.columns.difference(['report_id'])
    rename_dict = {col: f"{col}_{year}" for col in financial_cols}
    financial_wide = financial_wide.rename(columns=rename_dict)
    
    is_consolidated = financial_data.groupby('report_id')['is_consolidated'].any()
    financial_wide['is_consolidated'] = financial_wide['report_id'].map(is_consolidated)
    financial_wide = financial_wide.rename(columns={'is_consolidated': f'is_consolidated_{year}'})
    
    log_info(f"Financial columns for {year} after renaming: {sorted([col for col in financial_wide.columns if col != 'report_id'])}")
    
    return financial_wide
