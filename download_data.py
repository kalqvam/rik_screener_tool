"""
Download script for RIK screener data files.

Usage:
    python download_data.py --all --target ./CSV
    python download_data.py --emta --target ./CSV
    python download_data.py --rik --years 2023 2024 2025 --target ./CSV
    python download_data.py --all --target ./CSV --force

Not run by default — call explicitly when you want to fetch or refresh data.
"""

import argparse
import os
import re
import sys
import zipfile
import glob
from urllib.request import urlretrieve
from urllib.error import URLError, HTTPError

import requests

# ---------------------------------------------------------------------------
# EMTA — stable Nextcloud share links, direct CSV downloads
# ---------------------------------------------------------------------------
EMTA_FILES = {
    "tasutud_maksud_kaesolev_aasta.csv":
        "https://ncfailid.emta.ee/s/DFHQjB2Rsq3CK7p/download/tasutud_maksud_kaesolev_aasta.csv",
    "tasutud_maksud_varasemad_aastad.csv":
        "https://ncfailid.emta.ee/s/bCszrta8THHA9xn/download/tasutud_maksud_varasemad_aastad.csv",
}

# ---------------------------------------------------------------------------
# RIK — ZIP downloads; filenames contain a date suffix that changes monthly.
#        We scrape the download page to find current URLs.
# ---------------------------------------------------------------------------
RIK_DOWNLOAD_PAGE = "https://avaandmed.ariregister.rik.ee/et/avaandmete-allalaadimine"
RIK_BASE_URL = "https://avaandmed.ariregister.rik.ee"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "et,en;q=0.9",
}

# Patterns to match on the RIK page (relative path substrings)
RIK_FILE_PATTERNS = {
    "lihtandmed_csv":    r"/sites/default/files/avaandmed/ettevotja_rekvisiidid__lihtandmed\.csv\.zip",
    "osanikud_json":     r"/sites/default/files/avaandmed/ettevotja_rekvisiidid__osanikud\.json\.zip",
    "aruannete_yld":     r"/sites/default/files/1\.aruannete_yldandmed_kuni_\d+\.zip",
    "emtak_myygitulu":   r"/sites/default/files/2\.EMTAK_myygitulu_kuni_\d+\.zip",
    "geo_myygitulu":     r"/sites/default/files/3\.myygitulu_geograafiline_kuni_\d+\.zip",
}

# Per-year financial data pattern — one ZIP per year
RIK_YEAR_PATTERN = r"/sites/default/files/4\.{year}_aruannete_elemendid_kuni_\d+\.zip"

# Glob patterns for files already on disk (screener uses the same patterns)
RIK_GLOB_PATTERNS = {
    "lihtandmed_csv":  "ettevotja_rekvisiidid__lihtandmed.csv",
    "osanikud_json":   "ettevotja_rekvisiidid__osanikud.json",
    "aruannete_yld":   "1.aruannete_yldandmed_kuni_*.csv",
    "emtak_myygitulu": "2.EMTAK_myygitulu_kuni_*.csv",
    "geo_myygitulu":   "3.myygitulu_geograafiline_kuni_*.csv",
}

DEFAULT_YEARS = [2023, 2024, 2025]

CHUNK_SIZE = 1024 * 1024  # 1 MB chunks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print(msg, *, indent=0):
    print("  " * indent + msg, flush=True)


def _file_size_mb(path):
    return os.path.getsize(path) / 1_048_576


def _glob_newest(directory, pattern):
    """Return the newest file matching pattern in directory, or None."""
    matches = sorted(glob.glob(os.path.join(directory, pattern)))
    return matches[-1] if matches else None


def _find_rik_urls(html):
    """Parse the RIK download page HTML, return {label: full_url}."""
    found = {}
    for label, pattern in RIK_FILE_PATTERNS.items():
        matches = re.findall(pattern, html)
        found[label] = (RIK_BASE_URL + matches[-1]) if matches else None
    return found


def _find_rik_year_urls(html, years):
    """Return {year: full_url} for each requested year."""
    result = {}
    for year in years:
        pattern = RIK_YEAR_PATTERN.format(year=year)
        matches = re.findall(pattern, html)
        result[year] = (RIK_BASE_URL + matches[-1]) if matches else None
    return result


