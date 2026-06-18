"""
RIK Screener MCP Server

Exposes 5 tools to Claude via the Model Context Protocol:
  - validate_companies          (no credentials needed)
  - get_latest_reports          (requires RIK API credentials)
  - get_financial_statements    (requires RIK API credentials)
  - check_statement_consistency (requires RIK API credentials)
  - screen_companies            (requires local RIK CSV data files)

Credentials are resolved in this order (first match wins):
  1. Per-call username/password parameters
  2. credentials.txt in the project root (KEY=VALUE format)
  3. RIK_USERNAME / RIK_PASSWORD environment variables

Data path for the CSV pipeline:
  RIK_SCREENER_PATH environment variable, or hardcoded in .mcp.json env block.

Start the server:
  python -m rik_screener.mcp_server
  # or, after pip install:
  rik-screener-mcp
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context, FastMCP

from .api_workspace.main_orchestrator import (
    get_latest_reports_info,
    get_financial_statements,
    check_statement_consistency,
    get_company_representation,
    get_company_beneficial_owners,
)
from .api_workspace.utils import validate_company_codes
from .post_processing.mcp_formatter import (
    format_consistency_check,
    format_financial_statements,
    format_latest_reports,
    format_screening_results,
    format_validation_result,
)
from .utils.translations import COLUMN_TRANSLATIONS, translate_dataframe_columns
from .workflow.orchestrator import run_company_screening

mcp = FastMCP(
    "rik-screener",
    instructions=(
        "Tools for screening and analysing Estonian companies from the RIK business register.\n\n"
        "IMPORTANT RULES:\n"
        "1. Always call validate_companies first when working with registry codes provided by the user.\n"
        "2. screen_companies uses local CSV data and is self-contained — do NOT follow it with "
        "API calls (get_latest_reports, get_financial_statements, check_statement_consistency) "
        "unless the user explicitly asks for real-time API data in addition to the screening results.\n"
        "3. The API tools are for fetching live statement data for specific known companies. "
        "Use them only when the user's request clearly requires data not available in the CSV pipeline.\n"
        "4. When the user provides company names (not codes) and wants CSV data: call find_company() "
        "ONCE with all names as a list (e.g. find_company(names=['Pipedrive','Bolt','Wise'])), "
        "confirm ambiguous matches with the user, then call screen_companies() with company_codes=[...]. "
        "Never call find_company() in a loop — always batch all names into one call.\n"
        "5. MISSING DATA: Both CSV and API data frequently contain missing values — a company may not "
        "have filed, a field may not apply, or the registry simply has no record. Always report missing "
        "values explicitly as missing (show '—', 'N/A', or 'not reported'). Never fill, interpolate, "
        "estimate, or assume a value. Never exclude a company from results silently because one of its "
        "fields is missing — show the company with the missing field clearly marked.\n"
        "6. OWNERSHIP & REPRESENTATION TOOLS: get_representation_rights returns board members and signing "
        "rights. get_beneficial_owners_tool returns ultimate beneficial owners (UBO register, available "
        "since May 2022). Both require RIK API credentials. Use find_company first if only a name is given."
    ),
)


# ---------------------------------------------------------------------------
# Credentials file loader
# ---------------------------------------------------------------------------

# Look for credentials.txt in the project root (two levels up from this file:
# rik_screener/mcp_server.py → rik_screener/ → project root)
_CREDENTIALS_FILE = Path(__file__).parent.parent / "credentials.txt"


def _load_credentials_file() -> Dict[str, str]:
    """Read KEY=VALUE pairs from credentials.txt, ignoring blank lines and comments."""
    result: Dict[str, str] = {}
    if not _CREDENTIALS_FILE.exists():
        return result
    try:
        for line in _CREDENTIALS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip()
    except OSError:
        pass
    return result


# ---------------------------------------------------------------------------
# Credential helper
# ---------------------------------------------------------------------------

def _get_credentials(username: Optional[str], password: Optional[str]):
    """
    Resolve API credentials in priority order:
      1. Explicit per-call parameters
      2. credentials.txt in the project root
      3. RIK_USERNAME / RIK_PASSWORD environment variables
    """
    file_creds = _load_credentials_file()

    u = username or file_creds.get("RIK_USERNAME") or os.environ.get("RIK_USERNAME", "")
    p = password or file_creds.get("RIK_PASSWORD") or os.environ.get("RIK_PASSWORD", "")

    if not u or not p:
        raise ValueError(
            "RIK API credentials not found. "
            "Add RIK_USERNAME and RIK_PASSWORD to credentials.txt in the project folder, "
            "or set them as environment variables."
        )
    return u, p


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

def _derive_skip_steps(
    export_columns: Optional[List[str]],
    industry_keyword: Optional[str],
    industry_codes: Optional[List[str]],
    ownership_filters: Optional[Dict],
    geo_filters: Optional[Dict],
    explicit_skip_steps: Optional[List[str]],
) -> List[str]:
    """
    Infer which enrichment steps can be skipped based on what was requested.

    If the user supplied explicit skip_steps, those take precedence.
    If export_columns is None (show everything), nothing is skipped.
    Otherwise, skip any step whose output columns are not needed.
    """
    if explicit_skip_steps is not None:
        return explicit_skip_steps
    if export_columns is None:
        return []

    col_set = set(export_columns)
    skip: List[str] = []

    # Ownership: skip if no ownership columns requested and no ownership filters
    ownership_cols = {"owner_count", "top_3_owners", "top_3_percentages"}
    if not ownership_cols & col_set and not ownership_filters:
        skip.append("ownership")

    # Age: skip if company_age_years not requested
    if "company_age_years" not in col_set:
        skip.append("age")

    # Industry + EMTAK: skip if no industry columns requested and no industry filters
    industry_needed = bool(industry_keyword or industry_codes)
    if not industry_needed:
        industry_prefixes = ("industry_code", "industry_description", "industry_combined")
        industry_needed = any(c.startswith(industry_prefixes) for c in col_set)
    if not industry_needed:
        skip.extend(["industry", "emtak"])

    # Geography: skip if no geo columns requested and no geo filters
    geo_needed = bool(geo_filters)
    if not geo_needed:
        geo_prefixes = ("geo_domestic_share", "geo_export_share", "geo_top_export_market", "geo_revenue_countries")
        geo_needed = any(c.startswith(geo_prefixes) for c in col_set)
    if not geo_needed:
        skip.append("geography")

    return skip


def _reverse_column_map(years: List[int]) -> Dict[str, str]:
    """Build a per-year English→Estonian reverse map for raw financial columns."""
    reverse: Dict[str, str] = {}
    for est, eng in COLUMN_TRANSLATIONS.items():
        for y in years:
            reverse[f"{eng}_{y}"] = f"{est}_{y}"
    return reverse


def _translate_filters_to_estonian(
    filters: List[Dict[str, Any]],
    years: List[int],
) -> List[Dict[str, Any]]:
    """
    Convert English raw financial column names in financial_filters to Estonian.

    Computed/ratio columns (e.g. ebitda_margin_2023, revenue_growth_2021_to_2023)
    are not in the reverse map and pass through unchanged. Estonian names already
    in the filters also pass through unchanged.
    """
    reverse = _reverse_column_map(years)
    result = []
    for f in filters:
        col = f.get("column", "")
        translated = reverse.get(col, col)
        result.append({**f, "column": translated})
    return result


def _translate_formula_expressions(
    custom_formulas: Dict[str, str],
    years: List[int],
) -> Dict[str, str]:
    """
    Translate English column names inside custom formula expression strings to Estonian.

    Non-destructive: names not found in the reverse map (already Estonian, or
    computed ratio names like ebitda_margin_2023) pass through unchanged.
    Works for both Estonian and English input — no flag needed.
    """
    reverse = _reverse_column_map(years)
    translated = {}
    for name, expr in custom_formulas.items():
        new_expr = expr
        for eng, est in reverse.items():
            # Replace quoted English column names, e.g. "revenue_2023" → "Müügitulu_2023"
            new_expr = new_expr.replace(f'"{eng}"', f'"{est}"')
        translated[name] = new_expr
    return translated


def _apply_industry_filters(
    df: "Any",  # pd.DataFrame
    years: List[int],
    industry_keyword: Optional[str],
    industry_codes: Optional[List[str]],
) -> "Any":
    """
    Filter translated DataFrame by industry description (substring) and/or
    EMTAK code (prefix). Applied post-translation. Returns a filtered copy.

    Uses the most recent year's industry columns. Silently skips if the
    required columns are absent (e.g. when skip_steps excluded industry/emtak).
    """
    import pandas as pd

    if df.empty or (not industry_keyword and not industry_codes):
        return df

    ref_year = years[0]
    mask = pd.Series(True, index=df.index)

    if industry_keyword:
        for col in (f"industry_description_{ref_year}", f"industry_combined_{ref_year}"):
            if col in df.columns:
                mask &= df[col].astype(str).str.contains(industry_keyword, case=False, na=False)
                break

    if industry_codes:
        col = f"industry_code_{ref_year}"
        if col in df.columns:
            prefixes = tuple(industry_codes)
            mask &= df[col].astype(str).str.startswith(prefixes)

    return df[mask].copy()


# ---------------------------------------------------------------------------
# Tool 1 — validate_companies
# ---------------------------------------------------------------------------

@mcp.tool()
def validate_companies(company_codes: List[str]) -> str:
    """
    Validate a list of Estonian company registry codes.

    Estonian registry codes are 7–8 digit numbers (e.g. "12417834").
    Always run this before making API calls when codes come from user input.

    Args:
        company_codes: List of registry code strings to validate.

    Returns:
        Markdown summary listing valid and invalid codes with counts.
    """
    valid = validate_company_codes(company_codes)
    invalid = [c for c in company_codes if c not in valid]
    return format_validation_result(valid, invalid)


# ---------------------------------------------------------------------------
# Tool 2 — get_latest_reports
# ---------------------------------------------------------------------------

@mcp.tool()
def get_latest_reports(
    company_codes: List[str],
    include_names: bool = True,
    rate_limit: int = 20,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> str:
    """
    Fetch the most recent annual report metadata for a list of companies.

    Requires RIK API credentials (set RIK_USERNAME / RIK_PASSWORD env vars).

    Args:
        company_codes: List of 7–8 digit registry code strings.
        include_names: Whether to fetch the company name alongside report info.
                       Adds one extra API call per company. Default True.
        rate_limit:    Max API requests per minute. Default 20. Do not exceed 30.
        username:      RIK API username (overrides RIK_USERNAME env var).
        password:      RIK API password (overrides RIK_PASSWORD env var).

    Returns:
        Markdown table with latest report year and period for each company.
    """
    try:
        u, p = _get_credentials(username, password)
        df = get_latest_reports_info(
            company_codes=company_codes,
            username=u,
            password=p,
            include_names=include_names,
            rate_limit=rate_limit,
        )
        return format_latest_reports(df, company_codes)
    except Exception as e:
        return f"## Error\n\n{e}"


# ---------------------------------------------------------------------------
# Tool 3 — get_financial_statements
# ---------------------------------------------------------------------------

@mcp.tool()
def get_financial_statements_tool(
    company_codes: List[str],
    statement_type: str = "BS",
    starting_year: Optional[int] = None,
    num_requests: int = 1,
    rate_limit: int = 20,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> str:
    """
    Fetch financial statements from the RIK SOAP API.

    Requires RIK API credentials (set RIK_USERNAME / RIK_PASSWORD env vars).

    Args:
        company_codes:  List of 7–8 digit registry code strings.
        statement_type: "BS" (Balance Sheet), "IS" (Income Statement),
                        or "CF" (Cash Flow Statement). Default "BS".
        starting_year:  Most recent year to fetch. Defaults to current year minus 1
                        (the most recent complete fiscal year).
        num_requests:   How many years back to retrieve. Default 1.
                        E.g. starting_year=2023, num_requests=3 → 2023, 2021, 2019
                        (step of 2 years is the default; each SOAP response covers
                        a 2-year comparison period).
        rate_limit:     Max API requests per minute. Default 20.
        username:       RIK API username (overrides RIK_USERNAME env var).
        password:       RIK API password (overrides RIK_PASSWORD env var).

    Returns:
        Markdown report with financial statement lines as rows and years as columns,
        grouped by company. Line names are translated from Estonian to English.
    """
    try:
        u, p = _get_credentials(username, password)
        df = get_financial_statements(
            company_codes=company_codes,
            username=u,
            password=p,
            statement_type=statement_type,
            starting_year=starting_year,
            num_requests=num_requests,
            rate_limit=rate_limit,
        )
        return format_financial_statements(df, company_codes, statement_type)
    except Exception as e:
        return f"## Error\n\n{e}"


# ---------------------------------------------------------------------------
# Tool 4 — check_statement_consistency
# ---------------------------------------------------------------------------

@mcp.tool()
def check_statement_consistency_tool(
    company_codes: List[str],
    target_year: int,
    end_year: int,
    statement_types: Optional[List[str]] = None,
    rate_limit: int = 20,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> str:
    """
    Check whether a company's statement structure codes stayed constant across years.

    Run this before get_financial_statements when doing multi-year analysis —
    a code change means the line-item structure changed and year-over-year
    comparisons may not be valid.

    Requires RIK API credentials (set RIK_USERNAME / RIK_PASSWORD env vars).

    Args:
        company_codes:    List of 7–8 digit registry code strings.
        target_year:      Most recent year (e.g. 2023). Must be >= end_year.
        end_year:         Oldest year to check (e.g. 2020).
        statement_types:  List of types to check. Default ["BS", "IS", "CF"].
        rate_limit:       Max API requests per minute. Default 20.
        username:         RIK API username (overrides RIK_USERNAME env var).
        password:         RIK API password (overrides RIK_PASSWORD env var).

    Returns:
        Markdown table showing whether each company's structure is consistent,
        the consolidation status, and the statement codes per year.
    """
    if statement_types is None:
        statement_types = ["BS", "IS", "CF"]
    try:
        u, p = _get_credentials(username, password)
        results = check_statement_consistency(
            company_codes=company_codes,
            username=u,
            password=p,
            target_year=target_year,
            end_year=end_year,
            statement_types=statement_types,
            rate_limit=rate_limit,
        )
        return format_consistency_check(results, statement_types, target_year, end_year)
    except Exception as e:
        return f"## Error\n\n{e}"


# ---------------------------------------------------------------------------
# Tool 5 — get_company_representation
# ---------------------------------------------------------------------------

@mcp.tool()
def get_representation_rights(
    company_codes: List[str],
    username: Optional[str] = None,
    password: Optional[str] = None,
    rate_limit: int = 20,
) -> str:
    """
    Fetch rights of representation for all persons related to one or more companies.

    Returns each person's name, role, personal identification code, country, and whether
    they have exclusive right of representation. Also shows any exceptions to representation
    rights noted in the registry.

    Useful for: "Who can sign on behalf of company X?", "Who are the board members?",
    "Does this person have exclusive representation rights?"

    Args:
        company_codes: List of 7–8 digit Estonian registry codes.
        username:      RIK API username (falls back to credentials.txt / env).
        password:      RIK API password (falls back to credentials.txt / env).
        rate_limit:    Max API requests per minute (default 20).
    """
    try:
        user, pwd = _get_credentials(username, password)
        results = get_company_representation(
            company_codes=company_codes,
            username=user,
            password=pwd,
            rate_limit=rate_limit,
        )
        return _format_representation_rights(results, company_codes)
    except Exception as e:
        return f"## Error\n\n{e}"


def _format_representation_rights(results: list, requested_codes: List[str] = None) -> str:
    if not results:
        return "## Rights of Representation\n\nNo data returned."

    sections = ["## Rights of Representation\n"]

    for company in results:
        name = company.get("company_name") or company["company_code"]
        code = company["company_code"]
        status = company.get("status") or "—"
        legal = company.get("legal_form") or "—"
        exceptions = company.get("exceptions")

        sections.append(f"### {name} (`{code}`)\n")
        sections.append(f"**Status:** {status} | **Legal form:** {legal}\n")

        persons = company.get("persons", [])
        if not persons:
            sections.append("*No persons on record.*\n")
        else:
            lines = [
                "| Name | Role | Exclusive | Country | Personal Code |",
                "|---|---|---|---|---|",
            ]
            for p in persons:
                first = p.get("first_name") or ""
                last = p.get("last_name") or ""
                full_name = f"{first} {last}".strip() or "—"
                role = p.get("role") or p.get("role_code") or "—"
                exclusive = p.get("exclusive_representation") or "—"
                country = p.get("country") or p.get("country_code") or "—"
                personal_code = p.get("personal_code") or "—"
                lines.append(f"| {full_name} | {role} | {exclusive} | {country} | {personal_code} |")
            sections.append("\n".join(lines) + "\n")

        if exceptions:
            sections.append(f"**Representation exceptions:** {exceptions}\n")

    if requested_codes:
        returned = {r["company_code"] for r in results}
        missing = [c for c in requested_codes if c not in returned]
        if missing:
            sections.append(
                f"\n> **Note:** No data returned for {len(missing)} of {len(requested_codes)} "
                f"requested companies: {', '.join(f'`{c}`' for c in missing)} "
                f"— API call failed or company not found."
            )

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Tool 6 — get_beneficial_owners
# ---------------------------------------------------------------------------

@mcp.tool()
def get_beneficial_owners_tool(
    company_codes: List[str],
    active_only: bool = True,
    username: Optional[str] = None,
    password: Optional[str] = None,
    rate_limit: int = 20,
) -> str:
    """
    Fetch beneficial owners (tegelikud kasusaajad) for one or more companies.

    A beneficial owner is a natural person who ultimately owns or controls a company
    (typically >25% shareholding or board control). Available as of 25.05.2022.

    Returns each owner's name, personal code, country of residence, method of control,
    and start date. Also shows total/hidden owner counts and discrepancy note status.

    Useful for: "Who ultimately owns company X?", "What is the ownership structure?",
    "Are there hidden beneficial owners?"

    Args:
        company_codes: List of 7–8 digit Estonian registry codes.
        active_only:   If True (default), return only currently valid records.
                       Set False to include historical beneficial owners.
        username:      RIK API username (falls back to credentials.txt / env).
        password:      RIK API password (falls back to credentials.txt / env).
        rate_limit:    Max API requests per minute (default 20).
    """
    try:
        user, pwd = _get_credentials(username, password)
        results = get_company_beneficial_owners(
            company_codes=company_codes,
            username=user,
            password=pwd,
            active_only=active_only,
            rate_limit=rate_limit,
        )
        return _format_beneficial_owners(results, company_codes)
    except Exception as e:
        return f"## Error\n\n{e}"


def _format_beneficial_owners(results: list, requested_codes: List[str] = None) -> str:
    if not results:
        return "## Beneficial Owners\n\nNo data returned."

    sections = ["## Beneficial Owners\n"]

    for company in results:
        code = company["company_code"]
        obliged = company.get("obliged_to_report")
        total = company.get("total_owners")
        hidden = company.get("hidden_owners")
        discrepancy_absence = company.get("discrepancy_on_absence")

        obliged_str = "Yes" if obliged else "No" if obliged is not None else "—"
        total_str = str(total) if total is not None else "—"
        hidden_str = str(hidden) if hidden is not None else "—"

        sections.append(f"### `{code}`\n")
        meta = f"**Obliged to report:** {obliged_str} | **Total owners:** {total_str}"
        if hidden and hidden > 0:
            meta += f" ({hidden_str} hidden)"
        if discrepancy_absence:
            meta += " | ⚠ Discrepancy note on absence filed"
        sections.append(meta + "\n")

        owners = company.get("owners", [])
        if not owners:
            if obliged is False:
                sections.append("*This company is not required to report beneficial owners.*\n")
            else:
                sections.append("*No beneficial owner records returned.*\n")
            continue

        lines = [
            "| Name | Personal Code | Country | Control Method | From | Discrepancy |",
            "|---|---|---|---|---|---|",
        ]
        for o in owners:
            first = o.get("first_name") or ""
            last = o.get("last_name") or ""
            full_name = f"{first} {last}".strip() or "—"
            personal_code = o.get("personal_code") or o.get("foreign_id") or "—"
            country = o.get("country") or o.get("country_code") or "—"
            control = o.get("control_method") or o.get("control_code") or "—"
            start = o.get("start_date") or "—"
            discrepancy_raw = o.get("discrepancy_note")
            discrepancy = "Yes" if discrepancy_raw is True else ("No" if discrepancy_raw is False else "—")
            lines.append(f"| {full_name} | {personal_code} | {country} | {control} | {start} | {discrepancy} |")

        sections.append("\n".join(lines) + "\n")

    if requested_codes:
        returned = {r["company_code"] for r in results}
        missing = [c for c in requested_codes if c not in returned]
        if missing:
            sections.append(
                f"\n> **Note:** No data returned for {len(missing)} of {len(requested_codes)} "
                f"requested companies: {', '.join(f'`{c}`' for c in missing)} "
                f"— API call failed or company not found."
            )

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Tool 7 — get_annual_report_toc
# ---------------------------------------------------------------------------

@mcp.tool()
def get_annual_report_toc(
    company_code: str,
    year: int,
) -> str:
    """Fetch the table of contents (Sisukord) from a company's annual report PDF.

    Always call this BEFORE get_annual_report_section. It downloads and caches
    the PDF locally, then returns the full table of contents with page ranges.

    Section names are in Estonian. Common ones:
    - Tegevusaruanne = management report
    - Konsolideeritud/Konsolideerimata bilanss = balance sheet
    - Konsolideeritud/Konsolideerimata kasumiaruanne = income statement
    - Raamatupidamise aastaaruande lisad = notes to financial statements
    - Lisa N <name> = note N (e.g. Lisa 11 Laenukohustised = note on loans)
    - Vandeaudiitori aruanne = auditor's opinion

    Args:
        company_code: Estonian company registry code (e.g. "10523510")
        year:         Report year (e.g. 2024)

    Returns:
        Markdown table of contents with section names and page ranges.
        Use page numbers directly in get_annual_report_section calls.
    """
    from .api_workspace.report_scraper import discover_file_id, download_pdf, parse_toc_from_pdf, load_toc

    # Use cached ToC if available
    cached = load_toc(company_code, year)
    if cached:
        return _format_toc(company_code, year, cached, from_cache=True)

    file_id = discover_file_id(company_code, year)
    if not file_id:
        return f"## Error\n\nNo annual report found for company `{company_code}` year `{year}`. Check that the company code and year are correct."

    try:
        pdf_path = download_pdf(company_code, year, file_id)
    except RuntimeError as e:
        return f"## Error\n\n{e}"

    try:
        entries = parse_toc_from_pdf(pdf_path, company_code, year)
    except RuntimeError as e:
        return f"## Error\n\nPDF downloaded but table of contents could not be parsed: {e}"

    return _format_toc(company_code, year, entries, from_cache=False)


def _format_toc(company_code: str, year: int, entries: list, from_cache: bool) -> str:
    lines = [
        f"## Annual Report Table of Contents — {company_code} ({year})\n",
        f"{'*(from cache)*' if from_cache else '*(freshly downloaded)*'}\n",
        "| Section | Pages |",
        "|---|---|",
    ]
    for e in entries:
        pages = f"{e['start_page']}–{e['end_page']}" if e['start_page'] != e['end_page'] else str(e['start_page'])
        lines.append(f"| {e['name']} | {pages} |")
    lines.append(
        f"\nUse `get_annual_report_section(company_code=\"{company_code}\", year={year}, "
        f"start_page=N, end_page=M)` to retrieve any section."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 8 — get_annual_report_section
# ---------------------------------------------------------------------------

@mcp.tool()
def get_annual_report_section(
    company_code: str,
    year: int,
    start_page: int,
    end_page: int,
) -> str:
    """Extract text from a specific page range of a cached annual report PDF.

    Requires get_annual_report_toc to have been called first for this
    company + year — that call downloads and caches the PDF.

    Page numbers come directly from the table of contents returned by
    get_annual_report_toc. They are 1-indexed (matching the printed page numbers).
    For a single-page section pass the same value for start_page and end_page.

    Args:
        company_code: Estonian company registry code (e.g. "10523510")
        year:         Report year (e.g. 2024)
        start_page:   First page to extract (1-indexed, from the ToC)
        end_page:     Last page to extract (1-indexed, from the ToC)

    Returns:
        Extracted text from the specified pages.
    """
    from .api_workspace.report_scraper import extract_text_from_pages

    try:
        text = extract_text_from_pages(company_code, year, start_page, end_page)
    except RuntimeError as e:
        return f"## Error\n\n{e}"

    if not text.strip():
        return f"## No text found\n\nPages {start_page}–{end_page} of the report appear to contain no extractable text (may be image-based)."

    page_label = f"page {start_page}" if start_page == end_page else f"pages {start_page}–{end_page}"
    return f"## Annual Report Extract — {company_code} ({year}), {page_label}\n\n{text}"


# ---------------------------------------------------------------------------
# Tool 9 — get_screener_help
# ---------------------------------------------------------------------------

@mcp.tool()
def get_screener_help() -> str:
    """
    Return a concise reference card for the RIK screener MCP tools.

    Call this when a user prompt is ambiguous and it is unclear which tools,
    column names, or formula names to use. No arguments required.
    """
    return """\
