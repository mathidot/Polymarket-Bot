import json
import time
import requests
from typing import Dict, List, Tuple, Optional
from .logger import logger

EVENTS_URL = "https://gamma-api.polymarket.com/events"
SLUG_URL = "https://gamma-api.polymarket.com/events/slug"
MARKET_URL = "https://gamma-api.polymarket.com/markets"

def _fetch_json(url: str, params: Optional[Dict] = None, headers: Optional[Dict] = None, timeout: int = 10) -> Dict:
    """获取并解析 JSON 响应，带请求头与超时。

    Args:
        url: 请求地址。
        params: 查询参数。
        headers: 额外请求头。
        timeout: 请求超时秒数。

    Returns:
        解析后的 JSON 对象（dict 或 list）。

    Raises:
        requests.exceptions.RequestException: 非 200 或网络异常。
    """
    h = {
        "Accept": "application/json",
        "User-Agent": "polymarket-bot/slug-source"
    }
    if headers:
        h.update(headers)
    resp = requests.get(url, params=params, headers=h, timeout=timeout)
    if resp.status_code != 200:
        logger.error(f"HTTP {resp.status_code} for {url} | {resp.text[:200]}")
        resp.raise_for_status()
    return resp.json()

def get_all_slug_events() -> List[str]:
    """分页拉取未关闭事件的全部 slugs。"""
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
    """按事件 slug 获取关联的 market ID 列表。"""
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
    """按 market ID 获取二元市场的 clobTokenIds（YES/NO）。"""
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
    """从 JSON 文件加载监控的 slugs 列表。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    slugs = data.get("slugs", [])
    return [s for s in slugs if s]

def resolve_tokens_from_watchlist(slugs: List[str]) -> Tuple[Dict[str, str], Dict[str, Tuple[str, str]]]:
    """解析 watchlist slugs 为 token 配对与元数据。

    Returns:
        (pairs, meta)，其中 pairs 为互指配对映射，meta 为 token → (slug, outcome)。
    """
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
"""基于 slug 解析 Polymarket 市场与 token 的数据源模块。"""