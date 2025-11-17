import time
from web3 import Web3
from py_clob_client.client import ClobClient
from .config import PRIVATE_KEY, YOUR_PROXY_WALLET, WEB3_PROVIDER, MAX_RETRIES
from .logger import logger

web3 = Web3(Web3.HTTPProvider(WEB3_PROVIDER))

client = None

def get_client(max_retries: int = MAX_RETRIES) -> ClobClient | None:
    """惰性获取并初始化 Polymarket CLOB 客户端。

    Args:
        max_retries: 最大重试次数。

    Returns:
        已初始化的 `ClobClient`；若初始化失败则返回 None。
    """
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
    """刷新 API 凭证，保持会话有效。

    Returns:
        True 表示刷新成功；False 表示客户端不可用或刷新失败。
    """
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
"""CLOB 客户端管理模块。

提供惰性初始化的客户端获取与凭证刷新逻辑，避免在 import 阶段因网络异常导致程序崩溃。
"""