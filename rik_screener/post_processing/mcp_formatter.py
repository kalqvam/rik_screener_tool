"""
MCP output formatter — converts DataFrames to Claude-readable markdown strings.

All functions receive DataFrames that have already been translated to English
column names (via translate_dataframe_columns / translate_line_names).

Public API:
  format_screening_results(df, title, top_n)
  format_latest_reports(df)
  format_financial_statements(df, company_codes, statement_type)
  format_consistency_check(results, statement_types, target_year, end_year)
  format_validation_result(valid_codes, invalid_codes)
"""

import re
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..utils.translations import get_display_name, translate_line_names

# ---------------------------------------------------------------------------
# Column classification helpers
# ---------------------------------------------------------------------------

# Base names of columns that should be formatted as percentages (× 100, + %)
_PCT_BASES = {
    "ebitda_margin", "roe", "roa", "labour_ratio",
    "revenue_growth", "revenue_cagr",
}

# Base names formatted as plain ratios (2 d.p.) with explicit unit suffixes
_RATIO_BASES = {
    "current_ratio", "cash_ratio", "debt_to_equity", "asset_turnover",
}
_RATIO_SUFFIXES = {
    "current_ratio":  ":1",
    "cash_ratio":     ":1",
    "debt_to_equity": ":1",
    "asset_turnover": "×",
}

# Base names formatted as large currency amounts
_CURRENCY_BASES = {
    "revenue", "operating_profit", "equity", "depreciation",
    "net_profit", "total_assets", "cash", "current_liabilities",
    "long_term_liabilities", "current_assets", "labour_costs",
    "employee_efficiency", "ebitda",
}

_GROWTH_RE = re.compile(r"^(revenue_growth|revenue_cagr)_(\d{4}_to_\d{4})$")
_YEAR_RE = re.compile(r"^(.+?)_(\d{4})$")


def _col_base_year(col: str) -> Tuple[Optional[str], Optional[str]]:
    """Split column name into (base, year_label).

    Handles both single-year ('revenue_2023' → ('revenue', '2023')) and
    multi-year growth patterns ('revenue_growth_2022_to_2023' → ('revenue_growth', '2022_to_2023')).
    """
    m = _GROWTH_RE.match(col)
    if m:
        return m.group(1), m.group(2)
    m = _YEAR_RE.match(col)
    if m:
        return m.group(1), m.group(2)
    return col, None


def _format_value(value, col_base: str) -> str:
    """Format a single cell value based on its column type."""
    if pd.isna(value):
        return "—"
    if isinstance(value, float) and not np.isfinite(value):
        return "—"
    if col_base in _PCT_BASES:
        return f"{value * 100:.1f}%"
    if col_base in _RATIO_BASES:
        return f"{value:.2f}{_RATIO_SUFFIXES.get(col_base, '')}"
    if col_base in _CURRENCY_BASES:
        return f"{value:,.0f} €"
    if col_base == "avg_employees_fte":
        return f"{value:.1f}"
    if col_base == "company_age_years":
        return f"{int(value)}"
    if col_base == "owner_count":
        return str(int(value))
    return str(value)


def _detect_years(df: pd.DataFrame) -> List[str]:
    """Return sorted list of year strings found in column names, newest first."""
    years = set()
    for col in df.columns:
        _, yr = _col_base_year(str(col))
        if yr:
            years.add(yr)
    return sorted(years, reverse=True)


def _priority_columns(df: pd.DataFrame, years: List[str]) -> List[str]:
    """
    Build a display-priority column order:
      1. company_name, company_code
      2. Financial items (revenue, net_profit, total_assets, equity) newest year first
      3. Key ratio columns newest year first
      4. Remaining enrichment columns
    """
    priority_bases = [
        "company_name", "company_code",
        "revenue", "net_profit", "total_assets", "equity",
        "ebitda_margin", "roe", "roa", "debt_to_equity", "current_ratio",
        "operating_profit", "current_assets", "current_liabilities",
        "cash", "depreciation", "labour_costs", "avg_employees_fte",
        "long_term_liabilities", "asset_turnover", "employee_efficiency",
        "cash_ratio", "labour_ratio", "revenue_growth", "revenue_cagr",
        "industry_description", "industry_combined", "industry_code",
        "company_age_years", "owner_count", "top_3_owners", "top_3_percentages",
    ]

    ordered = []
    seen = set()

    def _add(col):
        if col in df.columns and col not in seen:
            ordered.append(col)
            seen.add(col)

    # Identity columns first (no year suffix)
    _add("company_name")
    _add("company_code")

    # Year-suffixed columns in priority order, newest year first
    for base in priority_bases[2:]:
        for yr in years:
            _add(f"{base}_{yr}")
        # Also try without year suffix (enrichment columns)
        _add(base)

    # Append any remaining columns not yet included
    for col in df.columns:
        _add(str(col))

    return ordered


