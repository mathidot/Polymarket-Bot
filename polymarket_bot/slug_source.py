import json
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Dict, List, Tuple, Optional
from .logger import logger
from .config import API_TIMEOUT, MAX_RETRIES, REQUESTS_VERIFY_SSL

EVENTS_URL = "https://gamma-api.polymarket.com/events"
SLUG_URL = "https://gamma-api.polymarket.com/events/slug"
MARKET_URL = "https://gamma-api.polymarket.com/markets"

_session = requests.Session()
_session.headers.update({
    "Accept": "application/json",
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

def _fetch_json(url: str, params: Optional[Dict] = None) -> Dict:
    params = params or {}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = _session.get(url, params=params, timeout=API_TIMEOUT, verify=REQUESTS_VERIFY_SSL)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.SSLError as e:
            logger.error(f"请求失败: {e} | 尝试 {attempt}/{MAX_RETRIES} | URL={url}")
        except requests.exceptions.RequestException as e:
            logger.error(f"请求失败: {e} | 尝试 {attempt}/{MAX_RETRIES} | URL={url}")
        time.sleep(min(5, 0.5 * (2 ** (attempt - 1))))
    raise requests.exceptions.RequestException(f"请求失败: 达到最大重试次数 ({MAX_RETRIES}) | URL={url}")

def get_all_slug_events() -> List[str]:
    all_slug_events: List[str] = []
    next_cursor = ""
    page_count = 0
    while True:
        page_count += 1
        params = {"closed": "false", "limit": 100}
        if next_cursor:
            params["next_cursor"] = next_cursor
        logger.info(f"   - 正在请求第 {page_count} 页...")
        try:
            data = _fetch_json(EVENTS_URL, params=params)
            for item in data:
                current_slugs = item.get('slug')
                if current_slugs:
                    all_slug_events.append(current_slugs)
            next_cursor = data[0].get('next_cursor', "") if data else ""
            if not next_cursor:
                break
            time.sleep(1)
        except requests.exceptions.RequestException as e:
            logger.error(f"请求失败: {e}")
            break
    logger.info(f"✅ 任务完成！总共获取到 {len(all_slug_events)} 个有效的 Market Slug。")
    return all_slug_events

def get_market_from_slug(eventslug: str) -> List[str]:
    url = f"{SLUG_URL}/{eventslug}"
    market_ids: List[str] = []
    try:
        event_data = _fetch_json(url)
        if event_data.get('slug') != eventslug:
            logger.warning(f"事件 slug 不匹配: {eventslug} != {event_data.get('slug')}")
            raise ValueError(f"事件 slug 不匹配: {eventslug} != {event_data.get('slug')}")
        markets = event_data.get('markets', [])
        for market in markets:
            market_ids.append(market.get('id', ""))
        return [m for m in market_ids if m]
    except requests.exceptions.RequestException as e:
        logger.error(f"请求失败: {e}")
        raise e

def get_token_from_market(market_id: str) -> List[str]:
    url = f"{MARKET_URL}/{market_id}"
    try:
        market_data = _fetch_json(url)
        tokens_str = market_data.get('clobTokenIds', "")
        tokens_list = json.loads(tokens_str) if isinstance(tokens_str, str) else tokens_str
        if tokens_list and len(tokens_list) == 2:
            yes_token = tokens_list[0]
            no_token = tokens_list[1]
            logger.info(f"   - 市场: {market_data.get('question')} | YES: {yes_token} | NO: {no_token}")
            return [yes_token, no_token]
        else:
            logger.error("❌ 警告：市场数据中未找到有效的 Token 列表。")
            raise ValueError("❌ 警告：市场数据中未找到有效的 Token 列表。")
    except requests.exceptions.RequestException as e:
        logger.error(f"请求失败: {e}")
        raise e

def load_watchlist_slugs(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    slugs = data.get("slugs", [])
    return [s for s in slugs if s]

def resolve_tokens_from_watchlist(slugs: List[str]) -> Tuple[Dict[str, str], Dict[str, Tuple[str, str]]]:
    pairs: Dict[str, str] = {}
    meta: Dict[str, Tuple[str, str]] = {}
    for slug in slugs:
        market_ids = get_market_from_slug(slug)
        for mid in market_ids:
            try:
                tokens = get_token_from_market(mid)
                if len(tokens) == 2:
                    yes_token, no_token = tokens
                    pairs[yes_token] = no_token
                    pairs[no_token] = yes_token
                    meta[yes_token] = (slug, "YES")
                    meta[no_token] = (slug, "NO")
            except Exception:
                continue
    return pairs, meta