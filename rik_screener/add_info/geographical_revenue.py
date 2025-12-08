import pandas as pd
import numpy as np
from typing import List, Dict, Set, Optional, Union

from ..utils import (
    get_config,
    safe_read_csv,
    safe_write_csv,
    log_step,
    log_info,
    log_warning,
    log_error
)


def add_geographical_revenue(
    input_file: Optional[str] = "companies_with_industry.csv",
    input_data: Optional[pd.DataFrame] = None,
    output_file: Optional[str] = "companies_with_geo_revenue.csv",
    geography_file: str = "geography.csv",
    years: list = None,
    return_dataframe: bool = False
) -> Union[pd.DataFrame, None]:
    """
    Add geographical revenue breakdown (Estonia vs. Export) to company data.

    This function processes geographical revenue data from geography.csv and adds
    three columns for each year:
    - estonia_revenue_{year}: Sales revenue in Estonia
    - export_revenue_{year}: Sales revenue in other countries
    - geo_breakdown_{year}: Formatted string "Estonia: xx.x%, Export: yy.y%"

    Args:
        input_file: Path to input CSV file with company data
        input_data: Optional DataFrame with company data (takes precedence over input_file)
        output_file: Path to save output CSV file
        geography_file: Path to geography CSV file with columns:
                       - report_id
                       - "Riigi nimetus" (Country name)
                       - "Müügitulu geograafiliste piirkondade lõikes" (Revenue)
        years: List of years to process (defaults to config years)
        return_dataframe: If True, returns DataFrame instead of saving to file

    Returns:
        DataFrame with geographical revenue columns added, or None if an error occurs
    """

    # Load input data
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

    # Get years from config if not provided
    config = get_config()
    if years is None:
        years = config.get_years()

    years = sorted(years, reverse=True)

    # Check if geography file exists
    geography_header = safe_read_csv(geography_file, nrows=0)
    if geography_header is None:
        log_error(f"Geography file {geography_file} not found")
        return companies_df

    log_info(f"Loading geographical revenue data from {geography_file}")
    log_info(f"Available columns in geography file: {geography_header.columns.tolist()}")

    # Collect all report IDs from all years
    all_report_ids = set()
    year_report_id_mapping = {}

    for year in years:
        report_id_col = f"report_id_{year}"
        if report_id_col in companies_df.columns:
            year_report_ids = set(companies_df[report_id_col].dropna().astype(int))
            year_report_id_mapping[year] = year_report_ids
            all_report_ids.update(year_report_ids)
            log_info(f"Year {year}: {len(year_report_ids)} report IDs")

    if not all_report_ids:
        log_warning("No report IDs found for any year")
        return companies_df

    log_info(f"Total unique report IDs across all years: {len(all_report_ids)}")

    try:
        log_info("Reading geography file...")

        chunk_size = config.get_default('chunk_size', 500000)
        all_geo_data = []

        # Read geography file in chunks
        for chunk in safe_read_csv(
            geography_file,
            chunk_size=chunk_size,
            dtype={"report_id": int}
        ):
            # Filter for relevant report IDs
            filtered_chunk = chunk[chunk["report_id"].isin(all_report_ids)]

            if not filtered_chunk.empty:
                # Keep only necessary columns
                necessary_cols = ["report_id", "Riigi nimetus", "Müügitulu geograafiliste piirkondade lõikes"]
                filtered_chunk = filtered_chunk[necessary_cols].copy()
                all_geo_data.append(filtered_chunk)

        if not all_geo_data:
            log_warning("No geographical data found for any companies")
            # Initialize empty columns
            for year in years:
                companies_df[f"estonia_revenue_{year}"] = np.nan
                companies_df[f"export_revenue_{year}"] = np.nan
                companies_df[f"geo_breakdown_{year}"] = ""
            return companies_df

        geo_data = pd.concat(all_geo_data, ignore_index=True)
        log_info(f"Found {len(geo_data)} total geographical revenue records")

        # Rename columns for easier processing
        geo_data = geo_data.rename(columns={
            "Riigi nimetus": "country",
            "Müügitulu geograafiliste piirkondade lõikes": "revenue"
        })

        # Convert revenue to numeric, handling potential errors
        geo_data["revenue"] = pd.to_numeric(geo_data["revenue"], errors='coerce')
        geo_data = geo_data[geo_data["revenue"].notna()]  # Remove rows with invalid revenue

        log_info(f"After cleaning: {len(geo_data)} valid geographical revenue records")

        # Classify countries as Estonia or Export
        geo_data["is_estonia"] = geo_data["country"].str.strip().str.lower() == "eesti"

        # Aggregate by report_id
        geo_summary = geo_data.groupby(["report_id", "is_estonia"])["revenue"].sum().unstack(fill_value=0)

        # Ensure both columns exist
        if True not in geo_summary.columns:
            geo_summary[True] = 0
        if False not in geo_summary.columns:
            geo_summary[False] = 0

        geo_summary = geo_summary.rename(columns={True: "estonia_revenue", False: "export_revenue"})

        # Calculate total and percentages
        geo_summary["total_revenue"] = geo_summary["estonia_revenue"] + geo_summary["export_revenue"]

        # Calculate percentages (avoid division by zero)
        geo_summary["estonia_pct"] = np.where(
            geo_summary["total_revenue"] > 0,
            (geo_summary["estonia_revenue"] / geo_summary["total_revenue"]) * 100,
            0
        )
        geo_summary["export_pct"] = np.where(
            geo_summary["total_revenue"] > 0,
            (geo_summary["export_revenue"] / geo_summary["total_revenue"]) * 100,
            0
        )

        # Create formatted breakdown string
        geo_summary["geo_breakdown"] = geo_summary.apply(
            lambda row: f"Estonia: {row['estonia_pct']:.1f}%, Export: {row['export_pct']:.1f}%"
            if row["total_revenue"] > 0 else "",
            axis=1
        )

        log_info(f"Created geographical revenue summary for {len(geo_summary)} report IDs")

        # Convert to dictionary for efficient lookup
        geo_dict = geo_summary.to_dict('index')

        # Map geographical data to each year
        for year in years:
            if year not in year_report_id_mapping:
                log_warning(f"No report IDs found for year {year}, skipping")
                continue

            report_id_col = f"report_id_{year}"
            estonia_col = f"estonia_revenue_{year}"
            export_col = f"export_revenue_{year}"
            breakdown_col = f"geo_breakdown_{year}"

            log_info(f"Assigning geographical revenue for year {year}")

            # Map data for each company
            def get_geo_value(report_id, field):
                if pd.isna(report_id):
                    return np.nan if field != "geo_breakdown" else ""
                report_id = int(report_id)
                if report_id in geo_dict:
                    value = geo_dict[report_id].get(field)
                    return value if value is not None else (np.nan if field != "geo_breakdown" else "")
                return np.nan if field != "geo_breakdown" else ""

            companies_df[estonia_col] = companies_df[report_id_col].apply(
                lambda x: get_geo_value(x, "estonia_revenue")
            )
            companies_df[export_col] = companies_df[report_id_col].apply(
                lambda x: get_geo_value(x, "export_revenue")
            )
            companies_df[breakdown_col] = companies_df[report_id_col].apply(
                lambda x: get_geo_value(x, "geo_breakdown")
            )

            assigned_count = companies_df[breakdown_col].ne("").sum()
            total_count = companies_df[report_id_col].notna().sum()

            log_info(f"Year {year}: assigned geographical revenue to {assigned_count} out of {total_count} companies")

    except Exception as e:
        log_error(f"Error processing geographical revenue data: {str(e)}")
        import traceback
        traceback.print_exc()
        return companies_df

    # Save results
    if output_file and not return_dataframe:
        if safe_write_csv(companies_df, output_file):
            log_info(f"Saved {len(companies_df)} companies with geographical revenue to {output_file}")
        else:
            log_error(f"Failed to save results to {output_file}")

    # Log summary statistics
    for year in years:
        breakdown_col = f"geo_breakdown_{year}"
        if breakdown_col in companies_df.columns:
            non_empty = companies_df[breakdown_col].ne("").sum()
            log_info(f"Year {year}: {non_empty} companies with geographical revenue breakdown")
            if non_empty > 0:
                sample = companies_df[companies_df[breakdown_col] != ""][breakdown_col].head(5).tolist()
                log_info(f"Year {year} sample breakdowns: {sample}")

    return companies_df