def _make_pipe_table(df: pd.DataFrame, columns: List[str], top_n: int) -> str:
    """Render a subset of df as a markdown pipe table, capped at top_n rows."""
    display_df = df[columns].head(top_n).reset_index(drop=True)

    # Build header
    headers = []
    for col in columns:
        base, yr = _col_base_year(col)
        label = get_display_name(base)
        if yr:
            label = f"{label} ({yr})"
        headers.append(label)

    lines = []
    lines.append("| # | " + " | ".join(headers) + " |")
    lines.append("|---|" + "|".join(["---"] * len(headers)) + "|")

    for i, (_, row) in enumerate(display_df.iterrows(), start=1):
        cells = []
        for col in columns:
            base, _ = _col_base_year(col)
            # Escape pipe chars so they don't break the markdown table structure
            cells.append(_format_value(row[col], base).replace("|", "\\|"))
        lines.append(f"| {i} | " + " | ".join(cells) + " |")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public formatters
# ---------------------------------------------------------------------------

def _format_summary_stats(df: pd.DataFrame, years: List[str]) -> str:
    """Render a summary statistics table (mean/median/min/max) for numeric columns."""
    _excluded = ("company_name", "company_code", "legal_form",
                 "industry_code", "industry_description", "industry_combined",
                 "top_3_owners", "top_3_percentages", "investment_vehicle")
    _all_numeric = [
        c for c in _priority_columns(df, years)
        if c not in _excluded and pd.api.types.is_numeric_dtype(df[c])
    ]
    numeric_cols = [c for c in _all_numeric if df[c].notna().any()]
    _all_nan_count = len(_all_numeric) - len(numeric_cols)

    if not numeric_cols:
        return "*Summary statistics unavailable — no numeric columns with data found.*\n"

    lines = [
        "### Summary Statistics\n",
        "| Metric | Mean | Median | Min | Max |",
        "|---|---|---|---|---|",
        "| *Note* | *Median is more reliable than mean for skewed financial data* | | | |",
    ]
    for col in numeric_cols:
        base, yr = _col_base_year(col)
        label = get_display_name(base)
        if yr:
            label = f"{label} ({yr})"
        series = df[col].dropna()
        if series.empty:
            continue
        mean_v = _format_value(series.mean(), base)
        median_v = _format_value(series.median(), base)
        min_v = _format_value(series.min(), base)
        max_v = _format_value(series.max(), base)
        lines.append(f"| {label} | {mean_v} | {median_v} | {min_v} | {max_v} |")

    if _all_nan_count > 0:
        lines.append(
            f"| *Note* | *{_all_nan_count} column(s) had all values missing "
            f"(formula error or no data) — excluded from this table* | | | |"
        )

    return "\n".join(lines) + "\n"


def format_screening_results(
    df: pd.DataFrame,
    title: str = "Company Screening Results",
    top_n: int = 50,
    filters_applied: Optional[List[str]] = None,
) -> str:
    """
    Format the output of run_company_screening() as a markdown report.

    Expects a DataFrame with English column names (post translate_dataframe_columns).
    Includes a summary statistics block (mean/median/min/max) above the company table
    so aggregate questions ("what's the average X?") are answered without a second call.
    """
    if df.empty:
        return f"## {title}\n\nNo companies matched the screening criteria."

    years = _detect_years(df)
    year_range = f"{years[-1]}–{years[0]}" if len(years) > 1 else years[0] if years else "—"
    total = len(df)

    header = (
        f"## {title}\n\n"
        f"- **Companies returned:** {min(total, top_n)}"
        + (f" of {total} matched" if total > top_n else "")
        + f"\n- **Years covered:** {year_range}\n"
        + "- **Note:** Years reflect fiscal year-end — company fiscal years may span different calendar periods.\n"
    )
    if filters_applied:
        header += "- **Filters applied:** " + "; ".join(filters_applied) + "\n"

    summary = _format_summary_stats(df, years)
    columns = _priority_columns(df, years)
    table = _make_pipe_table(df, columns, top_n)

    footer = ""
    if total > top_n:
        footer = f"\n\n*Showing top {top_n} of {total} companies. Adjust `top_n` to see more.*"

    return header + "\n" + summary + "\n### Companies\n\n" + table + footer