## RIK Screener — Tool Reference Card

### Available Tools

| Tool | When to use |
|---|---|
| `validate_companies` | Always call first when the user supplies registry codes |
| `find_company` | Resolve a company name to a registry code — call before screen_companies or API tools when only a name is given |
| `get_latest_reports` | Fetch latest annual report year/period for specific companies (needs API creds) |
| `get_financial_statements` | Fetch full BS/IS/CF statement for specific companies (needs API creds) |
| `check_statement_consistency` | Verify structure is stable before multi-year comparison (needs API creds) |
| `get_representation_rights` | Who can sign on behalf of the company — board members and their signing rights (needs API creds) |
| `get_beneficial_owners_tool` | Who ultimately owns the company — UBO register data (needs API creds, available since May 2022) |
| `lookup_industry_codes` | Resolve a vague sector name to EMTAK codes — call before screen_companies |
| `lookup_geographic_regions` | List valid country names for export_countries filter — call before using geography filters |
| `list_financial_columns` | List available column names for a given year set — call before writing custom formulas |
| `screen_companies` | Run the full screening pipeline on local CSV data |
| `get_screener_help` | This reference card — call when uncertain |

---

### Confirmation Workflow

**Vague sector → confirm EMTAK codes first:**
1. `lookup_industry_codes("tootmine")` → get code list
2. Confirm with user which codes to use
3. `screen_companies(..., industry_codes=["10", "11"])`

