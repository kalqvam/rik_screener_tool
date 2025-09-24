import os
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path


class ConfigManager:    
    def __init__(self, base_path: Optional[str] = None):
        self._base_path = base_path or self._get_default_base_path()
        self._defaults = self._load_defaults()
        
    def _get_default_base_path(self) -> str:
        env_path = os.getenv('RIK_SCREENER_PATH')
        if env_path:
            return env_path

        return "/content/drive/MyDrive/YOUR_FOLDER"
    
    def _load_defaults(self) -> Dict[str, Any]:
        return {
            'years': [2023, 2022, 2021],
            'legal_forms': ["AS", "OÜ"],
            'encoding': 'utf-8-sig',
            'csv_separator': ';',
            'chunk_size': 500000,
            'decimal_separator': '.',
            'financial_items': [
                "Müügitulu",
                "Ärikasum (kahjum)",
                "Omakapital",
                "Põhivarade kulum ja väärtuse langus",
                "Aruandeaasta kasum (kahjum)",
                "Varad",
                "Töötajate keskmine arv taandatud täistööajale",
                "Raha",
                "Lühiajalised kohustised",
                "Pikaajalised kohustised",
                "Käibevarad",
                "Tööjõukulud"
            ]
        }
    
    @property
    def base_path(self) -> str:
        return self._base_path
    
    @base_path.setter
    def base_path(self, path: str):
        self._base_path = str(Path(path).resolve())
    
    def get_file_path(self, filename: str) -> str:
        return os.path.join(self.base_path, filename)
    
    def validate_base_path(self) -> bool:
        try:
            return os.path.exists(self.base_path) and os.path.isdir(self.base_path)
        except Exception:
            return False
    
    def get_default(self, key: str, fallback: Any = None) -> Any:
        return self._defaults.get(key, fallback)
    
    def get_years(self, custom_years: Optional[List[int]] = None) -> List[int]:
        years = custom_years or self.get_default('years')
        return sorted(years, reverse=True)
    
    def get_timestamp(self) -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S")
    
    def setup_environment(self) -> bool:
        try:
            from google.colab import drive
            drive.mount('/content/drive')
            print("Google Drive mounted successfully")
            return True
        except ImportError:
            print("Running outside of Google Colab")
            return True
        except Exception as e:
            print(f"Failed to mount Google Drive: {e}")
            return False

_config_instance = None

def get_config() -> ConfigManager:
    global _config_instance
    if _config_instance is None:
        _config_instance = ConfigManager()
    return _config_instance
