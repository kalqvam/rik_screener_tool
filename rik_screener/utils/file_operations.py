import os
import pandas as pd
from pathlib import Path
from typing import List, Optional, Dict, Any, Union
import glob as glob_module
from .config import get_config
from .logging import log_error, log_warning, log_info


# Maps logical filenames to glob patterns for real downloaded filenames.
# The date suffix (e.g., _kuni_28022026) changes per download.
FILE_PATTERNS: Dict[str, str] = {
    'general_data.csv': '1.aruannete_yldandmed_kuni_*.csv',
    'revenues.csv': '2.EMTAK_myygitulu_kuni_*.csv',
    'geo_revenue.csv': '3.myygitulu_geograafiline_kuni_*.csv',
    'legal_data.csv': 'ettevotja_rekvisiidid__lihtandmed.csv',
    'shareholders.json': 'ettevotja_rekvisiidid__osanikud.json',
    'emtak_2025.csv': 'emtak_2025.csv',
}

# Year-specific patterns — {year} is substituted at resolve time
FILE_PATTERNS_YEAR: Dict[str, str] = {
    'financials_{year}.csv': '4.{year}_aruannete_elemendid_kuni_*.csv',
}


def resolve_filename(filename: str, base_path: Optional[str] = None) -> str:
    """Resolve a logical filename to the actual file on disk.

    Tries the exact filename first.  If it doesn't exist, checks
    FILE_PATTERNS (and FILE_PATTERNS_YEAR for year-templated names)
    and returns the first glob match.  Falls back to the original
    filename so downstream code can report the expected name on error.
    """
    if base_path is None:
        base_path = get_config().base_path

    # 1. Exact match — fastest path
    if os.path.exists(os.path.join(base_path, filename)):
        return filename

    # 2. Check static patterns
    if filename in FILE_PATTERNS:
        matches = sorted(glob_module.glob(os.path.join(base_path, FILE_PATTERNS[filename])))
        if matches:
            resolved = os.path.basename(matches[-1])  # newest if multiple
            log_info(f"Resolved '{filename}' -> '{resolved}'")
            return resolved

    # 3. Check year-templated patterns (e.g., financials_2023.csv)
    for template, pattern_template in FILE_PATTERNS_YEAR.items():
        # Extract year from filename by matching against the template
        prefix = template.split('{year}')[0]
        suffix = template.split('{year}')[1]
        if filename.startswith(prefix) and filename.endswith(suffix):
            year = filename[len(prefix):-len(suffix)] if suffix else filename[len(prefix):]
            pattern = pattern_template.replace('{year}', year)
            matches = sorted(glob_module.glob(os.path.join(base_path, pattern)))
            if matches:
                resolved = os.path.basename(matches[-1])
                log_info(f"Resolved '{filename}' -> '{resolved}'")
                return resolved

    # 4. Fallback — return original name
    return filename


def get_file_path(filename: str, base_path: Optional[str] = None) -> str:
    resolved = resolve_filename(filename, base_path)
    if base_path is None:
        base_path = get_config().base_path
    return os.path.join(base_path, resolved)


def validate_file_exists(filename: str, base_path: Optional[str] = None) -> bool:
    file_path = get_file_path(filename, base_path)
    exists = os.path.exists(file_path)

    if not exists:
        log_warning(f"File not found: {file_path} (logical name: '{filename}')")

    return exists


def safe_read_csv(
    filename: str,
    base_path: Optional[str] = None,
    encoding: Optional[str] = None,
    separator: Optional[str] = None,
    chunk_size: Optional[int] = None,
    usecols: Optional[List[str]] = None,
    **kwargs
) -> Union[pd.DataFrame, "pd.io.parsers.TextFileReader", None]:
    """Read a CSV file. Returns a DataFrame, or a TextFileReader iterator when chunk_size is set."""
    file_path = get_file_path(filename, base_path)

    if not validate_file_exists(filename, base_path):
        return None
    
    config = get_config()
    
    if encoding is None:
        encoding = config.get_default('encoding', 'utf-8')
    
    if separator is None:
        separator = config.get_default('csv_separator', ';')

    try:
        log_info(f"Reading CSV file: {filename}")

        read_kwargs = {
            'encoding': encoding,
            **kwargs
        }

        if separator is not None:
            read_kwargs['sep'] = separator
            # Apply decimal separator for European CSV format (semicolon delimiter + comma decimal)
            if separator == ';' and 'decimal' not in kwargs:
                decimal_sep = config.get_default('decimal_separator', '.')
                read_kwargs['decimal'] = decimal_sep

        if chunk_size is not None:
            read_kwargs['chunksize'] = chunk_size

        if usecols is not None:
            read_kwargs['usecols'] = usecols

        df = pd.read_csv(file_path, **read_kwargs)

        if chunk_size is None:
            if df.columns.dtype == object:
                df.columns = df.columns.str.strip()
            log_info(f"Successfully read {len(df)} rows from {filename}")
            log_info(f"Columns detected: {df.columns.tolist()}")
        
        return df
        
    except Exception as e:
        log_error(f"Error reading CSV file {filename}: {str(e)}")
        return None


def safe_write_csv(
    df: pd.DataFrame,
    filename: str,
    base_path: Optional[str] = None,
    encoding: Optional[str] = None,
    **kwargs
) -> bool:
    file_path = get_file_path(filename, base_path)
    config = get_config()
    
    if encoding is None:
        encoding = config.get_default('encoding', 'utf-8')
    
    separator = config.get_default('csv_separator', ';')

    write_kwargs = {
        'index': False,
        'encoding': encoding,
        'sep': separator,
        **kwargs
    }
    
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        df.to_csv(file_path, **write_kwargs)
        log_info(f"Successfully saved {len(df)} rows to {filename}")
        return True
        
    except Exception as e:
        log_error(f"Error writing CSV file {filename}: {str(e)}")
        return False


def cleanup_temp_files(
    pattern: str = "*temp_*.csv",
    base_path: Optional[str] = None
) -> int:
    if base_path is None:
        base_path = get_config().base_path
    
    search_pattern = os.path.join(base_path, pattern)
    temp_files = glob_module.glob(search_pattern)
    
    deleted_count = 0
    
    for file_path in temp_files:
        try:
            os.remove(file_path)
            deleted_count += 1
            log_info(f"Deleted temporary file: {os.path.basename(file_path)}")
            
        except Exception as e:
            log_warning(f"Could not delete {file_path}: {str(e)}")
    
    if deleted_count > 0:
        log_info(f"Cleaned up {deleted_count} temporary files")
    
    return deleted_count