**Vague formula → confirm column mapping first:**
1. `list_financial_columns(years=[2023])` → get column names
2. Propose mapping to user: *"opex = operating_profit_2023, net assets = total_assets_2023 − current_liabilities_2023"*
3. `screen_companies(..., custom_formulas={"opex_net_assets": '"operating_profit_2023" / ("total_assets_2023" - "current_liabilities_2023")'})`

---

### Raw Financial Columns (available for filters and formulas)

Both Estonian and English names work in `financial_filters` and `custom_formulas`.

| English name | Estonian name |
|---|---|
| revenue | Müügitulu |
| operating_profit | Ärikasum (kahjum) |
| net_profit | Aruandeaasta kasum (kahjum) |
| equity | Omakapital |
| total_assets | Varad |
| current_assets | Käibevarad |
| current_liabilities | Lühiajalised kohustised |
| long_term_liabilities | Pikaajalised kohustised |
| cash | Raha |
| depreciation | Põhivarade kulum ja väärtuse langus |
| labour_costs | Tööjõukulud |
| avg_employees_fte | Töötajate keskmine arv taandatuna täistööajale |

All column names take a year suffix: `revenue_2023`, `Müügitulu_2023`, etc.

---

### Standard Formula Output Columns

| Formula name | Output column pattern | Note |
|---|---|---|
| ebitda | `ebitda_{year}` | Absolute EBITDA in EUR |
| ebitda_margin | `ebitda_margin_{year}` | Decimal (0.15 = 15%) |
| roe | `roe_{year}` | Decimal; '—' if equity ≤ 0 |
| roa | `roa_{year}` | Decimal |
| asset_turnover | `asset_turnover_{year}` | Times (×) |
| employee_efficiency | `employee_efficiency_{year}` | EUR/employee; '—' if 0 employees |
| cash_ratio | `cash_ratio_{year}` | Ratio (:1) |
| current_ratio | `current_ratio_{year}` | Ratio (:1); '—' if 0 liabilities |
| debt_to_equity | `debt_to_equity_{year}` | Ratio (:1); '—' if equity ≤ 0 |
| labour_ratio | `labour_ratio_{year}` | Decimal |
| revenue_growth | `revenue_growth_{from}_to_{to}` | Decimal YoY; '—' if base = 0 |
| revenue_cagr | `revenue_cagr_{start}_to_{end}` | Decimal CAGR; '—' if base = 0 |

