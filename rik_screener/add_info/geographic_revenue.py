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

DOMESTIC_COUNTRY = "Eesti"
GEO_COUNTRY_COL = "Riigi nimetus"
GEO_REVENUE_COL = "Müügitulu geograafiliste piirkondade lõikes"


def add_geographic_revenue(
    input_file: Optional[str] = "companies_with_ratios.csv",
    input_data: Optional[pd.DataFrame] = None,
    output_file: Optional[str] = "companies_with_geography.csv",
    geo_file: str = "geo_revenue.csv",
    years: list = None,
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

    config = get_config()
    if years is None:
        years = config.get_years()

    years = sorted(years, reverse=True)

    # Validate geo file header before chunked read
    geo_header = safe_read_csv(geo_file, nrows=0)
    if geo_header is None:
        log_error(f"Geographic revenue file {geo_file} not found")
        return companies_df

    if GEO_REVENUE_COL not in geo_header.columns:
        log_error(
            f"Expected column '{GEO_REVENUE_COL}' not found in {geo_file}. "
            f"Found: {geo_header.columns.tolist()}"
        )
        return companies_df

    log_info(f"Loading geographic revenue data from {geo_file}")

    all_report_ids: set = set()
    year_report_id_mapping: dict = {}

    for year in years:
        report_id_col = f"report_id_{year}"
        if report_id_col in companies_df.columns:
            year_ids = set(companies_df[report_id_col].dropna().astype(int))
            year_report_id_mapping[year] = year_ids
            all_report_ids.update(year_ids)
            log_info(f"Year {year}: {len(year_ids)} report IDs")

    if not all_report_ids:
        log_warning("No report IDs found for any year")
        return companies_df

    log_info(f"Total unique report IDs across all years: {len(all_report_ids)}")

    try:
        chunk_size = config.get_default('chunk_size', 500000)
        all_geo_chunks: list = []

        for chunk in safe_read_csv(geo_file, chunk_size=chunk_size):
            # Strip whitespace and stray quote artefacts from country names
            chunk[GEO_COUNTRY_COL] = chunk[GEO_COUNTRY_COL].astype(str).str.strip().str.strip("'\"")

            filtered = chunk[chunk["report_id"].isin(all_report_ids)].copy()

            # Drop null or zero-revenue rows — they add no signal
            filtered = filtered[filtered[GEO_REVENUE_COL].notna()]
            filtered = filtered[filtered[GEO_REVENUE_COL] != 0]

            if not filtered.empty:
                all_geo_chunks.append(filtered[["report_id", GEO_COUNTRY_COL, GEO_REVENUE_COL]])

        if not all_geo_chunks:
            log_warning("No geographic revenue data found for any companies")
            return companies_df

        geo_df = pd.concat(all_geo_chunks, ignore_index=True)
        log_info(f"Found {len(geo_df)} geographic revenue records for {geo_df['report_id'].nunique()} companies")

        # --- Aggregate per report_id ---

        total_by_id = geo_df.groupby("report_id")[GEO_REVENUE_COL].sum()

        domestic_mask = geo_df[GEO_COUNTRY_COL] == DOMESTIC_COUNTRY
        domestic_by_id = (
            geo_df[domestic_mask]
            .groupby("report_id")[GEO_REVENUE_COL]
            .sum()
        )

        # domestic_share: NaN when total is 0 (shouldn't happen after zero-drop, but guard anyway)
        domestic_share = (domestic_by_id / total_by_id).where(total_by_id > 0)

        # Companies with revenue but no Eesti row → 100% export
        has_revenue = total_by_id[total_by_id > 0].index
        purely_export = has_revenue.difference(domestic_by_id.index)
        domestic_share = domestic_share.reindex(total_by_id.index)
        domestic_share.loc[purely_export] = 0.0

        export_share = 1.0 - domestic_share

        # Top export market: country (excluding Eesti) with highest revenue per report_id
        export_rows = geo_df[geo_df[GEO_COUNTRY_COL] != DOMESTIC_COUNTRY]
        if not export_rows.empty:
            top_idx = export_rows.groupby("report_id")[GEO_REVENUE_COL].idxmax()
            top_export_market = (
                export_rows.loc[top_idx, ["report_id", GEO_COUNTRY_COL]]
                .set_index("report_id")[GEO_COUNTRY_COL]
            )
        else:
            top_export_market = pd.Series(dtype=str)

        # Countries list: comma-joined, ordered by revenue descending (non-zero already filtered)
        countries_list = (
            geo_df.sort_values(GEO_REVENUE_COL, ascending=False)
            .groupby("report_id")[GEO_COUNTRY_COL]
            .apply(", ".join)
        )

        log_info("Assigning geographic columns to companies per year")

        for year in years:
            if year not in year_report_id_mapping:
                log_warning(f"No report IDs found for year {year}, skipping")
                continue

            report_id_col = f"report_id_{year}"
            rid = companies_df[report_id_col]

            companies_df[f"geo_domestic_share_{year}"]    = rid.map(domestic_share)
            companies_df[f"geo_export_share_{year}"]      = rid.map(export_share)
            companies_df[f"geo_top_export_market_{year}"] = rid.map(top_export_market)
            companies_df[f"geo_revenue_countries_{year}"] = rid.map(countries_list)

            assigned = companies_df[f"geo_export_share_{year}"].notna().sum()
            total = rid.notna().sum()
            log_info(f"Year {year}: geographic data matched for {assigned} of {total} companies")

    except Exception as e:
        log_error(f"Error processing geographic revenue data: {str(e)}")
        import traceback
        traceback.print_exc()
        return companies_df

    if output_file and not return_dataframe:
        if safe_write_csv(companies_df, output_file, encoding='utf-8'):
            log_info(f"Saved {len(companies_df)} companies with geographic data to {output_file}")
        else:
            log_error(f"Failed to save results to {output_file}")

    return companies_df