def format_latest_reports(df: pd.DataFrame, company_codes: List[str] = None) -> str:
    """Format the output of get_latest_reports_info() as a markdown table."""
    if df.empty:
        return "## Latest Annual Reports\n\nNo data returned."

    lines = ["## Latest Annual Reports\n"]
    col_map = {
        "company_code":   "Registry Code",
        "company_name":   "Company",
        "latest_year":    "Latest Year",
        "period_start":   "Period Start",
        "period_end":     "Period End",
    }
    cols = [c for c in col_map if c in df.columns]
    headers = [col_map[c] for c in cols]

    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")

    for _, row in df.iterrows():
        cells = [str(row[c]) if pd.notna(row[c]) else "—" for c in cols]
        lines.append("| " + " | ".join(cells) + " |")

    lines.append(f"\n*{len(df)} companies*")
    lines.append("*To fetch full financial statements, call `get_financial_statements` with these registry codes and the desired year.*")

    if company_codes and "company_code" in df.columns:
        returned = set(df["company_code"].astype(str).tolist())
        missing = [c for c in company_codes if c not in returned]
        if missing:
            lines.append(
                f"\n> **Note:** No data returned for {len(missing)} of {len(company_codes)} "
                f"requested companies: {', '.join(f'`{c}`' for c in missing)} "
                f"— API call failed or company not found."
            )

    return "\n".join(lines)


def format_financial_statements(
    df: pd.DataFrame,
    company_codes: List[str],
    statement_type: str = "BS",
) -> str:
    """
    Format the output of get_financial_statements() as a markdown report.

    The DataFrame returned by get_financial_statements() has:
      - index: line_code
      - columns: MultiIndex(company_code, period) PLUS a flat 'line_name' column
                 inserted at position 0

    We separate line_name out first, then iterate over each company's columns.
    """
    if df.empty:
        return f"## Financial Statements ({statement_type})\n\nNo data returned."

    type_labels = {"BS": "Balance Sheet", "IS": "Income Statement", "CF": "Cash Flow Statement"}
    type_label = type_labels.get(statement_type.upper(), statement_type)

    # Separate the flat line_name column from the MultiIndex value columns.
    # get_financial_statements() inserts 'line_name' as a flat column alongside
    # the MultiIndex columns, so df.columns is a mix — extract it first.
    if "line_name" in df.columns:
        line_names = df["line_name"]
        value_df = df.drop(columns=["line_name"])
    else:
        line_names = pd.Series(df.index, index=df.index)
        value_df = df

    # Translate Estonian line names to English
    from ..utils.translations import LINE_NAME_TRANSLATIONS
    line_names = line_names.map(
        lambda x: LINE_NAME_TRANSLATIONS.get(x, x) if isinstance(x, str) else x
    )

    sections = [f"## {type_label}\n"]

    if not isinstance(value_df.columns, pd.MultiIndex):
        # Unexpected structure — render as-is
        sections.append("*Unexpected data structure — raw output:*\n")
        sections.append(value_df.to_markdown() or "")
        return "\n".join(sections)

    companies = value_df.columns.get_level_values("company_code").unique().tolist()

    missing_codes = [c for c in company_codes if c not in companies]
    if missing_codes:
        sections.append(
            f"> **Note:** No data returned for {len(missing_codes)} of {len(company_codes)} "
            f"requested companies: {', '.join(f'`{c}`' for c in missing_codes)} "
            f"— API call failed or no statements found.\n"
        )

    for company in companies:
        sections.append(f"### {company}\n")
        try:
            # df[company] returns a Series when there is only one period column,
            # and a DataFrame when there are multiple. Normalise to DataFrame.
            subset = value_df[company]
            if isinstance(subset, pd.Series):
                subset = subset.to_frame()

            periods = list(subset.columns)

            # Clean period labels: "2024_A1" → "2024", "2024_A2" → "2023"
            # A1 = the requested year, A2 = prior year comparison from same request.
            def _period_label(p: str) -> str:
                parts = str(p).split("_", 1)
                if len(parts) == 2 and parts[0].isdigit():
                    year = int(parts[0])
                    suffix = parts[1].upper()
                    # A2, B2, C2 etc. are the prior-year comparison columns
                    if suffix.endswith("2"):
                        return f"{year - 1} (comparative)"
                    return str(year)
                return str(p)

            period_labels = [_period_label(p) for p in periods]

            header = "| Line Item | " + " | ".join(period_labels) + " |"
            sep = "|---|" + "|".join(["---:"] * len(periods)) + "|"
            rows_out = [header, sep]

            for line_code, line_name in line_names.items():
                if pd.isna(line_name) or str(line_name).strip() == "":
                    continue
                if line_code not in subset.index:
                    continue
                values = []
                for period in periods:
                    # Use .loc with .iloc[0] to handle duplicate line_code index entries
                    # (BS statements reuse codes across asset/liability sections)
                    raw = subset.loc[line_code, period]
                    val = raw.iloc[0] if isinstance(raw, pd.Series) else raw
                    values.append("—" if pd.isna(val) else f"{val:,.0f}")
                rows_out.append(f"| {line_name} | " + " | ".join(values) + " |")

            sections.append("\n".join(rows_out) + "\n")

        except Exception as e:
            sections.append(f"*Error rendering data for {company}: {e}*\n")

    return "\n".join(sections)


