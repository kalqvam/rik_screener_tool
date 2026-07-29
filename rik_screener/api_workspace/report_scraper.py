import io
import json
import os
import re
import zipfile
from typing import Optional

import requests
from bs4 import BeautifulSoup

# Lines per synthetic "page" when paginating iXBRL plain text
_IXBRL_LINES_PER_PAGE = 80


def _get_pdf_cache_dir() -> str:
    raw = os.environ.get(
        "RIK_SCREENER_PDF_PATH",
        os.path.join(os.path.expanduser("~"), ".rik_screener", "report_cache"),
    )
    path = os.path.expandvars(raw)
    os.makedirs(path, exist_ok=True)
    return path


def _cache_path(company_code: str, year: int, suffix: str) -> str:
    return os.path.join(_get_pdf_cache_dir(), f"{company_code}_{year}{suffix}")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_file_id(
    company_code: str,
    year: int,
    username: str = None,
    password: str = None,
) -> Optional[str]:
    """Find the RIK file ID for a company's annual report.

    Tries the SOAP document-list API first when credentials are supplied —
    this works for all companies including IFRS filers and JS-rendered pages.
    Falls back to HTML scraping if credentials are not available.
    """
    if username and password:
        file_id = _discover_via_soap(company_code, year, username, password)
        if file_id is not None:
            return file_id
    return _discover_via_html(company_code, year)


def _discover_via_soap(
    company_code: str,
    year: int,
    username: str,
    password: str,
) -> Optional[str]:
    """Query ettevotjaDokumentideLoetelu_v1 and return the file ID for the given year.

    Prefers type A (plain PDF). Falls back to type I (ZIP/ASICE package used
    by ESEF filers such as listed companies).
    """
    import xml.etree.ElementTree as ET
    from xml.sax.saxutils import escape as xml_escape

    envelope = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"'
        ' xmlns:prod="http://arireg.x-road.eu/producer/">'
        "<soapenv:Body>"
        "<prod:ettevotjaDokumentideLoetelu_v1>"
        "<prod:keha>"
        f"<prod:ariregister_kasutajanimi>{xml_escape(username)}</prod:ariregister_kasutajanimi>"
        f"<prod:ariregister_parool>{xml_escape(password)}</prod:ariregister_parool>"
        f"<prod:ariregistri_kood>{xml_escape(company_code)}</prod:ariregistri_kood>"
        "</prod:keha>"
        "</prod:ettevotjaDokumentideLoetelu_v1>"
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    )

    try:
        resp = requests.post(
            "https://ariregxmlv6.rik.ee/",
            data=envelope.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""},
            timeout=30,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception:
        return None

    ns = "http://arireg.x-road.eu/producer/"
    fallback_I = None
    for doc in root.findall(f".//{{{ns}}}ettevotja_dokumendid"):
        liik = doc.findtext(f"{{{ns}}}dokumendi_liik")
        aasta = doc.findtext(f"{{{ns}}}aruandeaasta")
        doc_id = doc.findtext(f"{{{ns}}}dokumendi_id") or None
        if aasta != str(year) or not doc_id:
            continue
        if liik == "A":
            return doc_id          # plain PDF — best option
        if liik == "I" and fallback_I is None:
            fallback_I = doc_id    # ZIP/ASICE package — use if no plain PDF

    return fallback_I


def _discover_via_html(company_code: str, year: int) -> Optional[str]:
    """Fallback: scrape the company HTML page for file links."""
    urls_to_try = [
        f"https://ariregister.rik.ee/est/company/{company_code}",
        f"https://ariregister.rik.ee/est/company/{company_code}/annual-reports",
    ]

    candidates: list[tuple[str, int]] = []
    seen: set[str] = set()

    for url in urls_to_try:
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException:
            continue

        soup = BeautifulSoup(resp.text, "html.parser")

        for link in soup.find_all("a", href=re.compile(rf"/est/company/{company_code}/file/(\d+)")):
            href = link["href"]
            m = re.search(r"/file/(\d+)", href)
            if not m:
                continue
            file_id = m.group(1)
            if file_id in seen:
                continue
            seen.add(file_id)

            container = link.find_parent(["tr", "li", "div"])
            text = container.get_text(" ", strip=True) if container else ""

            if re.search(rf"\b{year}\b", text):
                return file_id
            elif re.search(rf"\b{year + 1}\b", text):
                candidates.append((file_id, 1))
            else:
                candidates.append((file_id, 0))

    if candidates:
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]

    return None


