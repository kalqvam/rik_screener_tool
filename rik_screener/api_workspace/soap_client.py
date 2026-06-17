import time
import requests
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from typing import Dict, Optional
from .config_auth import get_api_config
from ..utils import log_info, log_warning, log_error

MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2


class SOAPClient:
    def __init__(self):
        self.config = get_api_config()
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'text/xml; charset=utf-8',
            'SOAPAction': ''
        })

    def build_envelope(self, operation: str, body_content: str) -> str:
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:xro="http://x-road.eu/xsd/xroad.xsd"
                  xmlns:iden="http://x-road.eu/xsd/identifiers"
                  xmlns:prod="http://arireg.x-road.eu/producer/">
    <soapenv:Body>
        <prod:{operation}>
            <prod:keha>
                <prod:ariregister_kasutajanimi>{self.config.username}</prod:ariregister_kasutajanimi>
                <prod:ariregister_parool>{self.config.password}</prod:ariregister_parool>
                {body_content}
            </prod:keha>
        </prod:{operation}>
    </soapenv:Body>
</soapenv:Envelope>'''

    def send_request(self, envelope: str) -> Optional[ET.Element]:
        self.config.wait_for_rate_limit()

        log_info(f"SOAP Request URL: {self.config.base_url}")

        last_exception = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self.session.post(
                    self.config.base_url,
                    data=envelope.encode('utf-8'),
                    timeout=30
                )

                log_info(f"Response Status: {response.status_code}")

                response.raise_for_status()

                root = ET.fromstring(response.content)
                fault = root.find('.//{http://schemas.xmlsoap.org/soap/envelope/}Fault')
                if fault is not None:
                    faultstring = fault.findtext('faultstring', 'Unknown SOAP fault')
                    log_error(f"SOAP fault: {faultstring}")
                    return None
                return root

            except requests.RequestException as e:
                last_exception = e
                if attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF_BASE ** attempt
                    log_warning(f"Request failed (attempt {attempt}/{MAX_RETRIES}): {e}. Retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    log_error(f"Request failed after {MAX_RETRIES} attempts: {e}")
                    return None
            except ET.ParseError as e:
                log_error(f"XML parsing failed: {e}")
                return None

    def call_endpoint(self, operation: str, params: Dict[str, str]) -> Optional[ET.Element]:
        body_parts = []
        for key, value in params.items():
            body_parts.append(f"<prod:{key}>{escape(str(value))}</prod:{key}>")

        body_content = "\n                ".join(body_parts)
        envelope = self.build_envelope(operation, body_content)

        return self.send_request(envelope)
