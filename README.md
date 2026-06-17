# RIK Screener

A Python package for screening and analyzing Estonian companies using open data from the Estonian Business Register (RIK) and the Estonian Tax Authority (EMTA).

## Overview

RIK Screener processes large open-data CSV files published by RIK and EMTA to produce ranked company lists based on financial ratios, industry classifications, ownership structures, geography, and turnover growth. It is designed for financial analysts, investors, and researchers working with Estonian company data.

Two independent screening pipelines are available:

- **RIK pipeline** — annual financial statements (balance sheet, income statement, key indicators). Multi-year support, ratio calculations, ownership and geography enrichment.
- **EMTA pipeline** — quarterly VAT-turnover declarations. Screens companies by year-over-year turnover growth, independent of the RIK data.

Both pipelines can be used programmatically, via a config-driven workflow runner, or through an MCP server that exposes them as tools to Claude.

---

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Data Files](#data-files)
  - [Downloading Data](#downloading-data)
  - [RIK Files](#rik-files)
  - [EMTA Files](#emta-files)
- [Environment Setup](#environment-setup)
- [Usage](#usage)
  - [RIK Company Screening](#rik-company-screening)
  - [EMTA Turnover Screening](#emta-turnover-screening)
  - [MCP Server](#mcp-server)
  - [Live RIK API](#live-rik-api)
- [Configuration Reference](#configuration-reference)
- [License](#license)

---

## Features

- **Multi-year financial analysis** — merge and compare annual report data across any set of years
- **Financial ratio engine** — built-in formulas for EBITDA margin, ROE, ROA, D/E, current ratio, revenue CAGR, employee efficiency, and more
- **Custom formula engine** — define arbitrary expressions over any column in the dataset
- **Industry classification** — EMTAK code mapping, industry descriptions, and revenue distribution by sector
- **Ownership analysis** — shareholder structure and concentration from the RIK shareholder registry
- **Geographic revenue** — export share and country-level revenue breakdown
- **Company age filtering** — filter by registration date ranges
- **EMTA turnover screening** — quarterly YoY turnover growth as a volume-trend signal, completely separate from RIK annual data
- **MCP server** — expose both screeners as tools to Claude via the Model Context Protocol
- **Live RIK API** — fetch annual report PDFs directly from the RIK API and extract structured financial data (requires RIK credentials)
- **Automated downloader** — `download_data.py` script to fetch and update all open data files on demand

---

## Requirements

- Python 3.10+
- pandas, numpy, requests, PyMuPDF, beautifulsoup4 (see `pyproject.toml`)
- For MCP server: `pip install rik-screener[mcp]`

---

## Installation

```bash
pip install rik-screener
```

For MCP server support:

```bash
pip install "rik-screener[mcp]"
```

For development:

```bash
git clone https://github.com/kalqvam/rik_screener_tool.git
cd rik_screener_tool
pip install -e ".[dev,mcp]"
```

---

## Data Files

The screener reads large open-data CSV files that are **not included in this repository**. They must be downloaded separately and placed in a local data folder.

### Downloading Data

The included `download_data.py` script handles downloading and extracting all required files:

```bash
# Download everything (RIK + EMTA), default years 2023–2025
python download_data.py --all --target /path/to/data

# Download only EMTA files
python download_data.py --emta --target /path/to/data

# Download RIK files for specific years
python download_data.py --rik --years 2021 2022 2023 2024 2025 --target /path/to/data

# Force re-download even if files already exist
python download_data.py --all --force --target /path/to/data
```

The script scrapes the RIK download page on each run to find the current dated ZIP URLs (filenames include a monthly update date, e.g. `kuni_31052026`), downloads and extracts them, and automatically removes outdated versions.

### RIK Files

Source: [avaandmed.ariregister.rik.ee](https://avaandmed.ariregister.rik.ee/et/avaandmete-allalaadimine)

| File (on disk after extraction) | Contents |
|---|---|
| `ettevotja_rekvisiidid__lihtandmed.csv` | Company names, registration codes, legal forms, status |
| `ettevotja_rekvisiidid__osanikud.json` | Shareholder registry |
| `1.aruannete_yldandmed_kuni_*.csv` | Annual report general data (filing dates, auditor, etc.) |
| `2.EMTAK_myygitulu_kuni_*.csv` | Revenue breakdown by EMTAK industry code |
| `3.myygitulu_geograafiline_kuni_*.csv` | Geographic revenue distribution |
| `4.{year}_aruannete_elemendid_kuni_*.csv` | Key financial indicators per year (one file per year) |

Files use semicolons as separators and Estonian column names. The screener resolves all glob-style filenames automatically.

### EMTA Files

Source: [emta.ee statistics & open data](https://www.emta.ee/eraklient/amet-uudised-ja-kontakt/uudised-pressiinfo-statistika/statistika-ja-avaandmed)

| File | Contents |
|---|---|
| `tasutud_maksud_kaesolev_aasta.csv` | Current year quarterly data |
| `tasutud_maksud_varasemad_aastad.csv` | Historical quarterly data (2022–prior year) |

**Important caveats about EMTA turnover (`käive`):**
- It is the sum of VAT declaration lines 1–3, **not** company revenue. It includes reverse-charge VAT purchases in addition to the company's own sales.
- Filings are 1-month lagged: the Q1 label covers December–February activity.
- Absolute values are not comparable to RIK income statement revenue.
- The useful signal is **YoY growth of the same quarter** — a reliable indicator of volume trends.

---

## Environment Setup

Set the `RIK_SCREENER_PATH` environment variable to the folder containing your data files:

```bash
# Linux / macOS
export RIK_SCREENER_PATH=/path/to/data

# Windows (PowerShell)
$env:RIK_SCREENER_PATH = "C:\path\to\data"
```

Or set it programmatically:

```python
import rik_screener
rik_screener.set_base_path('/path/to/data')
```

For the live RIK API, you also need RIK credentials (see [Live RIK API](#live-rik-api)). Copy `credentials.txt` to your data folder or project root and fill in your details — the file is gitignored and will not be committed.

---

## Usage

### RIK Company Screening

The main entry point is `run_company_screening(config)`, which runs the full pipeline from data loading through ratio calculation, enrichment, and filtering.

```python
import os
import rik_screener

os.environ['RIK_SCREENER_PATH'] = '/path/to/data'

config = {
    # Which annual report years to load and merge
    'years': [2024, 2023, 2022],

    # Filter to specific legal forms (optional; omit to include all)
    'legal_forms': ['AS', 'OÜ'],

    # Built-in financial ratios to calculate
    'standard_formulas': {
        'ebitda_margin':  {'years': [2024, 2023]},
        'roe':            {'years': [2024]},
        'debt_to_equity': {'years': [2024]},
        'revenue_growth': {'year_pairs': [[2023, 2024]]},
    },

    # Filters applied after ratio calculation
    'financial_filters': [
        {'column': 'ebitda_margin_2024', 'min': 0.05},
        {'column': 'revenue_growth_2023_to_2024', 'min': 0.0},
    ],

    'sort_column': 'ebitda_margin_2024',
    'top_n': 100,
    'output_file': 'results.csv',   # optional; omit to return DataFrame only
}

results = rik_screener.run_company_screening(config)
print(results.head())
```

#### Skipping pipeline steps

```python
config['skip_steps'] = ['ownership', 'geography']  # skip slow enrichment steps
```

Valid values: `industry`, `age`, `emtak`, `ownership`, `geography`.

#### Custom formulas

```python
config['custom_formulas'] = {
    'asset_efficiency': '"Müügitulu_2024" / "Varad_2024"',
    'margin_delta': 'abs("ebitda_margin_2024" - "ebitda_margin_2023")',
}
config['financial_filters'] = [
    {'column': 'asset_efficiency', 'min': 1.5},
]
```

#### Industry and geography filters

```python
config['industry_codes_filter'] = ['62', '63']   # IT sector EMTAK codes

config['geography_filters'] = {
    'min_export_share': 0.3,          # at least 30% revenue from exports
    'export_countries': ['FI', 'SE'],  # must have revenue in these countries
}
```

---

### EMTA Turnover Screening

The EMTA screener is fully independent of the RIK pipeline. It reads the EMTA quarterly files and screens companies by turnover growth.

```python
import os
from rik_screener import run_emta_screening

config = {
    'data_path': '/path/to/data',

    # Filters
    'min_turnover_yoy': 0.20,     # at least 20% YoY growth
    'max_turnover_yoy': 10.0,     # cap outliers
    'min_turnover': 100_000,      # minimum current-quarter turnover in EUR

    # Narrow by industry keyword (matched against EMTA industry description)
    'industry_keyword': 'ehitus',  # construction

    # Narrow by region (maakond)
    'region': 'Harju',

    # Or pass a specific list of registration codes
    # 'company_codes': ['12345678', '87654321'],

    'top_n': 50,
    'sort_by': 'turnover_yoy',
    'sort_ascending': False,
}

results = run_emta_screening(config)
print(results[['company_name', 'turnover_current', 'turnover_yoy', 'period']])
```

Output columns: `company_code`, `company_name`, `region`, `industry`, `company_type`, `turnover_current`, `turnover_prior`, `turnover_yoy`, `employees`, `period`, `period_prior`.

The screener automatically selects the most recent quarter with sufficient data coverage (≥10% of companies reporting) and compares it against the same quarter one year prior.

---

### MCP Server

The MCP server exposes both screeners as tools to Claude. It is useful for interactive analysis sessions where you want to query the data conversationally.

#### Setup

1. Install with MCP support: `pip install "rik-screener[mcp]"`
2. Copy `.mcp.json.example` to `.mcp.json` and fill in your paths:

```json
{
  "mcpServers": {
    "rik-screener": {
      "command": "python",
      "args": ["-m", "rik_screener.mcp_server"],
      "cwd": "/path/to/rik_screener_tool",
      "env": {
        "RIK_SCREENER_PATH": "/path/to/your/data/folder",
        "RIK_USERNAME": "${RIK_USERNAME}",
        "RIK_PASSWORD": "${RIK_PASSWORD}"
      }
    }
  }
}
```

3. In Claude Code, place `.mcp.json` in your project root or `~/.claude/`. Claude will detect the server automatically.

#### Available MCP tools

| Tool | Description |
|---|---|
| `screen_companies` | Run the full RIK screening pipeline with any supported config options |
| `screen_emta` | Run the EMTA quarterly turnover screener |
| `get_financial_statements` | Fetch annual report data for a company via the live RIK API |
| `search_companies` | Search for companies by name or registration code |

---

### Live RIK API

The live API fetches annual report PDFs directly from the RIK registry and extracts structured financial data. It requires RIK credentials.

#### Credentials

Fill in `credentials.txt` (gitignored) with your RIK login:

```
RIK_USERNAME=your_username_here
RIK_PASSWORD=your_password_here
```

Or set them as environment variables:

```bash
export RIK_USERNAME=your_username
export RIK_PASSWORD=your_password
```

You can register for RIK access at [ariregister.rik.ee](https://ariregister.rik.ee/est/register).

#### Usage

```python
from rik_screener.api_workspace import get_financial_statements

# Fetch the latest available annual report for a company
data = get_financial_statements(
    company_code='12417834',
    username='your_username',
    password='your_password',
)
print(data)
```

PDF reports are cached locally in `~/.rik_screener/report_cache/` (or the path set via `RIK_SCREENER_PDF_PATH` environment variable) to avoid repeated downloads.

---

## Configuration Reference

### Core RIK config keys

| Key | Type | Description |
|---|---|---|
| `years` | `list[int]` | **Required.** Annual report years to load, e.g. `[2024, 2023]` |
| `legal_forms` | `list[str]` | Filter to `["AS"]`, `["OÜ"]`, or both. Default: all. |
| `skip_steps` | `list[str]` | Pipeline steps to skip: `industry`, `age`, `emtak`, `ownership`, `geography` |
| `use_dataframe_pipeline` | `bool` | Use in-memory DataFrame pipeline (default `True`) vs CSV file pipeline |
| `standard_formulas` | `dict` | Built-in ratio definitions — see below |
| `custom_formulas` | `dict` | Custom expression formulas keyed by output column name |
| `financial_filters` | `list[dict]` | Column-based filters with `column`, `min`, `max` keys |
| `ownership_filters` | `dict` | Shareholder concentration filters |
| `industry_codes_filter` | `list[str]` | EMTAK code prefixes to include |
| `geography_filters` | `dict` | Export share and country filters |
| `sort_column` | `str` | Column to sort results by |
| `top_n` | `int` | Maximum rows to return |
| `output_file` | `str` | Optional CSV output path |

### Standard formulas

| Formula type | Config |
|---|---|
| `ebitda_margin` | `{'years': [2024]}` |
| `ebitda` | `{'years': [2024]}` |
| `roe` | `{'years': [2024], 'use_averages': True}` |
| `roa` | `{'years': [2024], 'use_averages': True}` |
| `asset_turnover` | `{'years': [2024], 'use_averages': True}` |
| `employee_efficiency` | `{'years': [2024]}` |
| `cash_ratio` | `{'years': [2024]}` |
| `current_ratio` | `{'years': [2024]}` |
| `debt_to_equity` | `{'years': [2024]}` |
| `labour_ratio` | `{'years': [2024]}` |
| `revenue_growth` | `{'year_pairs': [[2023, 2024]]}` |
| `revenue_cagr` | `{'start_year': 2020, 'end_year': 2024}` |

### EMTA config keys

| Key | Type | Description |
|---|---|---|
| `data_path` | `str` | Folder containing EMTA CSV files. Falls back to `RIK_SCREENER_PATH`. |
| `emta_current_file` | `str` | Filename for current-year data. Default: `tasutud_maksud_kaesolev_aasta.csv` |
| `emta_historical_file` | `str` | Filename for historical data. Default: `tasutud_maksud_varasemad_aastad.csv` |
| `min_coverage_ratio` | `float` | Minimum fraction of companies with data for a quarter to be selected as reference. Default: `0.10` |
| `min_turnover_yoy` | `float` | Minimum YoY growth (e.g. `0.2` = 20%) |
| `max_turnover_yoy` | `float` | Maximum YoY growth — caps outliers |
| `min_turnover` | `float` | Minimum current-quarter turnover in EUR |
| `industry_keyword` | `str` | Case-insensitive substring match against EMTA industry description |
| `region` | `str` | Substring match against `maakond` (county) |
| `company_codes` | `list[str]` | Restrict to specific registration codes |
| `top_n` | `int` | Rows to return. Default: `50` |
| `sort_by` | `str` | Column to sort by. Default: `turnover_yoy` |
| `sort_ascending` | `bool` | Sort direction. Default: `False` |

---

## License

MIT License — see [LICENSE](LICENSE) for details.