# ---------------------------------------------------------------------------
# Download — handles plain PDF and ZIP/ASICE packages
# ---------------------------------------------------------------------------

def download_pdf(company_code: str, year: int, file_id: str) -> str:
    """Download and cache the annual report for a company/year.

    Returns the path to a usable content file:
    - Plain PDF  → ``{code}_{year}.pdf``
    - ZIP/ASICE package (ESEF) → extracts the best content and returns either
      ``{code}_{year}.pdf`` (if a substantive PDF is inside) or
      ``{code}_{year}.html`` (iXBRL plain-text, if the report is iXBRL-only).

    Call ``parse_toc_from_pdf`` next regardless of which path is returned —
    it handles both extensions.
    """
    pdf_path = _cache_path(company_code, year, ".pdf")
    html_path = _cache_path(company_code, year, ".html")
    if os.path.exists(pdf_path):
        return pdf_path
    if os.path.exists(html_path):
        return html_path

    url = f"https://ariregister.rik.ee/est/company/{company_code}/file/{file_id}"
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Failed to download report for {company_code}/{year}: {e}")

    data = resp.content

    # Detect format by magic bytes
    if data[:2] == b"PK":
        # ZIP/ASICE package — extract content
        return _extract_from_zip(data, company_code, year)
    else:
        # Plain PDF (or unknown — let PyMuPDF sort it out)
        with open(pdf_path, "wb") as f:
            f.write(data)
        return pdf_path


def _collect_from_zip(zip_bytes: bytes) -> tuple:
    """Recursively collect all PDFs and iXBRL files from a ZIP/ASICE hierarchy.

    Returns (pdfs, ixbrls) where each is a list of (filename, bytes) tuples.
    Recurses into nested .asice / .bdoc containers.
    """
    pdfs = []
    ixbrls = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for entry in zf.namelist():
                name_lower = entry.lower()
                # Skip signature metadata
                if name_lower in ("mimetype",) or name_lower.startswith("meta-inf/"):
                    continue
                raw = zf.read(entry)
                if name_lower.endswith((".asice", ".bdoc", ".zip")):
                    sub_pdfs, sub_ixbrls = _collect_from_zip(raw)
                    pdfs.extend(sub_pdfs)
                    ixbrls.extend(sub_ixbrls)
                elif name_lower.endswith(".pdf"):
                    pdfs.append((entry, raw))
                elif name_lower.endswith((".xhtml", ".htm")) and "ixbrl" not in name_lower:
                    ixbrls.append((entry, raw))
                elif name_lower.endswith(".xhtml"):
                    ixbrls.append((entry, raw))
    except (zipfile.BadZipFile, Exception):
        pass
    return pdfs, ixbrls


def _extract_from_zip(zip_bytes: bytes, company_code: str, year: int) -> str:
    """Extract the best content from a ZIP/ASICE package and cache it.

    Strategy:
    1. If any iXBRL/XHTML file is ≥ 500 KB it is the main annual report
       (ESEF filers: the iXBRL is the primary document; PDFs inside are
       supplementary — audit opinion, CSRD assurance, etc.). Strip tags
       and cache as .html.
    2. Otherwise use the largest PDF ≥ 200 KB.
    3. Last resort: any PDF, or the largest iXBRL however small.
    """
    pdfs, ixbrls = _collect_from_zip(zip_bytes)

    pdfs.sort(key=lambda x: len(x[1]), reverse=True)
    ixbrls.sort(key=lambda x: len(x[1]), reverse=True)

    pdf_path = _cache_path(company_code, year, ".pdf")
    html_path = _cache_path(company_code, year, ".html")

    # Large iXBRL = main ESEF report
    if ixbrls and len(ixbrls[0][1]) >= 500_000:
        text = _extract_text_from_ixbrl(ixbrls[0][1])
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(text)
        return html_path

    # Substantive PDF (not just an audit opinion or cover page)
    if pdfs and len(pdfs[0][1]) >= 200_000:
        with open(pdf_path, "wb") as f:
            f.write(pdfs[0][1])
        return pdf_path

    # Small iXBRL
    if ixbrls:
        text = _extract_text_from_ixbrl(ixbrls[0][1])
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(text)
        return html_path

    # Any PDF
    if pdfs:
        with open(pdf_path, "wb") as f:
            f.write(pdfs[0][1])
        return pdf_path

    raise RuntimeError(
        f"ZIP package for {company_code}/{year} contained no usable PDF or iXBRL content."
    )


