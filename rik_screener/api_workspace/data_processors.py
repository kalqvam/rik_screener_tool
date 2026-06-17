import pandas as pd
import xml.etree.ElementTree as ET
from typing import List, Optional, Dict, Any, Iterable
from ..utils import log_error

STATEMENT_SEARCH_MAP: Dict[str, str] = {
    'BS': 'bilanss',
    'IS': 'kasum',
    'CF': 'raha',
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
        
    except (ET.ParseError, AttributeError, KeyError) as e:
        log_error(f"Error parsing annual reports for {company_code}: {e}")
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
        
    except (ET.ParseError, AttributeError, KeyError) as e:
        log_error(f"Error parsing company info for {company_code}: {e}")
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

    search_substring = STATEMENT_SEARCH_MAP.get(statement_type.upper())
    if search_substring is None:
        raise ValueError(f"Unsupported statement type '{statement_type}'")

    entries = xml_response.findall('.//ns1:majandusaasta_aruanded', ns)
    if not entries:
        raise ValueError("No annual reports available for the requested company")

    codes: List[str] = []

    for year in years:
        matched_code: Optional[str] = None

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

            if search_substring in entry_name.lower():
                matched_code = entry_code
                break

        if matched_code is not None:
            codes.append(matched_code)
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

def parse_beneficial_owners_response(
    xml_response: ET.Element,
    company_code: str,
) -> Optional[Dict[str, Any]]:
    """
    Parse the tegelikudKasusaajad_v2 response for a single company.

    Returns a dict with summary counts and a list of beneficial owner dicts, or None on error.
    """
    try:
        ns = {
            "ns1": "http://arireg.x-road.eu/producer/",
            "soapenv": "http://schemas.xmlsoap.org/soap/envelope/",
        }

        kasusaajad_el = xml_response.find(".//ns1:kasusaajad", ns)
        if kasusaajad_el is None:
            return None

        def _bool(el) -> Optional[bool]:
            t = _safe_text(el)
            if t is None:
                return None
            t_lower = t.lower().strip()
            if t_lower in ("true", "1", "jah", "yes"):
                return True
            if t_lower in ("false", "0", "ei", "no"):
                return False
            return None  # unrecognised value → unknown rather than silently False

        total = _safe_text(kasusaajad_el.find("ns1:kasusaajate_arv_kokku", ns))
        hidden = _safe_text(kasusaajad_el.find("ns1:peidetud_kasusaajate_arv", ns))
        discrepancy_absence = _bool(kasusaajad_el.find("ns1:lahknevusteade_puudumisest", ns))

        obliged_el = xml_response.find(".//ns1:esitab_kasusaajad", ns)
        obliged = _bool(obliged_el)

        owners = []
        for owner_el in kasusaajad_el.findall("ns1:kasusaaja", ns):
            discrepancy_raw = _bool(owner_el.find("ns1:lahknevusteade_esitatud", ns))
            start_raw = _safe_text(owner_el.find("ns1:algus_kpv", ns))
            end_raw = _safe_text(owner_el.find("ns1:lopp_kpv", ns))
            owners.append({
                "first_name":       _safe_text(owner_el.find("ns1:eesnimi", ns)),
                "last_name":        _safe_text(owner_el.find("ns1:nimi", ns)),
                "personal_code":    _safe_text(owner_el.find("ns1:isikukood", ns)),
                "foreign_id":       _safe_text(owner_el.find("ns1:välisriigi_isikukood", ns)),
                "country_code":     _safe_text(owner_el.find("ns1:aadress_riik", ns)),
                "country":          _safe_text(owner_el.find("ns1:aadress_riik_tekstina", ns)),
                "control_code":     _safe_text(owner_el.find("ns1:kontrolli_teostamise_viis", ns)),
                "control_method":   _safe_text(owner_el.find("ns1:kontrolli_teostamise_viis_tekstina", ns)),
                "start_date":       start_raw.rstrip("Z") if start_raw else None,
                "end_date":         end_raw.rstrip("Z") if end_raw else None,
                "end_type":         _safe_text(owner_el.find("ns1:lopetamise_liik_tekstina", ns)),
                "discrepancy_note": discrepancy_raw,
            })

        return {
            "company_code":           company_code,
            "obliged_to_report":      obliged,
            "total_owners":           int(total) if total and total.isdigit() else None,
            "hidden_owners":          int(hidden) if hidden and hidden.isdigit() else None,
            "discrepancy_on_absence": discrepancy_absence,
            "owners":                 owners,
        }

    except (ET.ParseError, AttributeError, KeyError) as e:
        log_error(f"Error parsing beneficial owners for {company_code}: {e}")
        return None


def parse_representation_rights_response(
    xml_response: ET.Element,
    company_code: str,
) -> Optional[Dict[str, Any]]:
    """
    Parse the esindus_v1 response for a single company.

    Returns a dict with company metadata and a list of person dicts, or None on error.
    """
    try:
        ns = {
            "ns1": "http://arireg.x-road.eu/producer/",
            "soapenv": "http://schemas.xmlsoap.org/soap/envelope/",
        }

        company_el = xml_response.find(".//ns1:ettevotjad/ns1:item", ns)
        if company_el is None:
            return None

        persons = []
        for person_el in company_el.findall("ns1:isikud/ns1:item", ns):
            exclusive_raw = _safe_text(person_el.find("ns1:ainuesindusoigus_olemas", ns))
            persons.append({
                "first_name":              _safe_text(person_el.find("ns1:fyysilise_isiku_eesnimi", ns)),
                "last_name":               _safe_text(person_el.find("ns1:fyysilise_isiku_perenimi", ns)),
                "personal_code":           _safe_text(person_el.find("ns1:fyysilise_isiku_kood", ns)),
                "country_code":            _safe_text(person_el.find("ns1:isikukood_riik", ns)),
                "country":                 _safe_text(person_el.find("ns1:isikukoodi_riik_tekstina", ns)),
                "role_code":               _safe_text(person_el.find("ns1:fyysilise_isiku_roll", ns)),
                "role":                    _safe_text(person_el.find("ns1:fyysilise_isiku_roll_tekstina", ns)),
                "exclusive_representation": "Yes" if exclusive_raw == "JAH" else "No" if exclusive_raw == "EI" else exclusive_raw,
            })

        exceptions_el = company_el.find("ns1:esindusoiguse_eritingimused", ns)
        exceptions = _safe_text(exceptions_el) if exceptions_el is not None else None

        return {
            "company_code":    _safe_text(company_el.find("ns1:ariregistri_kood", ns)) or company_code,
            "company_name":    _safe_text(company_el.find("ns1:arinimi", ns)),
            "status":          _safe_text(company_el.find("ns1:staatus_tekstina", ns)),
            "legal_form":      _safe_text(company_el.find("ns1:oiguslik_vorm_tekstina", ns)),
            "exceptions":      exceptions,
            "persons":         persons,
        }

    except (ET.ParseError, AttributeError, KeyError) as e:
        log_error(f"Error parsing representation rights for {company_code}: {e}")
        return None


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

        for statement_type in statement_types:
            st_upper = statement_type.upper()
            search_substring = STATEMENT_SEARCH_MAP.get(st_upper)

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

def parse_consolidation_status_by_year(
    xml_response: ET.Element,
    target_year: int,
    end_year: int
) -> Dict[int, bool]:
    """
    Parse consolidation status for each year by checking if "konsolid" appears in any statement name.

    Args:
        xml_response: XML response from get_annual_reports_list
        target_year: Starting year (most recent)
        end_year: Ending year (oldest)

    Returns:
        Dictionary with year as key and boolean as value (True if "konsolid" found in any statement)
    """
    ns = {
        'ns1': 'http://arireg.x-road.eu/producer/',
        'soapenv': 'http://schemas.xmlsoap.org/soap/envelope/'
    }

    entries = xml_response.findall('.//ns1:majandusaasta_aruanded', ns)
    if not entries:
        return {}

    # Track which years have consolidated statements
    consolidation_by_year: Dict[int, bool] = {}

    for entry in entries:
        entry_year = _safe_text(entry.find('ns1:aruande_aasta', ns))
        entry_name = _safe_text(entry.find('ns1:aruande_nimetus', ns))

        if entry_year is None or entry_name is None:
            continue

        try:
            entry_year_int = int(entry_year)
        except ValueError:
            continue

        # Skip years outside the range
        if entry_year_int < end_year or entry_year_int > target_year:
            continue

        # Check if "konsolid" appears in the statement name (case-insensitive)
        entry_name_lower = entry_name.lower()
        if 'konsolid' in entry_name_lower:
            consolidation_by_year[entry_year_int] = True
        else:
            # Only set to False if not already set to True
            if entry_year_int not in consolidation_by_year:
                consolidation_by_year[entry_year_int] = False

    return consolidation_by_year
