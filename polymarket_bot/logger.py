import os
import logging
import logging.handlers
import colorlog

def setup_logging() -> logging.Logger:
    os.makedirs('logs', exist_ok=True)
    logger = logging.getLogger('polymarket_bot')
    logger.setLevel(logging.INFO)
    logger.handlers = []
    file_formatter = logging.Formatter('%(asctime)s | %(levelname)-8s | %(threadName)-12s | %(name)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    console_formatter = colorlog.ColoredFormatter('%(log_color)s%(asctime)s | %(levelname)-8s | %(threadName)-12s | %(name)s | %(message)s%(reset)s', datefmt='%Y-%m-%d %H:%M:%S', log_colors={'DEBUG': 'cyan','INFO': 'green','WARNING': 'yellow','ERROR': 'red','CRITICAL': 'red,bg_white'})
    file_handler = logging.handlers.RotatingFileHandler('logs/polymarket_bot.log', maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.INFO)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(logging.INFO)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger

logger = setup_logging()

import functools

def log_function_call(logger: logging.Logger):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            name = func.__name__
            logger.debug(f"Entering {name}")
            try:
                result = func(*args, **kwargs)
                logger.debug(f"Exiting {name} successfully")
                return result
            except Exception as e:
                logger.error(f"Error in {name}: {str(e)}", exc_info=True)
                raise
        return wrapper
    return decorator

class LoggingContext:
    def __init__(self, logger, level=None, handler=None, close=True):
        self.logger = logger
        self.level = level
        self.handler = handler
        self.close = close
    def __enter__(self):
        if self.level is not None:
            self.old_level = self.logger.level
            self.logger.setLevel(self.level)
        if self.handler:
            self.logger.addHandler(self.handler)
    def __exit__(self, et, ev, tb):
        if self.level is not None:
            self.logger.setLevel(self.old_level)
        if self.handler:
            self.logger.removeHandler(self.handler)
        if self.handler and self.close:
            self.handler.close()
