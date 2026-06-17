import xml.etree.ElementTree as ET
from typing import Optional, Dict, Any
from .soap_client import SOAPClient

def get_annual_reports_list(company_code: str) -> Optional[ET.Element]:
    client = SOAPClient()
    params = {"ariregistri_kood": company_code}
    return client.call_endpoint("majandusaastaAruanneteLoetelu_v1", params)

def get_company_basic_info(company_code: str) -> Optional[ET.Element]:
    client = SOAPClient()
    params = {"ariregistri_kood": company_code}
    return client.call_endpoint("lihtandmed_v2", params)

def get_beneficial_owners(company_code: str, active_only: bool = True) -> Optional[ET.Element]:
    """Fetch beneficial owners (tegelikud kasusaajad) for a company (tegelikudKasusaajad_v2)."""
    client = SOAPClient()
    params = {
        "ariregistri_kood": company_code,
        "ainult_kehtivad": "1" if active_only else "0",
        "keel": "eng",
    }
    return client.call_endpoint("tegelikudKasusaajad_v2", params)


def get_representation_rights(company_code: str) -> Optional[ET.Element]:
    """Fetch rights of representation for all persons related to a company (esindus_v1)."""
    client = SOAPClient()
    params = {
        "ariregistri_kood": company_code,
        "keel": "eng",
    }
    return client.call_endpoint("esindus_v1", params)


def get_financial_statement_details(
    company_code: str,
    statement_code: str,
    year: int
) -> Optional[ET.Element]:
    """Retrieve a specific financial statement for a company and year."""

    client = SOAPClient()
    params = {
        "ariregistri_kood": company_code,
        "aruande_liik": statement_code,
        "aruandeaasta": str(year)
    }
    return client.call_endpoint("majandusaastaAruanneteKirjed_v1", params)