def _extract_text_from_ixbrl(html_bytes: bytes) -> str:
    """Strip XBRL tags from an iXBRL document and return clean plain text."""
    soup = BeautifulSoup(html_bytes, "html.parser")

    # Drop hidden elements (duplicate tagged values RIK hides from display)
    for tag in soup.find_all(True):
        attrs = tag.attrs if tag.attrs is not None else {}
        style = attrs.get("style", "")
        if "display:none" in style.replace(" ", "") or "display: none" in style:
            tag.decompose()

    # Drop script and style blocks
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()

    return soup.get_text(separator="\n", strip=True)


# ---------------------------------------------------------------------------
# TOC parsing — handles both PDF and plain-text (iXBRL) files
# ---------------------------------------------------------------------------

def parse_toc_from_pdf(pdf_path: str, company_code: str, year: int) -> list:
    """Parse the table of contents from a cached report file.

    Works for both PDF files and plain-text iXBRL files (.html extension).
    """
    if pdf_path.endswith(".html"):
        return _parse_toc_from_ixbrl(pdf_path, company_code, year)
    return _parse_toc_from_pdf(pdf_path, company_code, year)


def _parse_toc_from_pdf(pdf_path: str, company_code: str, year: int) -> list:
    try:
        import fitz
    except ImportError:
        raise RuntimeError("PyMuPDF is not installed. Run: pip install PyMuPDF")

    doc = fitz.open(pdf_path)
    total_pages = doc.page_count

    # Scan up to 5 pages for the TOC heading
    text = ""
    for i in range(min(5, total_pages)):
        text += doc[i].get_text()
    doc.close()

    TOC_HEADINGS = [
        "Sisukord", "SISUKORD",
        "Contents", "CONTENTS",
        "Table of Contents", "TABLE OF CONTENTS",
    ]
    toc_start = -1
    toc_heading_len = 0
    for heading in TOC_HEADINGS:
        idx = text.find(heading)
        if idx != -1:
            toc_start = idx
            toc_heading_len = len(heading)
            break

    if toc_start == -1:
        raise RuntimeError("Could not find table of contents in the first 5 pages of the PDF")

    toc_text = text[toc_start + toc_heading_len:]

    pure_digit = re.compile(r"^\d+$")
    combined_line = re.compile(r"^(.+?)\s+(\d+)\s*$")

    lines = [ln.strip() for ln in toc_text.splitlines() if ln.strip()]
    raw_entries = []
    pending_name = None
    for line in lines:
        if pure_digit.match(line):
            if pending_name is not None:
                raw_entries.append({"name": pending_name, "start_page": int(line)})
                pending_name = None
        else:
            m = combined_line.match(line)
            if m:
                pending_name = None
                raw_entries.append({"name": m.group(1).strip(), "start_page": int(m.group(2))})
            else:
                pending_name = line

    entries = []
    for i, entry in enumerate(raw_entries):
        end_page = raw_entries[i + 1]["start_page"] - 1 if i + 1 < len(raw_entries) else total_pages
        entries.append({
            "name": entry["name"],
            "start_page": entry["start_page"],
            "end_page": end_page,
        })

    toc_path = _cache_path(company_code, year, "_toc.json")
    with open(toc_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    return entries


def _parse_toc_from_ixbrl(html_path: str, company_code: str, year: int) -> list:
    """Build a synthetic TOC from an iXBRL plain-text file.

    The text is paginated into chunks of _IXBRL_LINES_PER_PAGE lines.
    Headings are identified as short lines (≤ 80 chars) that match known
    Estonian/English financial report section names, or that appear to be
    standalone titles in the flow.
    """
    with open(html_path, encoding="utf-8") as f:
        raw_text = f.read()

    all_lines = [ln for ln in raw_text.splitlines()]

    # Persist paginated text for extract_text_from_pages
    pages = []
    for i in range(0, len(all_lines), _IXBRL_LINES_PER_PAGE):
        pages.append("\n".join(all_lines[i: i + _IXBRL_LINES_PER_PAGE]))
    total_pages = len(pages)

    pages_path = _cache_path(company_code, year, "_pages.json")
    with open(pages_path, "w", encoding="utf-8") as f:
        json.dump(pages, f, ensure_ascii=False)

    # Identify heading lines — scan first 60% of doc (TOC-like overview)
    KNOWN_SECTIONS = re.compile(
        r"tegevusaruanne|juhatuse aruanne|kasumiaruanne|bilanss|finantsseisundi|"
        r"rahavoogude|omakapitali muutus|lisad?|lisa \d|audiitori|"
        r"management report|income statement|statement of financial|cash flow|"
        r"equity|notes|auditor",
        re.IGNORECASE,
    )

    raw_entries = []
    scan_lines = min(len(all_lines), int(len(all_lines) * 0.6))
    for line_idx, line in enumerate(all_lines[:scan_lines]):
        stripped = line.strip()
        if not stripped or len(stripped) > 80:
            continue
        if KNOWN_SECTIONS.search(stripped):
            page_num = line_idx // _IXBRL_LINES_PER_PAGE + 1
            raw_entries.append({"name": stripped, "start_page": page_num})

    # Deduplicate consecutive entries on the same page
    seen_pages: set[int] = set()
    deduped = []
    for e in raw_entries:
        key = (e["name"].lower(), e["start_page"])
        if key not in seen_pages:
            seen_pages.add(key)
            deduped.append(e)

    entries = []
    for i, entry in enumerate(deduped):
        end_page = deduped[i + 1]["start_page"] - 1 if i + 1 < len(deduped) else total_pages
        entries.append({
            "name": entry["name"],
            "start_page": entry["start_page"],
            "end_page": max(entry["start_page"], end_page),
        })

    if not entries:
        # Fallback: just return the full document as one section
        entries = [{"name": "Full report (iXBRL)", "start_page": 1, "end_page": total_pages}]

    toc_path = _cache_path(company_code, year, "_toc.json")
    with open(toc_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    return entries


def load_toc(company_code: str, year: int) -> Optional[list]:
    toc_path = _cache_path(company_code, year, "_toc.json")
    if not os.path.exists(toc_path):
        return None
    with open(toc_path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Text extraction — handles both PDF and paginated iXBRL text
# ---------------------------------------------------------------------------

def extract_text_from_pages(company_code: str, year: int, start_page: int, end_page: int) -> str:
    """Return the text for the requested page range.

    Works for both PDF-backed and iXBRL-backed (paginated plain-text) reports.
    """
    # iXBRL path: check for paginated text cache first
    pages_path = _cache_path(company_code, year, "_pages.json")
    if os.path.exists(pages_path):
        with open(pages_path, encoding="utf-8") as f:
            pages = json.load(f)
        start_idx = max(0, start_page - 1)
        end_idx = min(len(pages) - 1, end_page - 1)
        return "\n".join(pages[start_idx: end_idx + 1])

    # PDF path
    try:
        import fitz
    except ImportError:
        raise RuntimeError("PyMuPDF is not installed. Run: pip install PyMuPDF")

    pdf_path = _cache_path(company_code, year, ".pdf")
    if not os.path.exists(pdf_path):
        raise RuntimeError(
            f"Report for company {company_code} year {year} is not cached. "
            f"Call get_annual_report_toc first."
        )

    doc = fitz.open(pdf_path)
    total_pages = doc.page_count
    start_idx = max(0, start_page - 1)
    end_idx = min(total_pages - 1, end_page - 1)

    parts = [doc[i].get_text() for i in range(start_idx, end_idx + 1)]
    doc.close()
    return "\n".join(parts)