def format_consistency_check(
    results: Dict[str, Tuple],
    statement_types: List[str],
    target_year: int,
    end_year: int,
) -> str:
    """Format the output of check_statement_consistency() as a markdown table."""
    if not results:
        return "## Statement Consistency Check\n\nNo results returned."

    lines = [
        f"## Statement Consistency Check ({end_year}–{target_year})\n",
        f"Checks whether reporting structure codes stayed constant across years "
        f"(required for reliable multi-year analysis).\n",
    ]

    st_labels = {"BS": "Balance Sheet", "IS": "Income Statement", "CF": "Cash Flow"}
    st_cols = [st_labels.get(s, s) for s in statement_types]

    header = "| Registry Code | Consistent | Consolidation | " + " | ".join(st_cols) + " |"
    sep = "|---|---|---|" + "|".join(["---"] * len(st_cols)) + "|"
    lines += [header, sep]

    for code, (answer, arrays, cons_status) in results.items():
        icon = "Yes" if answer == "Yes" else ("No" if answer == "No" else "— *data unavailable*")
        code_cells = []
        for i, _ in enumerate(statement_types):
            codes = arrays[i] if i < len(arrays) else []
            non_none = [c for c in codes if c is not None]
            if not non_none:
                code_cells.append("—")
            elif len(set(non_none)) == 1 and None not in codes:
                code_cells.append(f"`{non_none[0]}`")
            else:
                code_cells.append(", ".join(str(c) if c else "—" for c in codes))
        lines.append(
            f"| {code} | {icon} | {cons_status} | " + " | ".join(code_cells) + " |"
        )

    consistent_count = sum(1 for _, (a, _, _) in results.items() if a == "Yes")
    error_count = sum(1 for _, (a, _, _) in results.items() if a == "—")
    checked_count = len(results) - error_count
    summary = f"\n*{consistent_count} of {checked_count} companies have consistent reporting structure across {end_year}–{target_year}.*"
    if error_count:
        summary += f" *{error_count} could not be retrieved (API error).*"
    lines.append(summary)
    lines.append(
        "\n**Key:** Consistent = same reporting structure code across all years checked "
        "(required for reliable multi-year comparison). "
        "Non-consolidated = standalone financials; Consolidated = includes subsidiaries. "
        "Comparative columns (marked 'comparative') are prior-year figures from the same report, not independent filings."
    )
    return "\n".join(lines)


def format_validation_result(valid_codes: List[str], invalid_codes: List[str]) -> str:
    """Format the result of validate_company_codes() as plain markdown."""
    lines = ["## Company Code Validation\n"]

    if valid_codes:
        lines.append(f"**Valid ({len(valid_codes)}):** " + ", ".join(f"`{c}`" for c in valid_codes))
    else:
        lines.append("**Valid:** none")

    if invalid_codes:
        lines.append(
            f"\n**Invalid ({len(invalid_codes)}):** "
            + ", ".join(f"`{c}`" for c in invalid_codes)
            + "\n\n*Estonian registry codes must be 7–8 digits.*"
        )

    return "\n".join(lines)
