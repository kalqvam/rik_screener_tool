from typing import Dict, List, Any, Set
import pandas as pd


def validate_config(config: Dict[str, Any]) -> None:
    if not isinstance(config, dict):
        raise ValueError("Config must be a dictionary")
    
    _validate_years(config.get('years'))
    _validate_legal_forms(config.get('legal_forms'))
    _validate_skip_steps(config.get('skip_steps'))
    _validate_pipeline_mode(config.get('use_dataframe_pipeline'))
    _validate_formulas(config)
    _validate_financial_filters(config.get('financial_filters'))
    _validate_ownership_filters(config.get('ownership_filters'))
    _validate_industry_codes_filter(config.get('industry_codes_filter'))
    _validate_geography_filters(config.get('geography_filters'))


def _validate_years(years):
    if years is None:
        raise ValueError("Years must be specified")
    if not isinstance(years, list) or len(years) == 0:
        raise ValueError("Years must be a non-empty list")
    if not all(isinstance(y, int) and 2000 <= y <= 2030 for y in years):
        raise ValueError("Years must be integers between 2000 and 2030")


def _validate_legal_forms(legal_forms):
    if legal_forms is not None:
        if not isinstance(legal_forms, list):
            raise ValueError("Legal forms must be a list")
        valid_forms = ["AS", "OÜ"]
        if not all(form in valid_forms for form in legal_forms):
            raise ValueError(f"Legal forms must be from {valid_forms}")


def _validate_skip_steps(skip_steps):
    if skip_steps is not None:
        if not isinstance(skip_steps, list):
            raise ValueError("Skip steps must be a list")
        valid_steps = ["industry", "age", "emtak", "ownership", "geography"]
        invalid_steps = [step for step in skip_steps if step not in valid_steps]
        if invalid_steps:
            raise ValueError(f"Invalid skip steps: {invalid_steps}. Valid options: {valid_steps}")


def _validate_pipeline_mode(use_dataframe_pipeline):
    if use_dataframe_pipeline is not None:
        if not isinstance(use_dataframe_pipeline, bool):
            raise ValueError("use_dataframe_pipeline must be a boolean")


def _validate_formulas(config):
    standard_formulas = config.get('standard_formulas', {})
    custom_formulas = config.get('custom_formulas', {})
    
    if not isinstance(standard_formulas, dict):
        raise ValueError("Standard formulas must be a dictionary")
    if not isinstance(custom_formulas, dict):
        raise ValueError("Custom formulas must be a dictionary")
    
    _validate_standard_formulas(standard_formulas, config.get('years', []))
    _validate_custom_formulas(custom_formulas)
    
    standard_names = _get_generated_formula_names(standard_formulas, config.get('years', []))
    custom_names = set(custom_formulas.keys())
    
    overlapping = standard_names & custom_names
    if overlapping:
        raise ValueError(f"Overlapping formula names between standard and custom: {overlapping}")


def _validate_standard_formulas(standard_formulas, years):
    valid_formula_types = [
        'ebitda', 'ebitda_margin', 'roe', 'roa', 'asset_turnover', 'employee_efficiency',
        'cash_ratio', 'current_ratio', 'debt_to_equity', 'labour_ratio',
        'revenue_growth', 'revenue_cagr'
    ]
    
    for formula_type, config_val in standard_formulas.items():
        if formula_type not in valid_formula_types:
            raise ValueError(f"Invalid standard formula type: {formula_type}")
        
        if formula_type in ['revenue_growth']:
            if not isinstance(config_val, dict) or 'year_pairs' not in config_val:
                raise ValueError(f"{formula_type} must have 'year_pairs' specified")
            if not isinstance(config_val['year_pairs'], list):
                raise ValueError(f"{formula_type} year_pairs must be a list")
            for pair in config_val['year_pairs']:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError(f"{formula_type} year_pairs must contain lists of 2 years each")
        
        elif formula_type in ['revenue_cagr']:
            if not isinstance(config_val, dict):
                raise ValueError(f"{formula_type} must be a dictionary")
            if 'start_year' not in config_val or 'end_year' not in config_val:
                raise ValueError(f"{formula_type} must have 'start_year' and 'end_year'")
        
        else:
            if not isinstance(config_val, dict) or 'years' not in config_val:
                raise ValueError(f"{formula_type} must have 'years' specified")
            if not isinstance(config_val['years'], list):
                raise ValueError(f"{formula_type} years must be a list")


def _validate_custom_formulas(custom_formulas):
    for name, formula in custom_formulas.items():
        if not isinstance(name, str) or not name:
            raise ValueError("Custom formula names must be non-empty strings")
        if not isinstance(formula, str) or not formula:
            raise ValueError("Custom formula expressions must be non-empty strings")


def _get_generated_formula_names(standard_formulas, years):
    names = set()
    
    for formula_type, config_val in standard_formulas.items():
        if formula_type == 'revenue_growth':
            for from_year, to_year in config_val.get('year_pairs', []):
                names.add(f"revenue_growth_{from_year}_to_{to_year}")
        elif formula_type == 'revenue_cagr':
            start_year = config_val.get('start_year')
            end_year = config_val.get('end_year')
            if start_year and end_year:
                names.add(f"revenue_cagr_{start_year}_to_{end_year}")
        else:
            use_averages = config_val.get('use_averages', True)
            for year in config_val.get('years', []):
                if formula_type in ['roe', 'roa', 'asset_turnover', 'employee_efficiency']:
                    suffix = "_single" if not use_averages else ""
                    names.add(f"{formula_type}{suffix}_{year}")
                else:
                    names.add(f"{formula_type}_{year}")
    
    return names


def _validate_financial_filters(financial_filters):
    if financial_filters is not None:
        if not isinstance(financial_filters, list):
            raise ValueError("Financial filters must be a list")
        for i, filter_dict in enumerate(financial_filters):
            if not isinstance(filter_dict, dict):
                raise ValueError(f"Filter {i} must be a dictionary")
            if 'column' not in filter_dict:
                raise ValueError(f"Filter {i} must have 'column' specified")


def _validate_ownership_filters(ownership_filters):
    if ownership_filters is not None:
        if not isinstance(ownership_filters, dict):
            raise ValueError("Ownership filters must be a dictionary")


def _validate_industry_codes_filter(industry_codes_filter):
    if industry_codes_filter is not None:
        if not isinstance(industry_codes_filter, list):
            raise ValueError("industry_codes_filter must be a list of strings")
        if not all(isinstance(c, str) for c in industry_codes_filter):
            raise ValueError("industry_codes_filter entries must be strings")


def _validate_geography_filters(geography_filters):
    if geography_filters is None:
        return
    if not isinstance(geography_filters, dict):
        raise ValueError("geography_filters must be a dictionary")
    valid_keys = {"min_export_share", "max_domestic_share", "export_countries"}
    unknown = set(geography_filters) - valid_keys
    if unknown:
        raise ValueError(f"Unknown geography_filters keys: {unknown}. Valid: {valid_keys}")
    for k in ("min_export_share", "max_domestic_share"):
        v = geography_filters.get(k)
        if v is not None:
            if not isinstance(v, (int, float)):
                raise ValueError(f"geography_filters['{k}'] must be a number")
            if not (0.0 <= v <= 1.0):
                raise ValueError(f"geography_filters['{k}'] must be between 0.0 and 1.0")
    ec = geography_filters.get("export_countries")
    if ec is not None:
        if not isinstance(ec, list):
            raise ValueError("geography_filters['export_countries'] must be a list")
        if not all(isinstance(c, str) for c in ec):
            raise ValueError("geography_filters['export_countries'] entries must be strings")
