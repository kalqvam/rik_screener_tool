import pandas as pd
import numpy as np
import os
import json
import re
from typing import List, Dict, Optional
from datetime import datetime

BASE_PATH = os.getenv('RIK_SCREENER_PATH', '')

from .df_prep.general_filter import filter_companies
from .df_prep.multi_year_merger import merge_multiple_years
from .criteria_setup.calculations import calculate_ratios, create_formula
from .utils.data_processing import extract_quoted_columns
from .add_info.industry_codes import add_industry_classifications
from .add_info.shareholder_data import add_ownership_data
from .add_info.emtak_descriptions import add_emtak_descriptions, get_industry_summary
from .post_processing.filtering import filter_and_rank
from .add_info.company_age import add_company_age
from .post_processing.company_names import add_company_names
from .emta_screener import run_emta_screening
from .workflow import run_company_screening, validate_config
from .utils.logging import log_info, log_warning

__version__ = "5.0.0"
__author__ = "kalqvam"

__all__ = [
    'filter_companies',
    'merge_multiple_years',
    'calculate_ratios',
    'create_formula',
    'extract_quoted_columns',
    'add_industry_classifications',
    'add_ownership_data',
    'add_emtak_descriptions',
    'get_industry_summary',
    'filter_and_rank',
    'add_company_age',
    'add_company_names',
    'run_emta_screening',
    'run_company_screening',
    'validate_config',
    'BASE_PATH',
    'get_timestamp',
    'validate_base_path',
    'set_base_path'
]

def set_base_path(path: str):
    global BASE_PATH
    BASE_PATH = path

    from .df_prep import general_filter
    from .df_prep import multi_year_merger
    from .criteria_setup import calculations
    from .add_info import industry_codes
    from .add_info import shareholder_data
    from .add_info import emtak_descriptions
    from .add_info import company_age
    from .post_processing import filtering
    from .post_processing import company_names

    general_filter.BASE_PATH = path
    multi_year_merger.BASE_PATH = path
    calculations.BASE_PATH = path
    industry_codes.BASE_PATH = path
    shareholder_data.BASE_PATH = path
    emtak_descriptions.BASE_PATH = path
    company_age.BASE_PATH = path
    filtering.BASE_PATH = path
    company_names.BASE_PATH = path

def get_timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def validate_base_path():
    if not BASE_PATH:
        log_warning("RIK_SCREENER_PATH is not set — call set_base_path() or set the environment variable")
        return False
    if not os.path.exists(BASE_PATH):
        log_warning(f"Base path {BASE_PATH} does not exist")
        return False
    return True

log_info(f"RIK Screener v{__version__} initialized")
if not BASE_PATH:
    log_info("Set RIK_SCREENER_PATH environment variable to point to your data folder")