---

### Common Query Patterns

**Top companies by EBITDA margin in a sector:**
```
screen_companies(
  years=[2023, 2022, 2021],
  industry_codes=["62"],
  sort_column="ebitda_margin_2023",
  top_n=20
)
```

**Revenue growth filter + custom formula:**
```
screen_companies(
  years=[2023, 2022],
  min_revenue_growth=0.10,
  custom_formulas={"profit_per_employee_2023": '"net_profit_2023" / "avg_employees_fte_2023"'}
)
```

**Flexible numeric filter on any column:**
```
screen_companies(
  years=[2023],
  financial_filters=[
    {"column": "debt_to_equity_2023", "max": 1.5},
    {"column": "revenue_2023", "min": 500000}
  ]
)
```

**Export-focused companies (geography screening):**
```
# Step 1: confirm country spelling
lookup_geographic_regions(keyword="Saksa")

# Step 2: screen for heavy exporters with German revenue
screen_companies(
  years=[2023, 2022],
  min_export_share=0.5,
  export_countries=["Saksamaa"],
  export_columns=["company_name", "geo_export_share_2023",
                  "geo_top_export_market_2023", "revenue_2023"]
)
```
"""


# ---------------------------------------------------------------------------
# Tool 6 — lookup_industry_codes
# ---------------------------------------------------------------------------

@mcp.tool()
def lookup_industry_codes(keyword: str) -> str:
    """
    Search the EMTAK industry code list by keyword.

    Call this before screen_companies when the user mentions a vague sector name
    (e.g. "tootmine", "IT", "food", "ehitus"). Returns matching codes and
    descriptions so you can confirm the right codes with the user.

    Args:
        keyword: Case-insensitive substring to match against industry descriptions.
                 Can be Estonian or English (e.g. "tootmine", "manufacturing", "62").

    Returns:
        Markdown table of matching EMTAK codes and descriptions.
    """
    import pandas as pd

    data_path = os.environ.get("RIK_SCREENER_PATH", "")
    if not data_path:
        return "## Error\n\nRIK_SCREENER_PATH not set — cannot locate emtak_2025.csv."

    emtak_file = Path(data_path) / "emtak_2025.csv"
    if not emtak_file.exists():
        return f"## Error\n\nemtak_2025.csv not found at {emtak_file}."

    try:
        df = pd.read_csv(emtak_file, header=None, names=["code", "description"],
                         dtype=str, encoding="utf-8-sig")
        df["code"] = df["code"].str.strip()
        df["description"] = df["description"].str.strip()

        mask = (
            df["description"].str.contains(keyword, case=False, na=False) |
            df["code"].str.contains(keyword, case=False, na=False)
        )
        matches = df[mask]

        if matches.empty:
            return (
                f"## EMTAK Search: '{keyword}'\n\n"
                f"No matches found. Try a broader term or check the spelling.\n\n"
                f"*Tip: use `get_screener_help()` to see common EMTAK code ranges.*"
            )

        lines = [
            f"## EMTAK Search: '{keyword}'\n",
            f"Found {len(matches)} matching codes. Confirm which to use before calling `screen_companies`.\n",
            "| EMTAK Code | Description |",
            "|---|---|",
        ]
        for _, row in matches.iterrows():
            lines.append(f"| {row['code']} | {row['description']} |")

        lines.append(
            "\n*Pass chosen codes to `screen_companies` as `industry_codes=[\"10\", \"11\"]`. "
            "Prefix matching is used — `\"10\"` matches all sub-codes starting with 10.*"
        )
        return "\n".join(lines)

    except Exception as e:
        return f"## Error\n\n{e}"


# ---------------------------------------------------------------------------
# Tool 7 — lookup_geographic_regions
# ---------------------------------------------------------------------------

@mcp.tool()
def lookup_geographic_regions(keyword: Optional[str] = None) -> str:
    """
    List all unique country and region names present in the geographic revenue dataset.

    Call this before using export_countries in screen_companies to confirm the exact
    spelling of country names. Estonian spellings are used throughout
    (e.g. "Saksamaa" for Germany, "Soome" for Finland, "USA" for the United States).

    Two aggregate region categories also appear in the data:
      - "Müük Euroopa Liidu riikidele, muud"  (other EU sales, not broken down by country)
      - "Müük väljaspool Euroopa Liidu riike, muud"  (other non-EU sales)
    These count toward export share and can be used as export_countries filter values.

    Args:
        keyword: Optional. If provided, filter the list to names containing this
                 substring (case-insensitive). Omit to return all names.

    Returns:
        Markdown table of country/region names valid for use as export_countries values.
    """
    import pandas as pd
    import glob as _glob

    data_path = os.environ.get("RIK_SCREENER_PATH", "")
    if not data_path:
        return "## Error\n\nRIK_SCREENER_PATH not set."

    pattern = str(Path(data_path) / "3.myygitulu_geograafiline_kuni_*.csv")
    matches = sorted(_glob.glob(pattern))
    if not matches:
        return (
            "## Error\n\nGeographic revenue CSV not found. "
            "Expected pattern: 3.myygitulu_geograafiline_kuni_*.csv"
        )

    geo_path = matches[-1]
    try:
        df = pd.read_csv(
            geo_path, sep=";",
            usecols=["Riigi nimetus"],
            dtype=str,
            encoding="utf-8-sig",
        )
        df["Riigi nimetus"] = df["Riigi nimetus"].str.strip().str.strip("'\"")
        names = sorted(df["Riigi nimetus"].dropna().unique())

        if keyword:
            names = [n for n in names if keyword.lower() in n.lower()]

        if not names:
            return (
                f"## Geographic Regions\n\n"
                f"No regions found{f' matching \"{keyword}\"' if keyword else ''}."
            )

        header = f"## Geographic Regions{f' matching \"{keyword}\"' if keyword else ''}\n\n"
        header += f"Found {len(names)} region(s). Use exact spelling in `export_countries`.\n\n"
        lines = ["| Country / Region |", "|---|"]
        for name in names:
            lines.append(f"| {name} |")

        lines.append(
            "\n*Pass exact names to `screen_companies` as "
            "`export_countries=[\"Saksamaa\", \"USA\"]`*"
        )
        return header + "\n".join(lines)

    except Exception as e:
        return f"## Error\n\n{e}"


# ---------------------------------------------------------------------------
# Tool 8 — find_company
# ---------------------------------------------------------------------------

@mcp.tool()
def find_company(names: List[str], top_n: int = 3) -> str:
    """
    Search for one or more companies by name and return their Estonian business registry codes.

    Always pass ALL company names in a single call — the CSV is loaded once and all
    names are matched against it, so one call for 20 names is as fast as one call for 1.

    Uses fuzzy matching so partial or approximate names work (e.g. "Pipedrive", "Bolt Food",
    "Transferwise"). Returns top_n matches per name ranked by similarity — confirm ambiguous
    matches with the user before calling screen_companies or API tools.

    Args:
        names: List of company names or fragments to search for (case-insensitive).
        top_n: Maximum matches to return per name (default 3).
    """
    import difflib
    import glob as _glob
    import pandas as pd

    data_path = os.environ.get("RIK_SCREENER_PATH", "")
    if not data_path:
        return "## Error\n\nRIK_SCREENER_PATH not set — cannot locate legal data CSV."

    pattern = str(Path(data_path) / "ettevotja_rekvisiidid__lihtandmed*.csv")
    file_matches = sorted(_glob.glob(pattern))
    if not file_matches:
        fallback = Path(data_path) / "ettevotja_rekvisiidid__lihtandmed.csv"
        if not fallback.exists():
            return f"## Error\n\nLegal data CSV not found at {data_path}."
        legal_path = str(fallback)
    else:
        legal_path = file_matches[-1]

    try:
        legal_df = pd.read_csv(
            legal_path, sep=";",
            usecols=["ariregistri_kood", "nimi"],
            dtype=str,
        )
        legal_df = legal_df.dropna(subset=["nimi", "ariregistri_kood"]).reset_index(drop=True)
        legal_df["nimi_lower"] = legal_df["nimi"].str.lower()

        # Normalise legal form variants to a canonical token so fuzzy matching
        # is not penalised for "AS" vs "aktsiaselts" or "OÜ" vs "osaühing".
        _LEGAL_FORM_RE = re.compile(
            r"\b(aktsiaselts|as|osaühing|oü|tulundusühistu|tü|usaldusühing|uü"
            r"|sihtasutus|sa|mittetulundusühing|mtü|füüsilisest isikust ettevõtja|fie)\b",
            re.IGNORECASE,
        )

        def _strip_legal_form(s: str) -> str:
            return _LEGAL_FORM_RE.sub("", s).strip(" ,.-")

        legal_df["nimi_core"] = legal_df["nimi_lower"].apply(_strip_legal_form)
        all_names_lower = legal_df["nimi_lower"].tolist()
        all_cores = legal_df["nimi_core"].tolist()

        def _search(query: str):
            q = query.strip().lower()
            q_core = _strip_legal_form(q)

            # Substring scan on original lowercased names (catches exact user input)
            mask = legal_df["nimi_lower"].str.contains(q, regex=False)
            # Also scan on core names using the core query (catches "Jupiter Plus" → "aktsiaselts Jupiter Plus")
            if q_core and q_core != q:
                mask |= legal_df["nimi_core"].str.contains(q_core, regex=False)
            exact_indices = legal_df.index[mask].tolist()
            exact = [(i, 1.0) for i in exact_indices]

            if len(exact) >= top_n:
                return exact[:top_n]

            # Fuzzy on core names so legal form differences don't inflate edit distance
            seen = set(exact_indices)
            token_mask = pd.Series(False, index=legal_df.index)
            for tok in q_core.split():
                if len(tok) >= 3:
                    token_mask |= legal_df["nimi_core"].str.contains(tok, regex=False)
            candidates_idx = [i for i in legal_df.index[token_mask] if i not in seen]
            candidate_cores = [all_cores[i] for i in candidates_idx]

            fuzzy = []
            close_set = set(difflib.get_close_matches(q_core, candidate_cores, n=20, cutoff=0.4))
            for core, i in zip(candidate_cores, candidates_idx):
                if core in close_set and i not in seen:
                    score = difflib.SequenceMatcher(None, q_core, core).ratio()
                    fuzzy.append((i, score))
                    seen.add(i)

            return sorted(exact + fuzzy, key=lambda x: -x[1])[:top_n]

        sections = []
        for name in names:
            results = _search(name)
            if not results:
                sections.append(f"### '{name}'\nNo matches found.\n")
                continue
            lines = [
                f"### '{name}'\n",
                "| # | Registry Code | Company Name | Match |",
                "|---|---|---|---|",
            ]
            for rank, (idx, score) in enumerate(results, 1):
                code = legal_df.iloc[idx]["ariregistri_kood"]
                full_name = legal_df.iloc[idx]["nimi"]
                lines.append(f"| {rank} | `{code}` | {full_name} | {score * 100:.0f}% |")
            sections.append("\n".join(lines))

        header = f"## Company Search ({len(names)} queries)\n"
        footer = "\n\n*Pass chosen registry codes to `screen_companies` (company_codes=[...]) or API tools.*"
        return header + "\n\n".join(sections) + footer

    except Exception as e:
        return f"## Error\n\n{e}"


# ---------------------------------------------------------------------------
# Tool 8 — list_financial_columns
# ---------------------------------------------------------------------------

@mcp.tool()
def list_financial_columns(years: List[int]) -> str:
    """
    List all available column names for a given set of years.

    Call this before writing custom_formulas or financial_filters when the user
    uses vague terms ("opex", "net assets"). Use the returned column names to
    propose an exact formula mapping for the user to confirm.

    Both Estonian and English names are accepted in formulas and filters —
    this tool shows both so the user can choose whichever they prefer.

    Args:
        years: Financial years of interest (e.g. [2023, 2022, 2021]).

    Returns:
        Markdown reference listing raw financial columns and computed ratio columns.
    """
    lines = [
        "## Available Financial Columns\n",
        "Both Estonian and English names work in `financial_filters` and `custom_formulas`.\n",
        "### Raw Financial Columns\n",
        "| English column name | Estonian column name |",
        "|---|---|",
    ]
    for est, eng in COLUMN_TRANSLATIONS.items():
        for y in years:
            lines.append(f"| `{eng}_{y}` | `{est}_{y}` |")

    lines += [
        "\n### Computed Ratio Columns\n",
        "| Column name | Notes |",
        "|---|---|",
    ]
    ratio_cols = []
    for y in years:
        ratio_cols += [
            (f"ebitda_{y}", "absolute EBITDA in €"),
            (f"ebitda_margin_{y}", ""),
            (f"roe_{y}", ""),
            (f"roa_{y}", ""),
            (f"asset_turnover_{y}", ""),
            (f"employee_efficiency_{y}", "revenue per employee"),
            (f"cash_ratio_{y}", ""),
            (f"current_ratio_{y}", ""),
            (f"debt_to_equity_{y}", ""),
            (f"labour_ratio_{y}", "labour costs / revenue"),
        ]
    if len(years) >= 2:
        sorted_years = sorted(years)
        for i in range(len(sorted_years) - 1):
            a, b = sorted_years[i], sorted_years[i + 1]
            ratio_cols.append((f"revenue_growth_{a}_to_{b}", "year-over-year"))
        ratio_cols.append(
            (f"revenue_cagr_{sorted_years[0]}_to_{sorted_years[-1]}", "compound annual growth rate")
        )

    for col, note in ratio_cols:
        lines.append(f"| `{col}` | {note} |")

    lines += [
        "\n### Enrichment Columns (no year suffix)\n",
        "| Column name | Description |",
        "|---|---|",
        "| `company_name` | Registered company name |",
        "| `company_code` | Estonian registry code |",
        "| `company_age_years` | Years since registration |",
        "| `owner_count` | Number of shareholders |",
        "| `top_3_owners` | Top 3 shareholder names |",
        "| `top_3_percentages` | Top 3 ownership stakes |",
        f"| `industry_code_{years[0]}` | EMTAK code |",
        f"| `industry_description_{years[0]}` | Industry description |",
        f"| `geo_domestic_share_{years[0]}` | Share of revenue from Estonia (0.0–1.0) |",
        f"| `geo_export_share_{years[0]}` | Share of revenue from non-Estonian markets (0.0–1.0) |",
        f"| `geo_top_export_market_{years[0]}` | Country with highest export revenue (Estonian name) |",
        f"| `geo_revenue_countries_{years[0]}` | All countries with revenue, comma-joined |",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 5 — screen_companies
# ---------------------------------------------------------------------------

@mcp.tool()
async def screen_companies(
    years: List[int],
    ctx: Context,
    data_path: Optional[str] = None,
    legal_forms: Optional[List[str]] = None,
    top_n: int = 50,
    sort_column: Optional[str] = None,
    sort_ascending: bool = False,
    standard_formulas: Optional[Dict[str, Any]] = None,
    custom_formulas: Optional[Dict[str, str]] = None,
    financial_filters: Optional[List[Dict[str, Any]]] = None,
    industry_keyword: Optional[str] = None,
    industry_codes: Optional[List[str]] = None,
    ownership_filters: Optional[Dict[str, Any]] = None,
    export_columns: Optional[List[str]] = None,
    skip_steps: Optional[List[str]] = None,
    company_codes: Optional[List[str]] = None,
    # Geography filters
    min_export_share: Optional[float] = None,
    max_domestic_share: Optional[float] = None,
    export_countries: Optional[List[str]] = None,
    geography_filters: Optional[Dict[str, Any]] = None,
    # Legacy convenience shortcuts
    min_ebitda_margin: Optional[float] = None,
    min_revenue: Optional[float] = None,
    min_equity: Optional[float] = None,
    min_revenue_growth: Optional[float] = None,
    min_revenue_cagr: Optional[float] = None,
) -> str:
    """
    Run the full company screening pipeline on local RIK open-data CSV files.

    Requires the RIK open-data CSV files locally. The folder path is read from
    RIK_SCREENER_PATH env var, or passed via data_path.

    ── YEARS & LEGAL FORMS ──────────────────────────────────────────────────
    years:         Financial years to analyse, NEWEST FIRST (e.g. [2023,2022,2021]).
    data_path:     Absolute path to folder with RIK CSV files. Overrides RIK_SCREENER_PATH.
    legal_forms:   Default ["AS", "OÜ"]. Use ["AS"] for joint-stock only.

    ── SORTING & PAGINATION ─────────────────────────────────────────────────
    top_n:         Max companies to return. Default 50.
    sort_column:   Column to sort by (English, with year/range suffix).
                   E.g. "ebitda_margin_2023", "revenue_cagr_2021_to_2023".
                   Defaults to ebitda_margin of the most recent year.
    sort_ascending: If True, sort lowest-first. Default False.

    ── STANDARD FORMULAS ────────────────────────────────────────────────────
    standard_formulas: Dict of formula_name → config. If omitted, a sensible
      default set is computed from years. Pass {} for raw data only.
      Per-year: ebitda (absolute €), ebitda_margin, roe, roa, asset_turnover,
                employee_efficiency, cash_ratio, current_ratio, debt_to_equity, labour_ratio
        config: {"years": [...], "use_averages": bool}
      NOTE: "ebitda_2024" and "ebitda_margin_2024" are computed by default — no need to pass standard_formulas explicitly.
      Growth:  revenue_growth: {"year_pairs": [[from, to], ...]}
               revenue_cagr:   {"start_year": int, "end_year": int}

    ── CUSTOM FORMULAS ───────────────────────────────────────────────────────
    custom_formulas: Dict of name → pandas eval expression. Column names must
      be quoted. Both Estonian and English column names are accepted.
      E.g. {"margin": '"operating_profit_2023" / "revenue_2023"'}
      Call list_financial_columns(years) to see available column names.

    ── NUMERIC FILTERS ───────────────────────────────────────────────────────
    financial_filters: List of {"column": str, "min": float, "max": float}.
      Any column can be filtered. Both Estonian and English column names work.
      All currency values are in EUROS. Ratios and margins are decimals (0.10 = 10%).
      E.g. [{"column": "ebitda_margin_2023", "min": 0.10},   # 10% margin
            {"column": "revenue_2023", "min": 1000000}]       # 1 000 000 EUR

    ── INDUSTRY / SECTOR FILTERS ─────────────────────────────────────────────
    industry_keyword: Case-insensitive substring match on industry_description.
      Call lookup_industry_codes() first to confirm the right terms.
    industry_codes:   List of EMTAK code prefixes (prefix matching).
      E.g. ["62"] matches all IT service sub-codes.

    ── OWNERSHIP FILTERS ─────────────────────────────────────────────────────
    ownership_filters: {"owner_count": {"min":int,"max":int,"exact":int},
                         "percentages": {"min":float,"max":float}}

    ── GEOGRAPHY FILTERS ─────────────────────────────────────────────────────
    min_export_share:  Minimum share (0.0–1.0) of revenue from non-Estonian markets.
                       E.g. 0.5 = at least 50% export revenue.
    max_domestic_share: Maximum share (0.0–1.0) of revenue from Estonia.
    export_countries:  List of country names in Estonian that must appear in the
                       company's revenue countries. E.g. ["Saksamaa", "USA"].
                       Call lookup_geographic_regions() first to confirm spellings.
                       A company matches if ANY of the listed countries has revenue.
    geography_filters: Dict combining any of the above keys for advanced use.
    NOTE: Companies without geographic revenue data are EXCLUDED when any geography
    filter is active. Geographic columns: "geo_export_share_{year}",
    "geo_domestic_share_{year}", "geo_top_export_market_{year}", "geo_revenue_countries_{year}".

    ── OUTPUT CONTROL ────────────────────────────────────────────────────────
    export_columns: List of column names to include (others hidden).
                   Identity columns: "company_name", "company_code" (NOT "registry_code").
                   Computed columns (e.g. "ebitda_2024") must first be requested via
                   standard_formulas or custom_formulas, then listed here.
    skip_steps:    Steps to skip: "industry", "age", "emtak", "ownership", "geography".
    company_codes: Restrict screening to specific companies by registry code.
                   Use after find_company() to get codes from names, then pass
                   them here to fetch CSV data for only those companies.

    ── LEGACY SHORTCUTS ──────────────────────────────────────────────────────
    min_ebitda_margin: Minimum EBITDA margin (e.g. 0.10 for 10%).
    min_revenue:       Minimum revenue in euros.
    min_equity:        Minimum equity in euros.
    min_revenue_growth: Minimum YoY revenue growth (most recent pair). Needs ≥2 years.
    min_revenue_cagr:   Minimum revenue CAGR over full years span. Needs ≥2 years.

    Returns:
        Markdown report with summary statistics and a ranked company table.
    """
    try:
        path = data_path or os.environ.get("RIK_SCREENER_PATH", "")
        if not path:
            return (
                "## Error\n\n"
                "Data path not set. Pass `data_path` explicitly or set "
                "`RIK_SCREENER_PATH` to the folder containing the RIK CSV files."
            )
        os.environ["RIK_SCREENER_PATH"] = path

        # --- Build financial_filters, starting with user-supplied list ---
        filters: List[Dict[str, Any]] = list(financial_filters or [])

        # Legacy shortcuts
        if min_ebitda_margin is not None:
            filters.append({"column": f"ebitda_margin_{years[0]}", "min": min_ebitda_margin})
        if min_revenue is not None:
            filters.append({"column": f"revenue_{years[0]}", "min": min_revenue})
        if min_equity is not None:
            filters.append({"column": f"equity_{years[0]}", "min": min_equity})
        if min_revenue_growth is not None:
            if len(years) < 2:
                return "## Error\n\n`min_revenue_growth` requires at least 2 years."
            filters.append({"column": f"revenue_growth_{years[1]}_to_{years[0]}", "min": min_revenue_growth})
        if min_revenue_cagr is not None:
            if len(years) < 2:
                return "## Error\n\n`min_revenue_cagr` requires at least 2 years."
            filters.append({"column": f"revenue_cagr_{years[-1]}_to_{years[0]}", "min": min_revenue_cagr})

        # Build geography_filters from convenience params + explicit dict
        final_geo_filters: Dict[str, Any] = dict(geography_filters or {})
        if min_export_share is not None:
            final_geo_filters["min_export_share"] = min_export_share
        if max_domestic_share is not None:
            final_geo_filters["max_domestic_share"] = max_domestic_share
        if export_countries:
            existing = set(final_geo_filters.get("export_countries", []))
            final_geo_filters["export_countries"] = list(existing | set(export_countries))

        # Save English column names before translating, so we can detect skipped filters later
        _requested_filter_cols = [f["column"] for f in filters]

        # Translate English raw financial column names in filters to Estonian
        filters = _translate_filters_to_estonian(filters, years)

        # Translate column names in custom formula expressions (non-destructive)
        translated_custom = None
        if custom_formulas:
            translated_custom = _translate_formula_expressions(custom_formulas, years)

        # Default standard formulas when not supplied
        if standard_formulas is None:
            standard_formulas = {
                "ebitda":              {"years": years},
                "ebitda_margin":       {"years": years},
                "roe":                 {"years": years},
                "roa":                 {"years": years},
                "asset_turnover":      {"years": years},
                "employee_efficiency": {"years": years},
                "cash_ratio":          {"years": years},
                "current_ratio":       {"years": years},
                "debt_to_equity":      {"years": years},
                "labour_ratio":        {"years": years},
                **({"revenue_growth": {"year_pairs": [[years[1], years[0]]]}}
                   if len(years) >= 2 else {}),
                **({"revenue_cagr": {"start_year": years[-1], "end_year": years[0]}}
                   if len(years) >= 2 else {}),
            }

        # Auto-derive skip_steps from requested columns (user override takes precedence)
        derived_skip = _derive_skip_steps(
            export_columns, industry_keyword, industry_codes, ownership_filters,
            final_geo_filters, skip_steps
        )

        # Warn if industry filters requested but enrichment steps are skipped
        warning = ""

        # Warn upfront about impossible filter bounds (min > max always yields empty results)
        for _f in (financial_filters or []):
            _min, _max = _f.get("min"), _f.get("max")
            if _min is not None and _max is not None and _min > _max:
                warning += (
                    f"> **Warning:** filter on `{_f.get('column', '')}` has min ({_min}) > max ({_max}) "
                    f"— this filter will produce no results.\n\n"
                )

        if (industry_keyword or industry_codes):
            blocked = [s for s in derived_skip if s in ("industry", "emtak")]
            if blocked:
                warning = (
                    f"> **Warning:** industry filters were requested but "
                    f"`skip_steps` includes {blocked} — industry columns will "
                    f"not be available and the filter will be silently skipped.\n\n"
                )

        # Auto-add industry columns to export_columns so the pipeline includes them
        auto_added_cols: List[str] = []
        if (industry_keyword or industry_codes) and export_columns is not None:
            for col in (f"industry_description_{years[0]}", f"industry_code_{years[0]}"):
                if col not in export_columns:
                    export_columns = list(export_columns) + [col]
                    auto_added_cols.append(col)

        skipped_str = ", ".join(derived_skip) if derived_skip else "nothing"
        await ctx.info(f"Starting pipeline — years: {years}, skipping: {skipped_str}")
        await ctx.report_progress(0, 4)

        config: Dict[str, Any] = {
            "years": years,
            "legal_forms": legal_forms or ["AS", "OÜ"],
            "use_dataframe_pipeline": True,
            "save_final_output": False,
            "standard_formulas": standard_formulas,
            "top_n": top_n,
            "sort_ascending": sort_ascending,
            **({"custom_formulas": translated_custom} if translated_custom else {}),
            **({"financial_filters": filters} if filters else {}),
            **({"ownership_filters": ownership_filters} if ownership_filters else {}),
            # export_columns applied post-translation in MCP layer (English names)

            **({"skip_steps": derived_skip} if derived_skip else {}),
            **({"sort_column": _reverse_column_map(years).get(sort_column, sort_column)} if sort_column else {}),
            **({"industry_codes_filter": industry_codes} if industry_codes else {}),
            **({"company_codes": company_codes} if company_codes else {}),
            **({"geography_filters": final_geo_filters} if final_geo_filters else {}),
        }

        await ctx.info("Running screening pipeline…")
        await ctx.report_progress(1, 4)

        df = run_company_screening(config)

        await ctx.report_progress(2, 4)

        if df.empty:
            return warning + "## Screening Results\n\nNo companies matched the criteria."

        await ctx.info("Translating and filtering results…")
        translated = translate_dataframe_columns(df, years)

        # Post-pipeline industry keyword filter (industry_codes already handled inside pipeline)
        if industry_keyword:
            translated = _apply_industry_filters(translated, years, industry_keyword, None)
            # Warn if keyword filter had no industry columns to apply (enrichment may have failed)
            if not any(
                f"industry_description_{y}" in translated.columns
                or f"industry_combined_{y}" in translated.columns
                for y in years
            ):
                warning += (
                    "> **Warning:** `industry_keyword` filter was requested but no industry "
                    "description columns are present in the results (industry enrichment may "
                    "have failed) — results are unfiltered by industry keyword.\n\n"
                )

        await ctx.report_progress(3, 4)

        if translated.empty:
            return warning + "## Screening Results\n\nNo companies matched the industry filter."

        # Apply export_columns filter now that columns are in English
        # (auto-added industry cols are excluded unless user originally requested them)
        sort_was_computed = sort_column is not None and sort_column in translated.columns
        if export_columns is not None:
            original_export = [c for c in export_columns if c not in auto_added_cols]
            # Auto-include sort column so it's always visible in the output
            if sort_column and sort_column not in original_export and sort_column in translated.columns:
                original_export = original_export + [sort_column]
            available = [c for c in original_export if c in translated.columns]
            missing = [c for c in original_export if c not in translated.columns]
            if missing:
                await ctx.warning(f"Requested columns not found (skipped): {missing}")
            if available:
                translated = translated[available]

        # Resolve sort column fallback; warn if user's column wasn't found
        if not sort_column:
            candidate = f"ebitda_margin_{years[0]}"
            if candidate in translated.columns:
                sort_column = candidate
        elif sort_column not in translated.columns:
            if not sort_was_computed:
                # Column was never computed — sorting did not happen
                warning += (
                    f"> **Warning:** sort column `{sort_column}` was not found in the results "
                    f"— results are shown in default order.\n\n"
                )
            sort_column = None

        # Warn if any requested filter columns were silently skipped (column not in data)
        if _requested_filter_cols:
            skipped_filters = [c for c in _requested_filter_cols if c not in translated.columns]
            if skipped_filters:
                warning += (
                    f"> **Warning:** filter column(s) not found — filter was NOT applied for: "
                    + ", ".join(f"`{c}`" for c in skipped_filters) + ". "
                    f"Results are unfiltered for these columns.\n\n"
                )

        # Warn if ownership filters were requested but ownership columns are absent
        # (happens when shareholders.json is missing — enrichment returns unchanged df, not None)
        if ownership_filters and "owner_count" not in translated.columns:
            warning += (
                "> **Warning:** `ownership_filters` were requested but ownership data is not "
                "available (shareholders.json may be missing or failed to load) — ownership "
                "filters were NOT applied.\n\n"
            )

        # Warn if any requested year has zero financial columns in results
        # (happens when that year's CSV is missing and the merger silently skipped it)
        absent_years = [
            y for y in years
            if not any(c.endswith(f"_{y}") for c in translated.columns)
        ]
        if absent_years:
            warning += (
                f"> **Warning:** no data found for year(s) "
                f"{', '.join(str(y) for y in absent_years)} — CSV files for these "
                f"years may be missing. Results only include available years.\n\n"
            )

        # Warn if any custom formula is absent from results (validation failed before it ran)
        # Formulas that ran but produced all-NaN are handled separately in the summary footnote.
        if custom_formulas:
            absent_formulas = [n for n in custom_formulas if n not in translated.columns]
            if absent_formulas:
                warning += (
                    f"> **Warning:** custom formula(s) "
                    + ", ".join(f"`{n}`" for n in absent_formulas)
                    + " could not be computed (validation failed or required columns missing)"
                    " — these columns are absent from results.\n\n"
                )

        # Build human-readable filter summary for the output header
        filters_applied: List[str] = []
        if industry_keyword:
            filters_applied.append(f"industry keyword: '{industry_keyword}'")
        if industry_codes:
            filters_applied.append(f"EMTAK codes: {', '.join(industry_codes)}")
        for f in (financial_filters or []):
            parts = [f["column"]]
            if "min" in f:
                parts.append(f"≥ {f['min']}")
            if "max" in f:
                parts.append(f"≤ {f['max']}")
            filters_applied.append(" ".join(parts))

        # Surface which formula columns were computed so Claude knows what's available
        computed = list((standard_formulas or {}).keys()) + list((custom_formulas or {}).keys())
        if computed:
            label = ", ".join(computed[:6]) + (" …" if len(computed) > 6 else "")
            filters_applied.append(f"computed columns: {label}")
        else:
            filters_applied.append("computed columns: none (raw financial data only)")

        year_range = f"{years[-1]}–{years[0]}" if len(years) > 1 else str(years[0])
        title = f"Company Screening — {year_range}"

        await ctx.info(f"Formatting {len(translated)} companies…")
        await ctx.report_progress(4, 4)

        return warning + format_screening_results(
            translated, title=title, top_n=top_n, filters_applied=filters_applied
        )

    except Exception as e:
        return f"## Error\n\n{e}"


# ---------------------------------------------------------------------------
# EMTA standalone screener
# ---------------------------------------------------------------------------

@mcp.tool()
async def screen_emta(
    ctx: Context,
    data_path: Optional[str] = None,
    top_n: int = 50,
    sort_ascending: bool = False,
    min_turnover_yoy: Optional[float] = None,
    max_turnover_yoy: Optional[float] = None,
    min_turnover: Optional[float] = None,
    industry_keyword: Optional[str] = None,
    region: Optional[str] = None,
    company_codes: Optional[List[str]] = None,
) -> str:
    """
    Screen Estonian companies using EMTA quarterly VAT-turnover data only.

    Completely independent of RIK annual filing data. Loads
    tasutud_maksud_kaesolev_aasta.csv and tasutud_maksud_varasemad_aastad.csv
    from the data folder and computes year-over-year turnover growth for the
    most recent quarter with sufficient data coverage.

    ── IMPORTANT CAVEATS ────────────────────────────────────────────────────
    EMTA turnover (käive) is NOT the same as RIK revenue. It is the sum of
    VAT declaration lines 1-3 and includes reverse-charge VAT purchases in
    addition to the company's own taxable sales. Absolute values should not
    be compared to financial statement revenue.
    Filings are 1-month lagged: Q1 label covers Dec-Feb activity.
    Use turnover_yoy (YoY growth of the same quarter) as the primary signal.

    ── OUTPUT COLUMNS ───────────────────────────────────────────────────────
    company_name      — company name from EMTA register
    company_code      — registry code (registrikood)
    region            — county (maakond)
    industry          — EMTA industry description (tegevusala)
    turnover_current  — turnover (€) in the reference quarter
    turnover_prior    — turnover (€) in the same quarter one year prior
    turnover_yoy      — YoY growth rate (e.g. 0.25 = +25%)
    employees         — employee count in the reference quarter
    period            — reference quarter label (e.g. "2025Q4")
    period_prior      — prior-year quarter label (e.g. "2024Q4")

    ── FILTERS ──────────────────────────────────────────────────────────────
    min_turnover_yoy  — minimum YoY growth (e.g. 0.20 = +20% or more)
    max_turnover_yoy  — maximum YoY growth
    min_turnover      — minimum turnover in reference quarter (€)
    industry_keyword  — case-insensitive substring match on EMTA industry
    region            — case-insensitive substring match on county name
    company_codes     — restrict to specific registry codes

    ── SORTING ──────────────────────────────────────────────────────────────
    Results are sorted by turnover_yoy descending by default (fastest-growing
    companies first). Set sort_ascending=True to reverse.

    Returns a markdown report with a ranked company table.
    """
    try:
        path = data_path or os.environ.get("RIK_SCREENER_PATH", "")
        if not path:
            return (
                "## Error\n\n"
                "Data path not set. Pass `data_path` explicitly or set "
                "the `RIK_SCREENER_PATH` environment variable."
            )

        from .utils.config import ConfigManager
        ConfigManager(base_path=path)

        from .emta_screener import run_emta_screening

        await ctx.info("Loading EMTA data…")
        await ctx.report_progress(1, 3)

        config: Dict[str, Any] = {
            'top_n': top_n,
            'sort_ascending': sort_ascending,
            **({"min_turnover_yoy": min_turnover_yoy} if min_turnover_yoy is not None else {}),
            **({"max_turnover_yoy": max_turnover_yoy} if max_turnover_yoy is not None else {}),
            **({"min_turnover": min_turnover} if min_turnover is not None else {}),
            **({"industry_keyword": industry_keyword} if industry_keyword else {}),
            **({"region": region} if region else {}),
            **({"company_codes": company_codes} if company_codes else {}),
        }

        df = run_emta_screening(config)

        await ctx.report_progress(2, 3)

        if df.empty:
            return "## EMTA Screening Results\n\nNo companies matched the criteria or no EMTA data files were found."

        await ctx.info(f"Formatting {len(df)} companies…")
        await ctx.report_progress(3, 3)

        return _format_emta_results(df, top_n=top_n, filters={
            "min_turnover_yoy": min_turnover_yoy,
            "max_turnover_yoy": max_turnover_yoy,
            "min_turnover": min_turnover,
            "industry_keyword": industry_keyword,
            "region": region,
        })

    except Exception as e:
        return f"## Error\n\n{e}"


def _is_val(v) -> bool:
    """Return True if v is a non-null, non-NaN value."""
    if v is None:
        return False
    try:
        return v == v  # NaN != NaN
    except Exception:
        return False


def _format_emta_results(df, top_n: int = 50, filters: dict = None) -> str:
    """Format run_emta_screening() output as a markdown report."""
    period = df['period'].iloc[0] if 'period' in df.columns and not df.empty else "—"
    period_prior = df['period_prior'].iloc[0] if 'period_prior' in df.columns and not df.empty else "—"
    total = len(df)

    # Build filter summary
    filter_parts = []
    for k, v in (filters or {}).items():
        if v is not None:
            filter_parts.append(f"{k}: {v}")

    header = (
        f"## EMTA Screening — {period} vs {period_prior}\n\n"
        f"- **Companies returned:** {min(total, top_n)}"
        + (f" of {total} matched" if total > top_n else "")
        + f"\n- **Reference quarter:** {period} (EMTA filing lag: +1 month)"
        + f"\n- **Compared to:** {period_prior}\n"
        + "- **Note:** Turnover = VAT declaration lines 1-3. Includes reverse-charge "
        "purchases. Use YoY growth signal only — do not compare to RIK revenue.\n"
    )
    if filter_parts:
        header += "- **Filters:** " + "; ".join(filter_parts) + "\n"

    # Column display config: (col_name, header_label, formatter)
    col_config = [
        ("company_name",     "Company",          lambda v: str(v) if _is_val(v) else "—"),
        ("company_code",     "Registry Code",    lambda v: str(v) if _is_val(v) else "—"),
        ("region",           "Region",           lambda v: str(v) if _is_val(v) else "—"),
        ("industry",         "Industry",         lambda v: str(v) if _is_val(v) else "—"),
        ("turnover_current", "Turnover (current)", lambda v: f"{int(v):,} €" if _is_val(v) else "—"),
        ("turnover_prior",   "Turnover (prior)",   lambda v: f"{int(v):,} €" if _is_val(v) else "—"),
        ("turnover_yoy",     "YoY Growth",         lambda v: f"{v:+.1%}" if _is_val(v) else "—"),
        ("employees",        "Employees",          lambda v: str(int(v)) if _is_val(v) else "—"),
        ("period",           "Period",             lambda v: str(v) if _is_val(v) else "—"),
    ]

    active_cols = [(col, hdr, fmt) for col, hdr, fmt in col_config if col in df.columns]

    headers = ["#"] + [hdr for _, hdr, _ in active_cols]
    rows = ["|" + "|".join(["---"] * len(headers)) + "|"]
    rows.insert(0, "| " + " | ".join(headers) + " |")
    rows.append("|" + "|".join(["---"] * len(headers)) + "|")

    for rank, (_, row) in enumerate(df.head(top_n).iterrows(), 1):
        cells = [str(rank)] + [fmt(row.get(col)) for col, _, fmt in active_cols]
        rows.append("| " + " | ".join(cells) + " |")

    table = "\n".join(rows)
    footer = ""
    if total > top_n:
        footer = f"\n\n*Showing top {top_n} of {total} companies. Adjust `top_n` to see more.*"

    return header + "\n" + table + footer


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    mcp.run()


if __name__ == "__main__":
    main()
