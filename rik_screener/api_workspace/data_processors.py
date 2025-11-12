import pandas as pd
import xml.etree.ElementTree as ET
from typing import List, Optional, Dict, Any, Iterable

STATEMENT_METADATA: Dict[str, Dict[str, List[str]]] = {
    "BS": {
        "primary": ["Bilanss"],
        "alternatives": []
    },
    "IS": {
        "primary": ["Kasumiaruanne skeem 1"],
        "alternatives": ["Kasumiaruanne skeem 2"]
    },
    "CF": {
        "primary": ["Rahavoogude aruanne (kaudne meetod)"],
        "alternatives": ["Rahavoogude aruanne (otsene meetod)"]
    }
}

def _safe_text(element: Optional[ET.Element]) -> Optional[str]:
    if element is None or element.text is None:
        return None
    text = element.text.strip()
    return text if text else None

def parse_annual_reports_response(xml_response: ET.Element, company_code: str) -> Optional[Dict[str, Any]]:
    try:
        ns = {
            'ns1': 'http://arireg.x-road.eu/producer/',
            'soapenv': 'http://schemas.xmlsoap.org/soap/envelope/'
        }
        
        aruanded = xml_response.findall('.//ns1:majandusaasta_aruanded', ns)
        
        if not aruanded:
            return None
        
        first_report = aruanded[0]
        
        aruande_aasta = first_report.find('ns1:aruande_aasta', ns)
        majandusaasta_algus = first_report.find('ns1:majandusaasta_algus', ns)
        majandusaasta_lopp = first_report.find('ns1:majandusaasta_lopp', ns)
        
        return {
            'company_code': company_code,
            'latest_year': aruande_aasta.text if aruande_aasta is not None else None,
            'period_start': majandusaasta_algus.text if majandusaasta_algus is not None else None,
            'period_end': majandusaasta_lopp.text if majandusaasta_lopp is not None else None
        }
        
    except Exception as e:
        print(f"Error parsing annual reports for {company_code}: {e}")
        return None

def parse_company_info_response(xml_response: ET.Element, company_code: str) -> Optional[str]:
    try:
        ns = {
            'ns1': 'http://arireg.x-road.eu/producer/',
            'soapenv': 'http://schemas.xmlsoap.org/soap/envelope/'
        }
        
        evnimi = xml_response.find('.//ns1:ettevotjad/ns1:item/ns1:evnimi', ns)
        
        if evnimi is not None:
            return evnimi.text
        
        return None
        
    except Exception as e:
        print(f"Error parsing company info for {company_code}: {e}")
        return None

def create_latest_reports_dataframe(reports_data: List[Dict[str, Any]], names_data: Optional[Dict[str, str]] = None) -> pd.DataFrame:
    df = pd.DataFrame(reports_data)

    if names_data:
        df['company_name'] = df['company_code'].map(names_data)
        cols = ['company_code', 'company_name', 'latest_year', 'period_start', 'period_end']
        df = df[cols]

    return df

def extract_statement_code(
    xml_response: ET.Element,
    years: Iterable[int],
    statement_type: str
) -> str:
    ns = {
        'ns1': 'http://arireg.x-road.eu/producer/',
        'soapenv': 'http://schemas.xmlsoap.org/soap/envelope/'
    }

    metadata = STATEMENT_METADATA.get(statement_type.upper())
    if metadata is None:
        raise ValueError(f"Unsupported statement type '{statement_type}'")

    entries = xml_response.findall('.//ns1:majandusaasta_aruanded', ns)
    if not entries:
        raise ValueError("No annual reports available for the requested company")

    codes: List[str] = []

    for year in years:
        primary_code: Optional[str] = None
        alternative_code: Optional[str] = None

        for entry in entries:
            entry_year = _safe_text(entry.find('ns1:aruande_aasta', ns))
            entry_name = _safe_text(entry.find('ns1:aruande_nimetus', ns))
            entry_code = _safe_text(entry.find('ns1:aruande_kood', ns))

            if entry_year is None or entry_name is None or entry_code is None:
                continue

            try:
                entry_year_int = int(entry_year)
            except ValueError:
                continue

            if entry_year_int != year:
                continue

            if entry_name in metadata['primary']:
                primary_code = entry_code
                break

            if entry_name in metadata['alternatives']:
                alternative_code = entry_code

        if primary_code is not None:
            codes.append(primary_code)
        elif alternative_code is not None:
            codes.append(alternative_code)
        else:
            raise ValueError(f"No {statement_type.upper()} code found for year {year}")

    unique_codes = set(codes)
    if len(unique_codes) > 1:
        raise ValueError("Change in reporting structure detected across requested years")

    return codes[0]

