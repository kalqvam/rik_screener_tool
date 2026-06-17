"""
Estonian → English translation maps for RIK screener output.

Two separate dictionaries:

  COLUMN_TRANSLATIONS   — the 12 summary financial items used as DataFrame
                          column names in the CSV pipeline (with year suffixes,
                          e.g. "Müügitulu_2023" → "revenue_2023").

  LINE_NAME_TRANSLATIONS — the full XBRL/SOAP taxonomy labels that appear as
                          row values in the `line_name` column returned by
                          get_financial_statements() (BS + IS, ~80 entries).

Public API:
  translate_dataframe_columns(df, years)  → renamed copy of df
  translate_line_names(df)                → copy with line_name column translated
  get_display_name(col_base)              → human-readable header label
"""

from typing import Dict, List
import pandas as pd

# ---------------------------------------------------------------------------
# 1. CSV pipeline column name translations
#    Keys are exact Estonian strings used in ConfigManager.financial_items
#    and as column prefixes; values are English snake_case equivalents.
# ---------------------------------------------------------------------------
COLUMN_TRANSLATIONS: Dict[str, str] = {
    "Müügitulu":                                        "revenue",
    "Ärikasum (kahjum)":                                "operating_profit",
    "Omakapital":                                       "equity",
    "Põhivarade kulum ja väärtuse langus":              "depreciation",
    "Aruandeaasta kasum (kahjum)":                      "net_profit",
    "Varad":                                            "total_assets",
    "Töötajate keskmine arv taandatuna täistööajale":    "avg_employees_fte",
    "Raha":                                             "cash",
    "Lühiajalised kohustised":                          "current_liabilities",
    "Pikaajalised kohustised":                          "long_term_liabilities",
    "Käibevarad":                                       "current_assets",
    "Tööjõukulud":                                      "labour_costs",
}

