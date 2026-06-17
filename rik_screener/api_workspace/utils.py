import re
from typing import List
from ..utils import log_warning

def validate_company_code(code: str) -> bool:
    if not isinstance(code, str):
        return False
    
    clean_code = code.strip()
    
    if not clean_code:
        return False
    
    if not re.match(r'^\d+$', clean_code):
        return False
    
    if len(clean_code) < 7 or len(clean_code) > 8:
        return False
    
    return True

def validate_company_codes(codes: List[str]) -> List[str]:
    valid_codes = []
    invalid_codes = []
    
    for code in codes:
        if validate_company_code(code):
            valid_codes.append(code.strip())
        else:
            invalid_codes.append(code)
    
    if invalid_codes:
        log_warning(f"Invalid company codes found and will be skipped: {invalid_codes}")
    
    return valid_codes

def format_progress(current: int, total: int, prefix: str = "Processing") -> str:
    percentage = (current / total) * 100 if total > 0 else 0
    return f"{prefix}: {current}/{total} ({percentage:.1f}%)"
