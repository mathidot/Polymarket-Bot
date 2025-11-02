import os
import logging.handlers
import colorlog


def setup_logging() -> logging.Logger:
    """Setup enhanced logging configuration with both file and console handlers"""
    # Create logs directory if it doesn't exist
    os.makedirs('logs', exist_ok=True)
    
    # Create a logger
    logger = logging.getLogger('polymarket_bot')
    logger.setLevel(logging.INFO)
    
    # Clear any existing handlers
    logger.handlers = []
    
    # Create formatters
    file_formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(threadName)-12s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    console_formatter = colorlog.ColoredFormatter(
        '%(log_color)s%(asctime)s | %(levelname)-8s | %(threadName)-12s | %(name)s | %(message)s%(reset)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'red,bg_white'
        }
    )
    
    # File handler - Rotating file handler with size limit
    file_handler = logging.handlers.RotatingFileHandler(
        'logs/polymarket_bot.log',
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.INFO)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(logging.INFO)
    
    # Add handlers to logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger