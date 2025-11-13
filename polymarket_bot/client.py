import time
from web3 import Web3
from py_clob_client.client import ClobClient
from .config import PRIVATE_KEY, YOUR_PROXY_WALLET, WEB3_PROVIDER
from .logger import logger

web3 = Web3(Web3.HTTPProvider(WEB3_PROVIDER))

def initialize_clob_client(max_retries: int = 3) -> ClobClient:
    for attempt in range(max_retries):
        try:
            client = ClobClient(host="https://clob.polymarket.com", key=PRIVATE_KEY, chain_id=137, signature_type=1, funder=YOUR_PROXY_WALLET)
            api_creds = client.create_or_derive_api_creds()
            client.set_api_creds(api_creds)
            return client
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            logger.warning(f"Failed to initialize ClobClient (attempt {attempt + 1}/3): {e}")
            time.sleep(2 ** attempt)
    raise RuntimeError("Failed to initialize ClobClient after maximum retries")

client = initialize_clob_client()

def refresh_api_credentials() -> bool:
    try:
        api_creds = client.create_or_derive_api_creds()
        client.set_api_creds(api_creds)
        logger.info("✅ API credentials refreshed successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to refresh API credentials: {str(e)}")
        return False
