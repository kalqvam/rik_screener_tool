"""Standalone EMTA company screener.

Loads EMTA quarterly VAT-turnover CSV files and screens companies by growth
and other EMTA-native signals. Completely independent of RIK CSV data.

Data source: Estonian Tax Authority (EMTA) — avaandmed.emta.ee
Files expected in the data folder:
  tasutud_maksud_kaesolev_aasta.csv   (current year quarters)
  tasutud_maksud_varasemad_aastad.csv (historical quarters, 2022-2024)

Data shape: wide format — one row per company per year, quarterly values in
separate columns (Käive I kv … Käive IV kv, Töötajate arv I kv … IV kv).
Files are comma-separated (unlike the RIK CSVs which use semicolons).

Important caveats about EMTA turnover (käive):
- It is the sum of VAT declaration lines 1-3, NOT company revenue.
- It includes reverse-charge VAT purchases as well as the company's own sales.
- Filings are 1-month lagged (Q1 label = Dec-Feb activity).
- Absolute values are not comparable to RIK financial statement revenue.
- YoY growth of the same quarter is a reliable signal for volume trends.
"""

import pandas as pd
import numpy as np
from typing import Any, Dict, List, Optional, Tuple

from .utils import (
    safe_read_csv,
    log_info,
    log_warning,
    log_error,
    log_step,
)

_EMTA_SEPARATOR = ','

_ROMAN_TO_QUARTER = {'i': 1, 'ii': 2, 'iii': 3, 'iv': 4}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return (
        s.lower()
         .replace('ä', 'a').replace('ö', 'o')
         .replace('ü', 'u').replace('õ', 'o')
    )


def _find_col(columns: List[str], patterns: List[str], label: str) -> Optional[str]:
    for col in columns:
        n = _norm(col)
        for pat in patterns:
            if pat in n:
                return col
    log_error(f"EMTA: could not find {label} column. Available: {columns}")
    return None


def _find_quarterly_cols(columns: List[str], keyword: str) -> Dict[int, str]:
    """Return {quarter_num: col_name} for columns matching keyword + Roman numeral suffix."""
    result: Dict[int, str] = {}
    for col in columns:
        n = _norm(col)
        if keyword not in n:
            continue
        for roman, qnum in _ROMAN_TO_QUARTER.items():
            if n.endswith(f' {roman} kv'):
                result[qnum] = col
                break
    return result