# ---------------------------------------------------------------------------
# 2. SOAP API / XBRL line name translations
#    Keys are exact strings returned in the `line_name` column by
#    parse_financial_statement_response(); values are English equivalents.
#    Unrecognised names are passed through unchanged.
# ---------------------------------------------------------------------------
LINE_NAME_TRANSLATIONS: Dict[str, str] = {
    # === Balance Sheet (BS) ===
    "RAHA JA PANGAKONTOD":                                                          "Cash and bank accounts",
    "KÄIBEVARA KOKKU":                                                              "Total current assets",
    "Tütarettevõtjate aktsiad või osad":                                            "Shares in subsidiaries",
    "Sidusettevõtjate aktsiad või osad":                                            "Shares in associates",
    "Investeeringud tütar- ja sidusettevõtjatesse":                                 "Investments in subsidiaries and associates",
    "Pikaajalised nõuded":                                                          "Long-term receivables",
    "Pikaajalised ettemaksed":                                                      "Long-term prepayments",
    "PIKAAJALISED FINANTSINVESTEERINGUD KOKKU":                                     "Total long-term financial investments",
    "MATERIAALNE PÕHIVARA KOKKU":                                                   "Total tangible fixed assets",
    "Pikaajalised nõuded ostjate vastu":                                            "Long-term trade receivables",
    "Pikaajalised nõuded seotud osapoolte vastu":                                   "Long-term receivables from related parties",
    "Pikaajalised maksude ettemaksed ja tagasinõuded":                              "Long-term tax prepayments and claims",
    "Pikaajalised muud nõuded":                                                     "Long-term other receivables",
    "IMMATERIAALNE PÕHIVARA KOKKU":                                                 "Total intangible assets",
    "NÕUDED JA ETTEMAKSED KOKKU":                                                   "Total receivables and prepayments",
    "KINNISVARAINVESTEERINGUD":                                                     "Investment properties",
    "BIOLOOGILISED VARAD":                                                          "Biological assets",
    "LÜHIAJALISED FINANTSINVESTEERINGUD":                                           "Short-term financial investments",
    "PÕHIVARA KOKKU":                                                               "Total non-current assets",
    "AKTIVA (VARAD) KOKKU":                                                         "Total assets",
    "LAENUKOHUSTUSED KOKKU":                                                        "Total borrowings",
    "Lühiajaline garantiieraldis":                                                  "Short-term warranty provision",
    "Lühiajaline maksueraldis":                                                     "Short-term tax provision",
    "Lühiajalised muud eraldised":                                                  "Short-term other provisions",
    "LÜHIAJALISED ERALDISED":                                                       "Short-term provisions",
    "SIHTFINANTSEERIMINE":                                                          "Targeted financing",
    "Lühiajalised maksuvõlad":                                                      "Short-term tax liabilities",
    "Lühiajalised muud võlad":                                                      "Short-term other payables",
    "Lühiajalised tulevaste perioodide tulud":                                      "Short-term deferred income",
    "Lühiajalised muud saadud ettemaksed":                                          "Short-term other advances received",
    "VÕLAD JA ETTEMAKSED KOKKU":                                                    "Total payables and advances received",
    "Lühiajalised võlad tarnijatele":                                               "Short-term trade payables",
    "Lühiajalised võlad töövõtjatele":                                              "Short-term payables to employees",
    "LÜHIAJALISED KOHUSTUSED KOKKU":                                                "Total current liabilities",
    "PIKAAJALISED LAENUKOHUSTUSED KOKKU":                                           "Total long-term borrowings",
    "Pikaajalised võlad tarnijatele":                                               "Long-term trade payables",
    "Pikaajalised võlad töövõtjatele":                                              "Long-term payables to employees",
    "Pikaajalised maksuvõlad":                                                      "Long-term tax liabilities",
    "Pikaajalised muud võlad":                                                      "Long-term other payables",
    "Pikaajalised tulevaste perioodide tulud":                                      "Long-term deferred income",
    "Pikaajalised muud saadud ettemaksed":                                          "Long-term other advances received",
    "PIKAAJALISED VÕLAD JA ETTEMAKSED":                                             "Long-term payables and advances received",
    "Pikaajaline garantiieraldis":                                                  "Long-term warranty provision",
    "Pikaajaline maksueraldis":                                                     "Long-term tax provision",
    "Pikaajalised muud eraldised":                                                  "Long-term other provisions",
    "PIKAAJALISED ERALDISED":                                                       "Long-term provisions",
    "PIKAAJALISED KOHUSTUSED KOKKU":                                                "Total non-current liabilities",
    "KOHUSTUSED KOKKU":                                                             "Total liabilities",
    "Tooraine ja materjal":                                                         "Raw materials and supplies",
    "AKTSIAKAPITAL VÕI OSAKAPITAL NIMIVÄÄRTUSES VÕI SIHTKAPITAL":                  "Share capital at par value or endowment capital",
    "Lõpetamata toodang":                                                           "Work in progress",
    "ÜLEKURSS":                                                                     "Share premium",
    "Valmistoodang":                                                                "Finished goods",
    "Müügiks ostetud kaubad":                                                       "Goods purchased for resale",
    "Ettemaksed varude eest":                                                       "Prepayments for inventories",
    "Kohustuslik reservkapital":                                                    "Statutory reserve capital",
    "Muud reservid":                                                                "Other reserves",
    "Sissemaksmata osakapital":                                                     "Unpaid share capital",
    "Muu omakapital":                                                               "Other equity",
    "REGISTREERIMATA AKTSIAKAPITAL VÕI OSAKAPITAL":                                "Unregistered share capital",
    "EELMISTE PERIOODIDE JAOTAMATA KASUM (KAHJUM) / AKUMULEERITUD TULEM":          "Retained earnings (deficit) from prior periods",
    "ARUANDEAASTA KASUM (KAHJUM) / TULEM":                                         "Net profit (loss) for the reporting year",
    "OMA OSAD VÕI AKTSIAD (miinus)":                                               "Treasury shares (minus)",
    "VARUD KOKKU":                                                                  "Total inventories",
    "OMAKAPITAL VÕI NETOVARA KOKKU":                                               "Total equity or net assets",
    "Lühiajalised nõuded ostjate vastu":                                            "Short-term trade receivables",
    "Lühiajalised nõuded seotud osapoolte vastu":                                   "Short-term receivables from related parties",
    "Lühiajalised maksude ettemaksed ja tagasinõuded":                              "Short-term tax prepayments and claims",
    "Lühiajalised muud nõuded":                                                     "Short-term other receivables",
    "Lühiajalised ettemaksed":                                                      "Short-term prepayments",
    "PASSIVA (KOHUSTUSED JA OMAKAPITAL VÕI NETOVARA) KOKKU":                       "Total equity and liabilities",
    "MÜÜGIOOTEL PÕHIVARA":                                                         "Non-current assets held for sale",

    # === Balance Sheet (BS) — additional sub-items ===
    "Palgakulu":                                                                    "Wage costs",
    "Sotsiaalmaksud":                                                               "Social taxes",
    "Pensionikulu":                                                                 "Pension costs",
    "Muud":                                                                         "Other",

    # === Income Statement (IS) ===
    "MÜÜGITULU":                                                                    "Revenue",
    "Kaubad, toore, materjal ja teenused":                                          "Goods, raw materials and services",
    "TÖÖJÕU KULUD KOKKU":                                                           "Total labour costs",
    "Põhivara kulum ja väärtuse langus":                                            "Depreciation and impairment of fixed assets",
    "Olulised käibevara allahindlused":                                             "Material write-downs of current assets",
    "Muud ärikulud":                                                                "Other operating expenses",
    "ÄRIKASUM (-KAHJUM)":                                                           "Operating profit (loss)",
    "ÄRIKASUM(-KAHJUM)":                                                            "Operating profit (loss)",
    "Finantstulud ja -kulud tütarettevõtjate aktsiatelt ja osadelt":               "Financial income and expenses from subsidiaries",
    "Finantstulud ja -kulud sidusettevõtjate aktsiatelt ja osadelt":               "Financial income and expenses from associates",
    "Muud finantstulud ja -kulud":                                                  "Other financial income and expenses",
    "Intressitulud":                                                                "Interest income",
    "Valmis- ja lõpetamata toodangu varude jääkide muutus":                        "Change in inventories of finished and work-in-progress goods",
    "Intressikulud":                                                                "Interest expense",
    "Kasum (kahjum) tütar- ja sidusettevõtjatelt":                                 "Profit (loss) from subsidiaries and associates",
    "Kasum (kahjum) finantsinvesteeringutelt":                                      "Profit (loss) from financial investments",
    "KASUM (KAHJUM) ENNE MAKSUSTAMIST":                                             "Profit (loss) before tax",
    "KASUM(KAHJUM) ENNE MAKSUSTAMIST":                                              "Profit (loss) before tax",
    "Tulumaks":                                                                     "Income tax",
    "ARUANDEAASTA PUHASKASUM (-KAHJUM)":                                           "Net profit (loss) for the reporting year",
    "ARUANDEAASTA PUHASKASUM(-KAHJUM)":                                            "Net profit (loss) for the reporting year",
    "Tulu varade sihtfinantseerimisest":                                            "Income from targeted financing of assets",
    "Sihtfinantseerimisega kaetud varade kulum ja väärtuse langus":                "Depreciation of assets covered by targeted financing",
    "Aruandeaasta kasum (kahjum) sihtfinantseerimise netomeetodi korral":          "Profit (loss) under the net method of targeted financing",
    "Kapitaliseeritud väljaminekud oma tarbeks põhivara valmistamisel":            "Capitalised expenditure on self-constructed assets",
    "Muud äritulud":                                                                "Other operating income",
    "Mitmesugused tegevuskulud":                                                    "Miscellaneous operating expenses",
    "Põllumajandusliku toodangu varude jääkide muutus":                            "Change in inventories of agricultural produce",
    "Kasum (kahjum) bioloogilistelt varadelt":                                      "Profit (loss) from biological assets",
}