def parse_financial_statement_response(xml_response: ET.Element) -> pd.DataFrame:
    ns = {
        'ns1': 'http://arireg.x-road.eu/producer/',
        'soapenv': 'http://schemas.xmlsoap.org/soap/envelope/'
    }

    rows = []

    for entry in xml_response.findall('.//ns1:majandusaasta_aruanded_read', ns):
        line_code = _safe_text(entry.find('ns1:rea_nr', ns))
        line_name = _safe_text(entry.find('ns1:rea_nimetus', ns))

        for column in entry.findall('ns1:majandusaasta_aruanded_veerud', ns):
            column_code = _safe_text(column.find('ns1:veeru_kood', ns))
            column_name = _safe_text(column.find('ns1:veeru_nimetus', ns))
            value_text = _safe_text(column.find('ns1:vaartus', ns))

            if line_code is None or column_code is None:
                continue

            value = pd.to_numeric(value_text, errors='coerce') if value_text is not None else None

            rows.append({
                'line_code': line_code,
                'line_name': line_name,
                'column_code': column_code,
                'column_name': column_name,
                'value': value
            })

    if not rows:
        return pd.DataFrame(columns=['line_code', 'line_name'])

    df = pd.DataFrame(rows)

    pivot = df.pivot_table(
        index=['line_code', 'line_name'],
        columns='column_code',
        values='value',
        aggfunc='first'
    )

    pivot = pivot.sort_index(axis=1)
    pivot.columns = pivot.columns.astype(str)
    pivot.reset_index(inplace=True)

    return pivot

def parse_statement_codes_by_year(
    xml_response: ET.Element,
    target_year: int,
    end_year: int,
    statement_types: List[str]
) -> Dict[str, List[Optional[str]]]:
    """
    Parse statement codes for multiple years and statement types using substring matching.

    Matching rules (case-insensitive):
    - BS: statement name contains "bilanss"
    - IS: statement name contains "kasum"
    - CF: statement name contains "raha"

    Args:
        xml_response: XML response from get_annual_reports_list
        target_year: Starting year
        end_year: Ending year
        statement_types: List of statement types (e.g., ["BS", "IS", "CF"])

    Returns:
        Dictionary with statement_type as key and list of codes (ordered from target_year to end_year) as value
    """
    ns = {
        'ns1': 'http://arireg.x-road.eu/producer/',
        'soapenv': 'http://schemas.xmlsoap.org/soap/envelope/'
    }

    entries = xml_response.findall('.//ns1:majandusaasta_aruanded', ns)
    if not entries:
        return {st: [] for st in statement_types}

    # Build a mapping: (year, statement_type) -> code
    year_type_codes: Dict[tuple, Optional[str]] = {}

    for entry in entries:
        entry_year = _safe_text(entry.find('ns1:aruande_aasta', ns))
        entry_name = _safe_text(entry.find('ns1:aruande_nimetus', ns))
        entry_code = _safe_text(entry.find('ns1:aruande_kood', ns))

        if entry_year is None or entry_name is None or entry_code is None:
            continue

        try:
            entry_year_int = int(entry_year)
        except ValueError:
            continue

        # Skip years outside the range
        if entry_year_int < end_year or entry_year_int > target_year:
            continue

        # Determine statement type based on name using substring matching
        entry_name_lower = entry_name.lower()

        # Mapping of statement types to search substrings (case-insensitive)
        statement_search_map = {
            'BS': 'bilanss',
            'IS': 'kasum',
            'CF': 'raha'
        }

        for statement_type in statement_types:
            st_upper = statement_type.upper()
            search_substring = statement_search_map.get(st_upper)

            if search_substring is None:
                continue

            # Check if the search substring is in the entry name (case-insensitive)
            if search_substring in entry_name_lower:
                key = (entry_year_int, st_upper)
                # Only store if not already stored (prioritize first match)
                if key not in year_type_codes:
                    year_type_codes[key] = entry_code

    # Build result: list of codes ordered from target_year to end_year
    result = {}

    for statement_type in statement_types:
        st_upper = statement_type.upper()
        codes = []

        # Iterate from target_year down to end_year
        for year in range(target_year, end_year - 1, -1):
            key = (year, st_upper)
            code = year_type_codes.get(key)
            codes.append(code)

        result[st_upper] = codes

    return result