def _load_emta_files(
    current_file: str,
    historical_file: str,
) -> Optional[pd.DataFrame]:
    """Load and concatenate EMTA CSV files. Returns None if neither is found."""
    frames = []
    for fname in [current_file, historical_file]:
        df = safe_read_csv(fname, separator=_EMTA_SEPARATOR)
        if df is not None and not df.empty:
            log_info(f"Loaded {len(df)} rows from {fname}")
            frames.append(df)
        else:
            log_warning(f"Could not load {fname} — skipping")
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def _select_reference_quarter(
    pivot: pd.DataFrame,
    min_coverage_ratio: float,
) -> Tuple[Optional[str], Optional[str]]:
    """Return (ref_period, prior_period) or (None, None) if none qualify."""
    total = len(pivot)
    for candidate in sorted(pivot.columns.tolist(), reverse=True):
        active = (pivot[candidate].notna() & (pivot[candidate] > 0)).sum()
        coverage = active / total
        if coverage >= min_coverage_ratio:
            ref_year = int(candidate[:4])
            ref_q = candidate[4:]
            prior = f"{ref_year - 1}{ref_q}"
            if prior in pivot.columns:
                log_info(
                    f"Reference quarter: {candidate} (coverage {coverage:.1%}, "
                    f"{active} companies). Comparing to: {prior}"
                )
                return candidate, prior
            log_warning(
                f"Quarter {candidate} has good coverage but prior {prior} missing — trying next"
            )
    return None, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_emta_screening(config: Dict[str, Any]) -> pd.DataFrame:
    """Load EMTA quarterly VAT-turnover data, compute YoY growth, filter and rank.

    Returns a DataFrame with EMTA-native columns (English names). Does NOT
    depend on any RIK CSV data.

    Config keys
    -----------
    emta_current_file : str
        Logical filename for current-year EMTA data.
        Default: "tasutud_maksud_kaesolev_aasta.csv"
    emta_historical_file : str
        Logical filename for historical EMTA data.
        Default: "tasutud_maksud_varasemad_aastad.csv"
    min_coverage_ratio : float
        Minimum share of companies that must have non-zero turnover in a quarter
        for it to be selected as the reference quarter. Default: 0.10
    min_turnover_yoy : float
        Minimum YoY growth rate filter (e.g. 0.20 = +20%). Optional.
    max_turnover_yoy : float
        Maximum YoY growth rate filter. Optional.
    min_turnover : float
        Minimum turnover (€) in the reference quarter. Excludes micro-filers.
        Optional.
    industry_keyword : str
        Case-insensitive substring match on the EMTA industry (Tegevusala).
        Optional.
    region : str
        Case-insensitive substring match on the EMTA county (Maakond). Optional.
    company_codes : list[str]
        Restrict to specific registry codes. Optional.
    top_n : int
        Maximum companies to return. Default: 50.
    sort_by : str
        Column to sort by. One of: 'turnover_yoy' (default), 'turnover_current',
        'employees'.
    sort_ascending : bool
        Sort direction. Default: False (highest first).
    """
    log_step("Loading EMTA data")

    current_file = config.get('emta_current_file', 'tasutud_maksud_kaesolev_aasta.csv')
    historical_file = config.get('emta_historical_file', 'tasutud_maksud_varasemad_aastad.csv')
    min_coverage = config.get('min_coverage_ratio', 0.10)
    top_n = config.get('top_n', 50)
    sort_by = config.get('sort_by', 'turnover_yoy')
    sort_ascending = config.get('sort_ascending', False)

    emta_df = _load_emta_files(current_file, historical_file)
    if emta_df is None:
        log_error("No EMTA files found — cannot run EMTA screening")
        return pd.DataFrame()

    cols = emta_df.columns.tolist()

    # Detect structural columns
    company_col = _find_col(cols, ['registrikood'], 'company code (Registrikood)')
    name_col = _find_col(cols, ['nimi'], 'company name (Nimi)')
    year_col = _find_col(cols, ['aasta'], 'year (Aasta)')
    region_col = _find_col(cols, ['maakond'], 'region (Maakond)')
    industry_col = _find_col(cols, ['tegevusala'], 'industry (Tegevusala)')
    type_col = _find_col(cols, ['liik'], 'company type (Liik)')

    if company_col is None or year_col is None:
        return pd.DataFrame()

    turnover_cols = _find_quarterly_cols(cols, 'kaiv')
    employee_cols = _find_quarterly_cols(cols, 'tootajate arv')

    if not turnover_cols:
        log_error(f"No quarterly turnover columns found. Available: {cols}")
        return pd.DataFrame()

    log_info(f"Turnover columns: {turnover_cols}")
    if employee_cols:
        log_info(f"Employee columns: {employee_cols}")

    # Clean data
    emta_df[company_col] = emta_df[company_col].astype(str).str.strip()
    emta_df[year_col] = pd.to_numeric(emta_df[year_col], errors='coerce')
    emta_df = emta_df.dropna(subset=[year_col])
    emta_df[year_col] = emta_df[year_col].astype(int)

    for col in list(turnover_cols.values()) + list(employee_cols.values()):
        emta_df[col] = pd.to_numeric(
            emta_df[col].astype(str).str.replace(' ', '').str.replace(',', '.'),
            errors='coerce'
        )

    # Melt turnover wide → long, then pivot by period
    log_step("Computing quarterly periods")
    melt_tv = emta_df[[company_col, year_col] + list(turnover_cols.values())].melt(
        id_vars=[company_col, year_col],
        value_vars=list(turnover_cols.values()),
        var_name='_tv_col',
        value_name='turnover',
    )
    col_to_q = {v: k for k, v in turnover_cols.items()}
    melt_tv['period'] = (
        melt_tv[year_col].astype(str) + 'Q' +
        melt_tv['_tv_col'].map(col_to_q).astype(str)
    )
    agg_tv = melt_tv.groupby([company_col, 'period'])['turnover'].sum().reset_index()
    pivot = agg_tv.pivot(index=company_col, columns='period', values='turnover')

    # Select reference and prior quarters
    ref_period, prior_period = _select_reference_quarter(pivot, min_coverage)
    if ref_period is None:
        log_error("Could not find a reference quarter with sufficient coverage")
        return pd.DataFrame()

    # Compute YoY growth
    current_tv = pivot[ref_period]
    prior_tv = pivot[prior_period]
    yoy = (current_tv - prior_tv) / prior_tv.abs()
    yoy[prior_tv.abs() < 1] = np.nan

    # Melt employees for the reference quarter
    ref_q_num = int(ref_period[5:])
    emp_current: Optional[pd.Series] = None
    if ref_q_num in employee_cols:
        emp_col = employee_cols[ref_q_num]
        emp_lookup = emta_df[
            emta_df[year_col] == int(ref_period[:4])
        ].groupby(company_col)[emp_col].sum()
        emp_current = emp_lookup

    # Build output: one row per company in the EMTA universe
    log_step("Building output DataFrame")

    # Base: metadata columns (take last record per company for stable values)
    meta_cols = [c for c in [company_col, name_col, region_col, industry_col, type_col] if c]
    meta = emta_df[meta_cols].drop_duplicates(subset=[company_col], keep='last')

    result = pd.DataFrame({
        'company_code': pivot.index,
        'turnover_current': current_tv.values,
        'turnover_prior': prior_tv.values,
        'turnover_yoy': yoy.values,
    }).reset_index(drop=True)

    result['company_code'] = result['company_code'].astype(str).str.strip()

    if emp_current is not None:
        result = result.merge(
            emp_current.reset_index().rename(columns={emp_col: 'employees', company_col: 'company_code'}),
            on='company_code', how='left'
        )

    # Merge metadata
    meta = meta.rename(columns={company_col: 'company_code'})
    rename_meta = {}
    if name_col:
        rename_meta[name_col] = 'company_name'
    if region_col:
        rename_meta[region_col] = 'region'
    if industry_col:
        rename_meta[industry_col] = 'industry'
    if type_col:
        rename_meta[type_col] = 'company_type'
    meta = meta.rename(columns=rename_meta)
    meta['company_code'] = meta['company_code'].astype(str).str.strip()

    result = result.merge(meta, on='company_code', how='left')
    result['period'] = ref_period
    result['period_prior'] = prior_period

    # Apply filters
    log_step("Filtering results")
    n_before = len(result)

    min_yoy = config.get('min_turnover_yoy')
    max_yoy = config.get('max_turnover_yoy')
    min_tv = config.get('min_turnover')
    industry_kw = config.get('industry_keyword')
    region_kw = config.get('region')
    company_codes = config.get('company_codes')

    if company_codes:
        result = result[result['company_code'].isin([str(c) for c in company_codes])]

    if min_yoy is not None:
        result = result[result['turnover_yoy'].notna() & (result['turnover_yoy'] >= min_yoy)]

    if max_yoy is not None:
        result = result[result['turnover_yoy'].notna() & (result['turnover_yoy'] <= max_yoy)]

    if min_tv is not None:
        result = result[result['turnover_current'].notna() & (result['turnover_current'] >= min_tv)]

    if industry_kw and 'industry' in result.columns:
        mask = result['industry'].str.contains(industry_kw, case=False, na=False)
        result = result[mask]

    if region_kw and 'region' in result.columns:
        mask = result['region'].str.contains(region_kw, case=False, na=False)
        result = result[mask]

    log_info(f"Filters: {n_before} → {len(result)} companies")

    # Sort and top_n
    sort_col_map = {
        'turnover_yoy': 'turnover_yoy',
        'turnover_current': 'turnover_current',
        'employees': 'employees',
    }
    sort_col = sort_col_map.get(sort_by, 'turnover_yoy')
    if sort_col in result.columns:
        result = result.sort_values(sort_col, ascending=sort_ascending, na_position='last')
    result = result.reset_index(drop=True)
    if top_n:
        result = result.head(top_n)

    return result
