import requests
import time
import json
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from log import setup_logging
from config import API_TIMEOUT, MAX_RETRIES, REQUESTS_VERIFY_SSL

EVENTS_URL = "https://gamma-api.polymarket.com/events"
SLUG_URL = "https://gamma-api.polymarket.com/events/slug"
MARKET_URL = "https://gamma-api.polymarket.com/markets"
logger = setup_logging()

# Persistent session with robust retry/backoff to handle intermittent SSL EOFs and network hiccups
_session = requests.Session()
_session.headers.update({
    "User-Agent": "PolymarketSpikeBot/1.0 (+https://polymarket.com)"
})
_retry = Retry(
    total=MAX_RETRIES,
    connect=MAX_RETRIES,
    read=MAX_RETRIES,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
    raise_on_status=False,
)
_adapter = HTTPAdapter(max_retries=_retry)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)


def _fetch_json(url: str, params: dict | None = None) -> dict | list:
    """Fetch JSON with retries, handling SSL EOF errors gracefully."""
    params = params or {}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = _session.get(url, params=params, timeout=API_TIMEOUT, verify=REQUESTS_VERIFY_SSL)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.SSLError as e:
            logger.error(
                f"请求失败: {e} | 尝试 {attempt}/{MAX_RETRIES} | URL={url}"
            )
        except requests.exceptions.RequestException as e:
            logger.error(
                f"请求失败: {e} | 尝试 {attempt}/{MAX_RETRIES} | URL={url}"
            )
        # Exponential backoff
        time.sleep(min(5, 0.5 * (2 ** (attempt - 1))))
    # Final failure
    raise requests.exceptions.RequestException(
        f"请求失败: 达到最大重试次数 ({MAX_RETRIES}) | URL={url}"
    )

'''
There are multiple events for each event slug, each event has multiple markets
and each market has multiple tokens (YES/NO)
'''

def get_all_slug_events() -> list:
    '''
    Get all event slugs from the Polymarket API.
    '''
    all_slug_events = []
    next_cursor = ""
    page_count = 0
    while True:
        page_count += 1
        # 构造请求 URL 和参数
        params = {
            "closed": "false",      # 仅获取未关闭的市场
            "limit": 100,            # 每页限制 100 个结果
        }
        if next_cursor:
            params["next_cursor"] = next_cursor
            
        logger.info(f"   - 正在请求第 {page_count} 页...")
            
        try:
            data = _fetch_json(EVENTS_URL, params=params)

            for item in data:
                current_slugs = item.get('slug')
                all_slug_events.append(current_slugs)
            
            # 注意：next_cursor 的位置可能随 API 变动，这里保持原有逻辑
            next_cursor = data[0].get('next_cursor', "")
            if not next_cursor:
                break
            time.sleep(1)
        except requests.exceptions.RequestException as e:
            logger.error(f"请求失败: {e}")
            break
    logger.info(f"\n✅ 任务完成！总共获取到 {len(all_slug_events)} 个有效的 Market Slug。")
    return all_slug_events

def get_market_from_slug(eventslug: str) -> list:
    '''
    Get the market IDs from a event slug.
    '''
    url = f"{SLUG_URL}/{eventslug}"
    market_ids = []
    try:
        event_data = _fetch_json(url)
        if event_data.get('slug') != eventslug:
            logger.warning(f"事件 slug 不匹配: {eventslug} != {event_data.get('slug')}")
            raise ValueError(f"事件 slug 不匹配: {eventslug} != {event_data.get('slug')}")
        markets = event_data.get('markets', [])
        for market in markets:
            market_ids.append(market.get('id', ""))
        return market_ids
    except requests.exceptions.RequestException as e:
        logger.error(f"请求失败: {e}")
        raise e

def get_token_from_market(market_id: str) -> list:
    '''
    Get the token IDs from a market ID.
    '''
    url = f"{MARKET_URL}/{market_id}"
    try:
        market_data = _fetch_json(url)
        tokens_str = market_data.get('clobTokenIds', "")
        tokens_list = json.loads(tokens_str)
        if tokens_list and len(tokens_list) == 2:
            # Polymarket 的二元市场通常包含两个 Token (YES/NO)
            yes_token = tokens_list[0]
            no_token = tokens_list[1]
            logger.info(f"   - 成功获取 Token ID:")
            logger.info(f"   - 市场问题: {market_data.get('question')}")
            logger.info(f"   - YES Token ID: **{yes_token}**")
            logger.info(f"   - NO Token ID: **{no_token}**")
            return [yes_token, no_token]
        else:
            logger.error("❌ 警告：市场数据中未找到有效的 Token 列表。")       
            raise ValueError("❌ 警告：市场数据中未找到有效的 Token 列表。")
    except requests.exceptions.RequestException as e:
        logger.error(f"请求失败: {e}")
        raise e

if __name__ == "__main__":
    all_slug_events = get_all_slug_events()
    market_ids = []
    print(len(all_slug_events))
    # for slug in all_slug_events:
    #     ids = get_market_from_slug(slug)
    #     print(ids)
    #     market_ids.extend(ids)
    token_ids = get_token_from_market("516706")
    print(token_ids)
