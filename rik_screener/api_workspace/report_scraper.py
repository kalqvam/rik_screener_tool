import json
import os
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup


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


def discover_file_id(company_code: str, year: int) -> Optional[str]:
    url = f"https://ariregister.rik.ee/est/company/{company_code}"
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Failed to fetch company page for {company_code}: {e}")

    soup = BeautifulSoup(resp.text, "html.parser")

    for link in soup.find_all("a", href=re.compile(rf"/est/company/{company_code}/file/(\d+)")):
        href = link["href"]
        file_id_match = re.search(r"/file/(\d+)", href)
        if not file_id_match:
            continue
        file_id = file_id_match.group(1)

        # Walk up the DOM looking for a year label close to this link
        container = link.find_parent(["tr", "li", "div"])
        if container:
            text = container.get_text(" ", strip=True)
            year_match = re.search(rf"\b{year}\b", text)
            if year_match:
                return file_id

    return None


def download_pdf(company_code: str, year: int, file_id: str) -> str:
    pdf_path = _cache_path(company_code, year, ".pdf")
    if os.path.exists(pdf_path):
        return pdf_path

    url = f"https://ariregister.rik.ee/est/company/{company_code}/file/{file_id}"
    try:
        resp = requests.get(url, timeout=60, stream=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Failed to download PDF for {company_code}/{year}: {e}")

    with open(pdf_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)

    return pdf_path


def parse_toc_from_pdf(pdf_path: str, company_code: str, year: int) -> list:
    try:
        import fitz
    except ImportError:
        raise RuntimeError("PyMuPDF is not installed. Run: pip install PyMuPDF")

    doc = fitz.open(pdf_path)
    total_pages = doc.page_count

    # Extract text from pages 1 and 2 (0-indexed: 0 and 1)
    text = ""
    for i in range(min(2, total_pages)):
        text += doc[i].get_text()

    doc.close()

    # Find "Sisukord" heading
    toc_start = text.find("Sisukord")
    if toc_start == -1:
        raise RuntimeError("Could not find 'Sisukord' in the first 2 pages of the PDF")

    toc_text = text[toc_start + len("Sisukord"):]

    # Parse entries — two layouts occur in practice:
    #   Layout A (same line):  "Tegevusaruanne 3"
    #   Layout B (split line): "Tegevusaruanne\n3"
    # Support both by treating a standalone digit line as the page number
    # for the most recently seen name line.
    pure_digit = re.compile(r'^\d+$')
    combined_line = re.compile(r'^(.+?)\s+(\d+)\s*$')

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

    # Compute end pages
    entries = []
    for i, entry in enumerate(raw_entries):
        if i + 1 < len(raw_entries):
            end_page = raw_entries[i + 1]["start_page"] - 1
        else:
            end_page = total_pages
        entries.append({
            "name": entry["name"],
            "start_page": entry["start_page"],
            "end_page": end_page,
        })

    # Cache the parsed ToC
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


def extract_text_from_pages(company_code: str, year: int, start_page: int, end_page: int) -> str:
    try:
        import fitz
    except ImportError:
        raise RuntimeError("PyMuPDF is not installed. Run: pip install PyMuPDF")

    pdf_path = _cache_path(company_code, year, ".pdf")
    if not os.path.exists(pdf_path):
        raise RuntimeError(
            f"PDF for company {company_code} year {year} is not cached. "
            f"Call get_annual_report_toc first."
        )

    doc = fitz.open(pdf_path)
    total_pages = doc.page_count

    # Convert 1-indexed to 0-indexed, clamp to valid range
    start_idx = max(0, start_page - 1)
    end_idx = min(total_pages - 1, end_page - 1)

    parts = []
    for i in range(start_idx, end_idx + 1):
        parts.append(doc[i].get_text())

    doc.close()
    return "\n".join(parts)
