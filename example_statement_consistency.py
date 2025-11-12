"""
Example usage of the check_statement_consistency function.

This script demonstrates how to use the new API feature to check if
statement codes remained consistent across multiple years for companies.
"""

from rik_screener.api_workspace import check_statement_consistency

# Example usage
if __name__ == "__main__":
    # Configuration
    USERNAME = "your_username"  # Replace with your RIK API username
    PASSWORD = "your_password"  # Replace with your RIK API password

    # Company codes to check
    company_codes = ["70000310", "10560025"]  # Example Estonian company codes

    # Year range
    target_year = 2023  # Most recent year
    end_year = 2020     # Oldest year to check

    # Statement types to check (BS = Balance Sheet, IS = Income Statement, CF = Cash Flow)
    statement_types = ["BS", "IS", "CF"]

    # Output CSV file
    output_file = "statement_consistency_results.csv"

    # Run the consistency check
    print("Starting statement consistency check...")
    print(f"Companies: {company_codes}")
    print(f"Year range: {target_year} to {end_year}")
    print(f"Statement types: {statement_types}")
    print()

    results = check_statement_consistency(
        company_codes=company_codes,
        username=USERNAME,
        password=PASSWORD,
        target_year=target_year,
        end_year=end_year,
        statement_types=statement_types,
        rate_limit=20,
        output_file=output_file
    )

    # Display results
    print("\nResults:")
    print("-" * 80)

    for company_code, (consistent, code_arrays, consolidation_status) in results.items():
        print(f"\nCompany: {company_code}")
        print(f"Overall consistency: {consistent}")
        print(f"Consolidation status: {consolidation_status}")

        for i, st in enumerate(statement_types):
            if i < len(code_arrays):
                codes = code_arrays[i]
                print(f"  {st} codes ({target_year} to {end_year}): {codes}")

                # Check if this specific statement type is consistent
                non_none_codes = [c for c in codes if c is not None]
                if non_none_codes and None not in codes:
                    unique = set(non_none_codes)
                    st_consistent = "Yes" if len(unique) == 1 else "No"
                else:
                    st_consistent = "No"
                print(f"  {st} consistent: {st_consistent}")

    print("\n" + "-" * 80)
    print(f"\nDetailed results saved to: {output_file}")

    # Example of interpreting results:
    # - If consistent = "Yes": All statement types have the same codes across all years
    # - If consistent = "No": At least one statement type changed or has missing data
    # - code_arrays contains the actual statement codes for each type and year
    #   (ordered from target_year to end_year)
    # - consolidation_status: "Non-consolidated", "Consolidated",
    #   "Consolidated since yyyy", or "Non-consolidated since yyyy"
