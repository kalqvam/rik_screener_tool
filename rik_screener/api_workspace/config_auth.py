import time
from typing import Optional

class APIConfig:
    def __init__(
        self,
        username: str,
        password: str,
        rate_limit: int = 20,
        base_url: str = "https://ariregxmlv6.rik.ee/"
    ):
        self.username = username
        self.password = password
        self.rate_limit = rate_limit
        self.base_url = base_url
        self.last_request_time = 0.0
    
    def wait_for_rate_limit(self):
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        wait_time = 60.0 / self.rate_limit
        
        if time_since_last < wait_time:
            sleep_duration = wait_time - time_since_last
            time.sleep(sleep_duration)
        
        self.last_request_time = time.time()

_config_instance: Optional[APIConfig] = None

def set_api_config(username: str, password: str, rate_limit: int = 20) -> APIConfig:
    global _config_instance
    _config_instance = APIConfig(username, password, rate_limit)
    return _config_instance

def get_api_config() -> APIConfig:
    global _config_instance
    if _config_instance is None:
        raise ValueError("API configuration not set. Call set_api_config() first.")
    return _config_instance
