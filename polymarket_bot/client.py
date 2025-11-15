import time
from web3 import Web3
from py_clob_client.client import ClobClient
from .config import PRIVATE_KEY, YOUR_PROXY_WALLET, WEB3_PROVIDER, MAX_RETRIES
from .logger import logger

web3 = Web3(Web3.HTTPProvider(WEB3_PROVIDER))

client = None

def get_client(max_retries: int = MAX_RETRIES) -> ClobClient | None:
    global client
    if client is not None:
        return client
    for attempt in range(max_retries):
        try:
            c = ClobClient(host="https://clob.polymarket.com", key=PRIVATE_KEY, chain_id=137, signature_type=1, funder=YOUR_PROXY_WALLET)
            api_creds = c.create_or_derive_api_creds()
            c.set_api_creds(api_creds)
            client = c
            logger.info("✅ ClobClient initialized")
            return client
        except Exception as e:
            logger.warning(f"Failed to initialize ClobClient (attempt {attempt + 1}/{max_retries}): {e}")
            time.sleep(2 ** attempt)
    logger.error("❌ ClobClient initialization failed; trading functions will be disabled until retry succeeds")
    return None

def refresh_api_credentials() -> bool:
    try:
        c = get_client()
        if c is None:
            return False
        api_creds = c.create_or_derive_api_creds()
        c.set_api_creds(api_creds)
        logger.info("✅ API credentials refreshed successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to refresh API credentials: {str(e)}")
        return False
