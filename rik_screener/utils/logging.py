import sys
import traceback
from datetime import datetime
from typing import Optional, Any
from enum import Enum


class LogLevel(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    STEP = "STEP"


class ProgressLogger:
    def __init__(self, enable_timestamps: bool = True):
        self.enable_timestamps = enable_timestamps
        self.current_step = 0
    
    def reset_step_counter(self):
        self.current_step = 0
    
    def _format_message(self, level: LogLevel, message: str) -> str:
        timestamp = datetime.now().strftime("%H:%M:%S") if self.enable_timestamps else ""
        
        if level == LogLevel.STEP:
            formatted = f"\n=== {message} ==="
        else:
            prefix = f"[{timestamp}] " if timestamp else ""
            formatted = f"{prefix}{message}"
        
        return formatted
    
    def _write_log(self, formatted_message: str):
        print(formatted_message, file=sys.stderr)
    
    def log(self, level: LogLevel, message: str):
        formatted = self._format_message(level, message)
        self._write_log(formatted)
    
    def info(self, message: str):
        self.log(LogLevel.INFO, message)
    
    def warning(self, message: str):
        self.log(LogLevel.WARNING, message)
    
    def error(self, message: str, include_traceback: bool = False):
        self.log(LogLevel.ERROR, message)
        
        if include_traceback:
            tb_str = traceback.format_exc()
            self._write_log(tb_str)
    
    def step(self, message: str, step_number: Optional[int] = None):
        if step_number is not None:
            self.current_step = step_number
        else:
            self.current_step += 1
        
        step_message = f"STEP {self.current_step}: {message}"
        self.log(LogLevel.STEP, step_message)

_logger_instance = None


def get_logger() -> ProgressLogger:
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = ProgressLogger()
    return _logger_instance


def reset_logger():
    global _logger_instance
    if _logger_instance is not None:
        _logger_instance.reset_step_counter()


def log_info(message: str):
    get_logger().info(message)


def log_warning(message: str):
    get_logger().warning(message)


def log_error(message: str, include_traceback: bool = False):
    get_logger().error(message, include_traceback)


def log_step(message: str, step_number: Optional[int] = None):
    get_logger().step(message, step_number)
