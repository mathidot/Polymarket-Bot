import time
import random
import logging
from typing import Any, Optional

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderType, OrderArgs, BookParams

from config import PRIVATE_KEY, YOUR_PROXY_WALLET, MAX_RETRIES, ORDERBOOK_RETRY_MAX, ORDERBOOK_RETRY_BASE_DELAY, ORDERBOOK_RETRY_JITTER_MS, SIMULATION_MODE


logger = logging.getLogger("polymarket_bot")


_client: Optional[ClobClient] = None


def initialize_clob_client(max_retries: int = 3) -> ClobClient:
    for attempt in range(max_retries):
        try:
            client = ClobClient(
                host="https://clob.polymarket.com",
                key=PRIVATE_KEY if not SIMULATION_MODE else "0x" + ("0" * 64),
                chain_id=137,
                signature_type=2,
                funder=YOUR_PROXY_WALLET if not SIMULATION_MODE else "0x0000000000000000000000000000000000000000",
            )
            # In simulation mode, do not derive or set API creds (no PK required for reads)
            if not SIMULATION_MODE:
                api_creds = client.create_or_derive_api_creds()
                client.set_api_creds(api_creds)
            return client
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            logger.warning(
                f"Failed to initialize ClobClient (attempt {attempt + 1}/{max_retries}): {e}"
            )
            time.sleep(2 ** attempt)
    raise RuntimeError("Failed to initialize ClobClient after maximum retries")


def get_client() -> ClobClient:
    global _client
    if _client is None:
        _client = initialize_clob_client()
    return _client


def refresh_api_credentials() -> bool:
    try:
        client = get_client()
        api_creds = client.create_or_derive_api_creds()
        client.set_api_creds(api_creds)
        logger.info("🔑 API credentials refreshed successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to refresh API credentials: {e}")
        return False


def token_has_orderbook(token_id: str) -> bool:
    try:
        client = get_client()
        ob = client.get_order_book(token_id)
        return bool(
            ob and ((getattr(ob, "bids", None) and len(ob.bids) > 0) or (getattr(ob, "asks", None) and len(ob.asks) > 0))
        )
    except Exception as e:
        logger.debug(f"🔍 Orderbook check failed for token {token_id}: {e}")
        return False


def get_order_book(token_id: str):
    client = get_client()
    return client.get_order_book(token_id)


def get_price(token_id: str, side: str) -> Any:
    client = get_client()
    return client.get_price(token_id, side)


def create_market_order(args: MarketOrderArgs):
    client = get_client()
    return client.create_market_order(args)


def post_order(order, order_type: OrderType):
    client = get_client()
    return client.post_order(order, order_type)


def create_limit_order(args: OrderArgs):
    client = get_client()
    # Limit orders are created via generic create_order
    return client.create_order(args)


def cancel_order(order_id: str):
    client = get_client()
    return client.cancel_order(order_id)


def cancel_orders(order_ids):
    client = get_client()
    return client.cancel_orders(order_ids)


def cancel_all():
    client = get_client()
    return client.cancel_all()


def cancel_market_orders(market: Optional[str] = None, asset_id: Optional[str] = None):
    client = get_client()
    # Cancel resting orders for a specific market or asset
    return client.cancel_market_orders(market=market, asset_id=asset_id)


def get_order_books(token_ids):
    client = get_client()
    params = [BookParams(token_id=str(t)) for t in token_ids]
    return client.get_order_books(params)


def get_order_books_with_retry(token_ids, max_retries: Optional[int] = None, base_delay: Optional[float] = None, jitter_ms: Optional[int] = None):
    """Batch fetch order books with exponential backoff and jitter.

    If the batch request keeps failing after retries, fall back to per-token
    retrieval with its own retry loop, returning a list aligned with input order.
    Missing tokens will be returned as None so callers can gracefully degrade.
    """
    client = get_client()
    tokens_list = [str(t) for t in token_ids]
    params = [BookParams(token_id=t) for t in tokens_list]
    retries = ORDERBOOK_RETRY_MAX if max_retries is None else max_retries
    delay = ORDERBOOK_RETRY_BASE_DELAY if base_delay is None else base_delay
    jitter = ORDERBOOK_RETRY_JITTER_MS if jitter_ms is None else jitter_ms
    last_err = None

    for attempt in range(retries):
        try:
            books = client.get_order_books(params)
            return books
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                sleep_s = delay * (2 ** attempt) + random.uniform(0, jitter) / 1000.0
                logger.warning(
                    f"Batch get_order_books failed (attempt {attempt+1}/{retries}) for {len(tokens_list)} tokens: {e}; retry in {sleep_s:.2f}s"
                )
                time.sleep(sleep_s)
            else:
                logger.error(
                    f"❌ Batch get_order_books failed after {retries} attempts for {len(tokens_list)} tokens: {e}"
                )
                break

    # Fallback: per-token retrieval with its own retry loop
    results = []
    if last_err:
        logger.warning("Falling back to sequential per-token order book fetch with retry")
    for idx, tok in enumerate(tokens_list):
        per_token = None
        per_err = None
        for attempt in range(retries):
            try:
                per_token = client.get_order_book(tok)
                break
            except Exception as e:
                per_err = e
                if attempt < retries - 1:
                    sleep_s = delay * (2 ** attempt) + random.uniform(0, jitter) / 1000.0
                    logger.debug(
                        f"Token {tok} get_order_book failed (attempt {attempt+1}/{retries}): {e}; retry in {sleep_s:.2f}s"
                    )
                    time.sleep(sleep_s)
                else:
                    logger.warning(
                        f"⚠️ Token {tok} get_order_book failed after {retries} attempts: {e}"
                    )
        results.append(per_token)

    return results

    
if __name__ == '__main__':
    order_book = get_order_book("89399717926999326485018846686054023773702524861214761232140040823109372457933")
    print(order_book)