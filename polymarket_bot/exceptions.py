class BotError(Exception):
    pass

class ConfigurationError(BotError):
    pass

class NetworkError(BotError):
    pass

class TradingError(BotError):
    pass

class ValidationError(BotError):
    pass