# ---------------------------------------------------------------------------
# Display name map for table headers
# Keys: English snake_case column base names (without year suffix)
# ---------------------------------------------------------------------------
_DISPLAY_NAMES: Dict[str, str] = {
    # Financial items
    "revenue":                  "Revenue",
    "operating_profit":         "Operating Profit",
    "equity":                   "Equity",
    "depreciation":             "Depreciation & Amortisation",
    "net_profit":               "Net Profit",
    "total_assets":             "Total Assets",
    "avg_employees_fte":        "Avg. Employees (FTE)",
    "cash":                     "Cash",
    "current_liabilities":      "Current Liabilities",
    "long_term_liabilities":    "Long-term Liabilities",
    "current_assets":           "Current Assets",
    "labour_costs":             "Labour Costs",
    # Ratios
    "ebitda":                   "EBITDA",
    "ebitda_margin":            "EBITDA Margin",
    "roe":                      "ROE",
    "roa":                      "ROA",
    "asset_turnover":           "Asset Turnover",
    "employee_efficiency":      "Revenue / Employee",
    "cash_ratio":               "Cash Ratio",
    "current_ratio":            "Current Ratio",
    "debt_to_equity":           "Debt / Equity",
    "labour_ratio":             "Labour Cost Ratio",
    "revenue_growth":           "Revenue Growth",
    "revenue_cagr":             "Revenue CAGR",
    # Identity / enrichment
    "company_name":             "Company",
    "company_code":             "Registry Code",
    "industry_code":            "EMTAK Code",
    "industry_description":     "Industry",
    "industry_combined":        "Industry",
    "company_age_years":        "Age (years)",
    "owner_count":              "Shareholders",
    "top_3_owners":             "Top 3 Owners",
    "top_3_percentages":        "Top 3 Stakes",
    "investment_vehicle":       "Investment Vehicle",
    # Geographic revenue
    "geo_domestic_share":       "Domestic Revenue Share",
    "geo_export_share":         "Export Revenue Share",
    "geo_top_export_market":    "Top Export Market",
    "geo_revenue_countries":    "Revenue Countries",
}


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def translate_dataframe_columns(df: "pd.DataFrame", years: List[int]) -> "pd.DataFrame":
    """
    Rename year-suffixed Estonian financial column names to English equivalents.

    E.g. "Müügitulu_2023" → "revenue_2023", "Ärikasum (kahjum)_2022" → "operating_profit_2022".
    All other columns (ratio names, enrichment columns) are passed through unchanged.

    Args:
        df:    DataFrame from run_company_screening() or similar.
        years: List of years present in the data (e.g. [2023, 2022, 2021]).

    Returns:
        A renamed copy of df — the original is never mutated.
    """
    rename_map: Dict[str, str] = {}
    year_set = {str(y) for y in years}

    for col in df.columns:
        for est, eng in COLUMN_TRANSLATIONS.items():
            suffix = col[len(est):]          # e.g. "_2023"
            if col.startswith(est) and suffix.startswith("_") and suffix[1:] in year_set:
                rename_map[col] = eng + suffix
                break

    return df.rename(columns=rename_map)


def translate_line_names(df: "pd.DataFrame") -> "pd.DataFrame":
    """
    Translate the `line_name` column of a financial statement DataFrame
    from Estonian XBRL labels to English.

    Unrecognised names are left unchanged (graceful degradation).

    Args:
        df: DataFrame with a `line_name` column (output of
            parse_financial_statement_response).

    Returns:
        A copy of df with `line_name` values mapped to English where possible.
    """
    if "line_name" not in df.columns:
        return df.copy()

    result = df.copy()
    result["line_name"] = result["line_name"].map(
        lambda x: LINE_NAME_TRANSLATIONS.get(x, x) if isinstance(x, str) else x
    )
    return result


def get_display_name(col_base: str) -> str:
    """
    Return a human-readable display label for a column base name.

    Args:
        col_base: Column name without year suffix (e.g. "ebitda_margin",
                  "revenue", "company_code").

    Returns:
        Display label string. Falls back to col_base with underscores replaced
        by spaces and title-cased if no mapping is found.
    """
    if col_base in _DISPLAY_NAMES:
        return _DISPLAY_NAMES[col_base]
    # Graceful fallback for unmapped names
    return col_base.replace("_", " ").title()
