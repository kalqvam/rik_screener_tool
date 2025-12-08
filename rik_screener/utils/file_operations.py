import os
import pandas as pd
from pathlib import Path
from typing import List, Optional, Dict, Any, Union
import glob
from .config import get_config
from .logging import log_error, log_warning, log_info


def get_file_path(filename: str, base_path: Optional[str] = None) -> str:
    if base_path is None:
        base_path = get_config().base_path
    return os.path.join(base_path, filename)


def validate_file_exists(filename: str, base_path: Optional[str] = None) -> bool:
    file_path = get_file_path(filename, base_path)
    exists = os.path.exists(file_path)
    
    if not exists:
        log_warning(f"File not found: {file_path}")
    
    return exists


def safe_read_csv(
    filename: str,
    base_path: Optional[str] = None,
    encoding: Optional[str] = None,
    separator: Optional[str] = None,
    chunk_size: Optional[int] = None,
    usecols: Optional[List[str]] = None,
    **kwargs
) -> Optional[pd.DataFrame]:
    file_path = get_file_path(filename, base_path)
    
    if not validate_file_exists(filename, base_path):
        return None
    
    config = get_config()
    
    if encoding is None:
        encoding = config.get_default('encoding', 'utf-8')
    
    if separator is None:
        if any(name in filename for name in ['general_data.csv', 'revenues.csv', 'financials_', 'geography.csv']):
            separator = ';'
    
    try:
        log_info(f"Reading CSV file: {filename}")
        
        read_kwargs = {
            'encoding': encoding,
            **kwargs
        }
        
        if separator is not None:
            read_kwargs['sep'] = separator
        
        if chunk_size is not None:
            read_kwargs['chunksize'] = chunk_size
            
        if usecols is not None:
            read_kwargs['usecols'] = usecols
        
        df = pd.read_csv(file_path, **read_kwargs)
        
        if chunk_size is None:
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
    
    write_kwargs = {
        'index': False,
        'encoding': encoding,
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
    temp_files = glob.glob(search_pattern)
    
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