def _progress_hook(label):
    """Return a urlretrieve reporthook that prints progress at 10% intervals."""
    last_pct = [-1]
    last_mb = [0]
    def hook(count, block_size, total_size):
        downloaded = count * block_size
        if total_size > 0:
            pct = min(100, downloaded * 100 // total_size)
            milestone = (pct // 10) * 10
            if milestone > last_pct[0]:
                last_pct[0] = milestone
                mb = downloaded / 1_048_576
                total_mb = total_size / 1_048_576
                print(f"        {milestone:3d}%  {mb:.1f}/{total_mb:.1f} MB", flush=True)
        else:
            mb = downloaded / 1_048_576
            if int(mb) > last_mb[0]:
                last_mb[0] = int(mb)
                print(f"        {mb:.0f} MB downloaded...", flush=True)
    return hook


def _download_file_urllib(url, dest_path, label, force=False):
    """Download via urllib (used for EMTA — no session needed)."""
    if os.path.exists(dest_path) and not force:
        size = _file_size_mb(dest_path)
        _print(f"  SKIP  {label}  ({size:.1f} MB already exists)")
        return False

    _print(f"  DOWN  {label}")
    _print(f"        {url}")
    try:
        urlretrieve(url, dest_path, reporthook=_progress_hook(label))
        size = _file_size_mb(dest_path)
        _print(f"        -> {dest_path} ({size:.1f} MB)")
        return True
    except (URLError, HTTPError) as e:
        _print(f"  ERROR {label}: {e}")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return False


def _download_file_session(session, url, dest_path, label, force=False):
    """Download via requests session (used for RIK — needs Referer + cookies)."""
    if os.path.exists(dest_path) and not force:
        size = _file_size_mb(dest_path)
        _print(f"  SKIP  {label}  ({size:.1f} MB already exists)")
        return False

    _print(f"  DOWN  {label}")
    _print(f"        {url}")
    try:
        resp = session.get(
            url,
            stream=True,
            timeout=60,
            headers={"Referer": RIK_DOWNLOAD_PAGE},
        )
        resp.raise_for_status()

        total = int(resp.headers.get("Content-Length", 0))
        total_mb = total / 1_048_576
        downloaded = 0
        last_pct = -1

        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = min(100, downloaded * 100 // total)
                        milestone = (pct // 10) * 10
                        if milestone > last_pct:
                            last_pct = milestone
                            mb = downloaded / 1_048_576
                            print(f"        {milestone:3d}%  {mb:.1f}/{total_mb:.1f} MB", flush=True)
                    else:
                        mb = downloaded / 1_048_576
                        if int(mb) > int((downloaded - len(chunk)) / 1_048_576):
                            print(f"        {mb:.0f} MB downloaded...", flush=True)

        size = _file_size_mb(dest_path)
        _print(f"        -> {dest_path} ({size:.1f} MB)")
        return True

    except requests.RequestException as e:
        _print(f"  ERROR {label}: {e}")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return False


def _extract_zip(zip_path, target_dir, cleanup_old_glob=None):
    """Extract a ZIP into target_dir, remove old dated versions first if glob given."""
    if cleanup_old_glob:
        for old in glob.glob(os.path.join(target_dir, cleanup_old_glob)):
            _print(f"        removing old: {os.path.basename(old)}")
            os.remove(old)

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        zf.extractall(target_dir)
        for name in names:
            out = os.path.join(target_dir, name)
            size = _file_size_mb(out) if os.path.exists(out) else 0
            _print(f"        extracted: {name} ({size:.1f} MB)")

    os.remove(zip_path)


# ---------------------------------------------------------------------------
# Download routines
# ---------------------------------------------------------------------------

def download_emta(target_dir, force=False):
    _print("\n=== EMTA files ===")
    os.makedirs(target_dir, exist_ok=True)
    ok = 0
    for filename, url in EMTA_FILES.items():
        dest = os.path.join(target_dir, filename)
        downloaded = _download_file_urllib(url, dest, filename, force=force)
        if downloaded or os.path.exists(dest):
            ok += 1
    _print(f"  EMTA done: {ok}/{len(EMTA_FILES)} files present")


def download_rik(target_dir, years=None, force=False):
    _print("\n=== RIK files ===")
    if years is None:
        years = DEFAULT_YEARS

    os.makedirs(target_dir, exist_ok=True)

    # Build a session that looks like a browser and carries page cookies
    session = requests.Session()
    session.headers.update(HEADERS)

    _print("  Fetching RIK download page...")
    try:
        resp = session.get(RIK_DOWNLOAD_PAGE, timeout=30)
        resp.raise_for_status()
        html = resp.text
    except requests.RequestException as e:
        _print(f"  ERROR: could not fetch RIK page: {e}")
        return

    core_urls = _find_rik_urls(html)
    year_urls = _find_rik_year_urls(html, years)

    # Core files
    for label, url in core_urls.items():
        if url is None:
            _print(f"  WARN  {label}: URL not found on page — may have been renamed")
            continue

        filename = url.split("/")[-1]
        zip_dest = os.path.join(target_dir, filename)
        base_name = filename.replace(".zip", "")
        glob_pat = RIK_GLOB_PATTERNS.get(label)

        # Skip if already have a matching extracted file
        if not force and glob_pat:
            existing = _glob_newest(target_dir, glob_pat)
            if existing:
                ex_name = os.path.basename(existing)
                prefix = base_name.split("kuni_")[0] if "kuni_" in base_name else base_name
                if ex_name == base_name or ex_name.startswith(prefix):
                    size = _file_size_mb(existing)
                    _print(f"  SKIP  {label}  ({ex_name}, {size:.1f} MB)")
                    continue

        downloaded = _download_file_session(session, url, zip_dest, label, force=True)
        if downloaded:
            old_glob = glob_pat if (glob_pat and "*" in glob_pat) else None
            _extract_zip(zip_dest, target_dir, cleanup_old_glob=old_glob)

    # Year-specific files
    for year in years:
        url = year_urls.get(year)
        if url is None:
            _print(f"  WARN  year {year}: URL not found on page")
            continue

        filename = url.split("/")[-1]
        zip_dest = os.path.join(target_dir, filename)
        year_glob = f"4.{year}_aruannete_elemendid_kuni_*.csv"

        if not force:
            existing = _glob_newest(target_dir, year_glob)
            if existing:
                size = _file_size_mb(existing)
                _print(f"  SKIP  year {year}  ({os.path.basename(existing)}, {size:.1f} MB)")
                continue

        downloaded = _download_file_session(session, url, zip_dest, f"year {year}", force=True)
        if downloaded:
            _extract_zip(zip_dest, target_dir, cleanup_old_glob=year_glob)

    _print("  RIK done")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Download RIK screener data files (EMTA and/or RIK open data).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python download_data.py --all --target ./CSV
  python download_data.py --emta --target ./CSV
  python download_data.py --rik --years 2023 2024 2025 --target ./CSV
  python download_data.py --all --force --target ./CSV   # re-download everything
        """,
    )
    parser.add_argument(
        "--target", default="./CSV",
        help="Destination folder for downloaded files (default: ./CSV)",
    )
    parser.add_argument(
        "--emta", action="store_true",
        help="Download EMTA quarterly turnover CSV files",
    )
    parser.add_argument(
        "--rik", action="store_true",
        help="Download RIK open data ZIP files and extract CSVs",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Download both EMTA and RIK files (equivalent to --emta --rik)",
    )
    parser.add_argument(
        "--years", nargs="+", type=int, default=DEFAULT_YEARS, metavar="YEAR",
        help=f"Which RIK financial year files to download (default: {DEFAULT_YEARS})",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-download files even if they already exist",
    )

    args = parser.parse_args()

    if not (args.emta or args.rik or args.all):
        parser.print_help()
        sys.exit(0)

    target = os.path.abspath(args.target)
    _print(f"Target folder: {target}")

    if args.emta or args.all:
        download_emta(target, force=args.force)

    if args.rik or args.all:
        download_rik(target, years=args.years, force=args.force)

    _print("\nDone.")


if __name__ == "__main__":
    main()
