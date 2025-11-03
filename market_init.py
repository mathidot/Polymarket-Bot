import time
import os
import json
import logging
from typing import Dict, List, Tuple, Any, Optional

import requests

from models import PositionInfo, ValidationError
from state import ThreadSafeState
from config import (
    YOUR_PROXY_WALLET,
    API_TIMEOUT,
    MAX_RETRIES,
    INIT_PAIR_MODE,
    CONFIG_ASSET_PAIRS,
    CONFIG_INTEREST_SLUGS,
    CONFIG_INTEREST_JSON,
    MARKET_FETCH_LIMIT,
    SIMULATION_MODE,
    SIM_INIT_POSITIONS,
    SIM_INIT_POSITIONS_JSON,
    SIM_POSITIONS_AUTO_PAIR,
)
from api import get_client, token_has_orderbook
from market_analysis import (
    get_all_slug_events,
    get_token_from_market,
    get_market_from_slug,
)


logger = logging.getLogger("polymarket_bot")


def fetch_positions_with_retry(
    max_retries: int = MAX_RETRIES,
) -> Dict[str, List[PositionInfo]]:
    for attempt in range(max_retries):
        try:
            url = f"https://data-api.polymarket.com/positions?user={YOUR_PROXY_WALLET}"
            logger.info(
                f"🔄 Fetching positions from {url} (attempt {attempt + 1}/{max_retries})"
            )

            response = requests.get(url, timeout=API_TIMEOUT)
            logger.info(f"📡 API Response Status: {response.status_code}")

            if response.status_code != 200:
                logger.error(f"❌ API Error: {response.status_code} - {response.text}")
                raise ValidationError(
                    f"API returned status code {response.status_code}"
                )

            response.raise_for_status()
            data = response.json()

            if not isinstance(data, list):
                logger.error(f"❌ Invalid response format: {type(data)}")
                logger.error(f"Response content: {data}")
                raise ValidationError(f"Invalid response format from API: {type(data)}")

            if not data:
                logger.warning(
                    "⚠️ No positions found in API response. Waiting for positions..."
                )
                return {}

            positions: Dict[str, List[PositionInfo]] = {}
            for pos in data:
                event_id = (
                    pos.get("conditionId") or pos.get("eventId") or pos.get("marketId")
                )
                if not event_id:
                    logger.warning(f"⚠️ Skipping position with no event ID: {pos}")
                    continue

                if event_id not in positions:
                    positions[event_id] = []

                try:
                    position_info = PositionInfo(
                        eventslug=pos.get("eventSlug", ""),
                        outcome=pos.get("outcome", ""),
                        asset=pos.get("asset", ""),
                        avg_price=float(pos.get("avgPrice", 0)),
                        shares=float(pos.get("size", 0)),
                        current_price=float(pos.get("curPrice", 0)),
                        initial_value=float(pos.get("initialValue", 0)),
                        current_value=float(pos.get("currentValue", 0)),
                        pnl=float(pos.get("cashPnl", 0)),
                        percent_pnl=float(pos.get("percentPnl", 0)),
                        realized_pnl=float(pos.get("realizedPnl", 0)),
                    )
                    positions[event_id].append(position_info)
                    logger.debug(f"✅ Added position: {position_info}")
                except (ValueError, TypeError) as e:
                    logger.error(f"❌ Error parsing position data: {e}")
                    logger.error(f"Problematic position data: {pos}")
                    continue

            logger.info(f"✅ Successfully fetched {len(positions)} positions")
            return positions

        except requests.RequestException as e:
            logger.error(
                f"❌ Network error in fetch_positions (attempt {attempt + 1}/{max_retries}): {str(e)}"
            )
            if attempt == max_retries - 1:
                raise
            time.sleep(2**attempt)
        except (ValueError, ValidationError) as e:
            logger.error(
                f"❌ Validation error in fetch_positions (attempt {attempt + 1}/{max_retries}): {str(e)}"
            )
            if attempt == max_retries - 1:
                raise
            time.sleep(2**attempt)
        except Exception as e:
            logger.error(
                f"❌ Unexpected error in fetch_positions (attempt {attempt + 1}/{max_retries}): {str(e)}"
            )
            if attempt == max_retries - 1:
                raise
            time.sleep(2**attempt)

    raise ValidationError("Failed to fetch positions after maximum retries")


def fetch_markets_with_retry(
    max_retries: int = MAX_RETRIES, max_count: int = MARKET_FETCH_LIMIT
) -> List[Dict[str, Any]]:
    for attempt in range(max_retries):
        try:
            logger.info(f"🔄 Fetching markets (attempt {attempt + 1}/{max_retries})")
            data_resp: Dict[str, Any] = get_client().get_simplified_markets()
            data: List[Dict[str, Any]] = data_resp.get("data", [])
            if not isinstance(data, list):
                raise ValidationError(f"Invalid markets response format: {type(data)}")
            limited = data[:max_count] if max_count and max_count > 0 else data
            logger.info(f"✅ Fetched {len(limited)} markets for pairing")
            return limited
        except Exception as e:
            logger.error(
                f"❌ Error fetching markets (attempt {attempt + 1}/{max_retries}): {e}"
            )
            if attempt == max_retries - 1:
                raise
            time.sleep(2**attempt)


def parse_config_asset_pairs(config_str: str) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    if not config_str:
        return pairs
    try:
        for item in config_str.split(","):
            item = item.strip()
            if not item:
                continue
            parts = item.split(":")
            if len(parts) != 2:
                logger.warning(
                    f"⚠️ Invalid config pair format (expected idA:idB): {item}"
                )
                continue
            a, b = parts[0].strip(), parts[1].strip()
            if not a or not b:
                logger.warning(f"⚠️ Empty token id in pair: {item}")
                continue
            pairs.append((a, b))
    except Exception as e:
        logger.error(f"❌ Failed to parse config asset pairs: {e}")
    return pairs


def parse_config_interest_slugs(config_str: str) -> List[str]:
    slugs: List[str] = []
    if not config_str:
        return slugs
    try:
        for item in config_str.split(","):
            slug = item.strip()
            if slug:
                slugs.append(slug)
    except Exception as e:
        logger.error(f"❌ Failed to parse config interest slugs: {e}")
    return slugs


def load_interest_slugs_from_json(file_path: str) -> List[str]:
    """Load interest slugs from a JSON file. Accepts either {"slugs": [...] } or a simple list [ ... ]."""
    slugs: List[str] = []
    try:
        # Resolve path relative to current working directory if not absolute
        path = file_path
        if not os.path.isabs(path):
            path = os.path.join(os.getcwd(), path)

        if not os.path.exists(path):
            logger.warning(f"⚠️ JSON 配置文件不存在：{file_path}")
            return slugs

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        import json

        data = json.loads(content)

        if isinstance(data, dict):
            arr = data.get("slugs")
            if isinstance(arr, list):
                slugs = [str(x).strip() for x in arr if x]
        elif isinstance(data, list):
            slugs = [str(x).strip() for x in data if x]
        else:
            logger.error(f"❌ JSON 格式不正确：期望对象或数组，实际为 {type(data)}")
            return []

        # Deduplicate while preserving order
        slugs = list(dict.fromkeys(slugs))
        logger.info(f"✅ 从 JSON 读取 {len(slugs)} 个关注的市场 slug")
        return slugs
    except Exception as e:
        logger.error(f"❌ 读取 JSON 配置失败（{file_path}）：{e}")
        return []


def filter_pairs_with_orderbooks(pairs: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    filtered: List[Tuple[str, str]] = []
    for a0, a1 in pairs:
        has0 = token_has_orderbook(a0)
        has1 = token_has_orderbook(a1)
        if has0 or has1:
            filtered.append((a0, a1))
        else:
            logger.info(f"⏭️ Skipping pair without orderbooks: {a0} ↔ {a1}")
    logger.info(f"✅ Filtered pairs with orderbooks: {len(filtered)} of {len(pairs)}")
    return filtered


def _parse_sim_positions_inline(data_str: str) -> List[Dict[str, Any]]:
    try:
        parsed = json.loads(data_str)
        if isinstance(parsed, dict) and "positions" in parsed:
            parsed = parsed.get("positions")
        if not isinstance(parsed, list):
            logger.warning("[SIM] sim_init_positions is not a list or positions field")
            return []
        return parsed
    except Exception as e:
        logger.warning(f"[SIM] Failed to parse sim_init_positions JSON: {e}")
        return []


def _load_sim_positions_from_file(path: str) -> List[Dict[str, Any]]:
    try:
        if not path:
            return []
        if not os.path.exists(path):
            logger.warning(f"[SIM] sim_init_positions_json file not found: {path}")
            return []
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        return _parse_sim_positions_inline(text)
    except Exception as e:
        logger.warning(f"[SIM] Failed to load sim_init_positions_json: {e}")
        return []


def load_sim_positions_from_config(state: ThreadSafeState) -> int:
    """Load initial simulated positions from .env JSON configuration.

    Expected JSON format (inline or file):
      [
        {"asset": "<token_id>", "shares": 50, "avg_price": 0.45, "eventslug": "some-event", "outcome": "Yes"},
        {"asset": "<token_id2>", "shares": 40, "avg_price": 0.55, "eventslug": "some-event", "outcome": "No"}
      ]
    """
    try:
        if not SIMULATION_MODE:
            return 0
        items: List[Dict[str, Any]] = []
        inline = (SIM_INIT_POSITIONS or "").strip()
        file_path = (SIM_INIT_POSITIONS_JSON or "").strip()

        if inline:
            items = _parse_sim_positions_inline(inline)
            logger.info(f"[SIM] Loading inline initial positions: {len(items)} entries")
        elif file_path:
            items = _load_sim_positions_from_file(file_path)
            logger.info(
                f"[SIM] Loading file-based initial positions: {len(items)} entries"
            )
        else:
            logger.info("[SIM] No initial simulated positions configured")
            return 0

        if not items:
            return 0

        # Upsert positions and optionally auto-pair by eventslug
        paired_by_slug: Dict[str, List[str]] = {}
        count = 0
        for pos in items:
            try:
                asset = str(pos.get("asset", "")).strip()
                shares = float(pos.get("shares", 0))
                avg_price = float(pos.get("avg_price", 0))
                eventslug = str(pos.get("eventslug", "") or "").strip()
                outcome = str(pos.get("outcome", "") or "").strip() or "Unknown"

                if not asset or shares <= 0 or avg_price <= 0:
                    logger.debug(f"[SIM] Skip invalid position entry: {pos}")
                    continue

                current_price = float(pos.get("current_price", avg_price))
                initial_value = avg_price * shares
                current_value = current_price * shares

                # Populate meta
                state.set_asset_meta(asset, eventslug or "SimulatedEvent", outcome)

                # Write simulated position
                state.upsert_sim_position(
                    asset=asset,
                    eventslug=eventslug or "SimulatedEvent",
                    outcome=outcome,
                    avg_price=avg_price,
                    shares=shares,
                    current_price=current_price,
                    initial_value=initial_value,
                    current_value=current_value,
                    pnl=0.0,
                    percent_pnl=0.0,
                    realized_pnl=0.0,
                )
                count += 1

                if SIM_POSITIONS_AUTO_PAIR and eventslug:
                    paired_by_slug.setdefault(eventslug, []).append(asset)
            except Exception as e:
                logger.debug(f"[SIM] Failed to apply position entry {pos}: {e}")
                continue

        # Auto-pair assets sharing the same event slug
        if SIM_POSITIONS_AUTO_PAIR and paired_by_slug:
            added_pairs = 0
            for slug, assets in paired_by_slug.items():
                if len(assets) >= 2:
                    # Pair first two for simplicity
                    a0, a1 = assets[0], assets[1]
                    state.add_asset_pair(a0, a1)
                    logger.info(f"[SIM] Auto-paired by slug '{slug}': {a0} ↔ {a1}")
                    added_pairs += 1
            if added_pairs == 0:
                logger.info("[SIM] No auto-pairs created from initial positions")

        logger.info(f"[SIM] Applied {count} initial simulated positions")
        return count
    except Exception as e:
        logger.warning(f"[SIM] Error loading simulated positions from config: {e}")
        return 0


def wait_for_initialization(state: ThreadSafeState) -> bool:
    logger.info(f"⚙️ Initialization mode: {INIT_PAIR_MODE}")
    if INIT_PAIR_MODE == "positions":
        max_retries = 60
        retry_count = 0
        while retry_count < max_retries and not state.is_shutdown():
            try:
                positions = fetch_positions_with_retry()
                for event_id, sides in positions.items():
                    logger.info(f"🔎 Event ID {event_id}: {len(sides)}")
                    if len(sides) % 2 == 0 and len(sides) > 1:
                        ids = [s.asset for s in sides]
                        state.add_asset_pair(ids[0], ids[1])
                        for s in sides[:2]:
                            state.set_asset_meta(s.asset, s.eventslug, s.outcome)
                        logger.info(f"✅ Initialized asset pair: {ids[0]} ↔ {ids[1]}")

                if state.is_initialized():
                    logger.info(
                        f"✅ Initialization complete with {len(state._initialized_assets)} assets."
                    )
                    return True

                retry_count += 1
                time.sleep(2)

            except Exception as e:
                logger.error(
                    f"❌ Error during initialization (positions mode): {str(e)}"
                )
                retry_count += 1
                time.sleep(2)

        logger.warning("❌ Initialization timed out after 2 minutes (positions mode).")
        return False

    elif INIT_PAIR_MODE == "markets":
        try:
            initialized = 0
            skipped = 0
            added_pairs = 0
            target_pairs = (
                MARKET_FETCH_LIMIT
                if MARKET_FETCH_LIMIT and MARKET_FETCH_LIMIT > 0
                else None
            )

            all_slug_events = get_all_slug_events()
            logger.info(
                f"🔎 收到 {len(all_slug_events)} 个事件 slug，开始解析市场与 token..."
            )

            stop = False
            for slug in all_slug_events:
                if stop:
                    break
                try:
                    market_ids = get_market_from_slug(slug)
                except Exception as e:
                    logger.warning(f"⚠️ 获取 slug={slug} 的市场失败：{e}")
                    continue

                for mid in market_ids:
                    if stop:
                        break
                    try:
                        token_ids = get_token_from_market(str(mid))
                    except Exception as e:
                        logger.debug(f"⏭️ 跳过市场 {mid}：无法解析 token ids（{e}）")
                        continue

                    if not token_ids or len(token_ids) < 2:
                        continue
                    a0, a1 = str(token_ids[0]), str(token_ids[1])
                    if not a0 or not a1:
                        continue

                    if not token_has_orderbook(a0) and not token_has_orderbook(a1):
                        skipped += 2
                        logger.debug(
                            f"⏭️ Skipping market pair without orderbooks: {a0} ↔ {a1}"
                        )
                        continue

                    state.add_asset_pair(a0, a1)
                    state.set_asset_meta(a0, slug or "", "Yes")
                    state.set_asset_meta(a1, slug or "", "No")
                    initialized += 2
                    added_pairs += 1

                    if target_pairs and added_pairs >= target_pairs:
                        stop = True
                        break

            if state.is_initialized():
                logger.info(
                    f"✅ Markets 初始化完成：共初始化 {initialized} 个资产（{added_pairs} 对），跳过 {skipped} 个无订单簿资产。目标对数上限={target_pairs or '不限'}"
                )
                return True
            else:
                logger.warning("⚠️ 未初始化任何市场资产。请检查数据源或筛选条件。")
                return False
        except Exception as e:
            logger.error(f"❌ Error during initialization (markets mode): {e}")
            return False

    elif INIT_PAIR_MODE == "config":
        try:
            # Path A: direct token pairs via CONFIG_ASSET_PAIRS (idA:idB,idC:idD,...)
            pairs = parse_config_asset_pairs(CONFIG_ASSET_PAIRS)
            if pairs:
                safe_pairs = filter_pairs_with_orderbooks(pairs)
                if not safe_pairs:
                    logger.warning(
                        "⚠️ No config pairs have active orderbooks. Please adjust 'config_asset_pairs'."
                    )
                else:
                    for a0, a1 in safe_pairs:
                        state.add_asset_pair(a0, a1)
                        state.set_asset_meta(a0, "ConfiguredPair", "SideA")
                        state.set_asset_meta(a1, "ConfiguredPair", "SideB")
                    logger.info(
                        f"✅ Config 初始化完成：通过 config_asset_pairs 生成 {len(safe_pairs)} 对资产，共 {len(safe_pairs) * 2} 个资产。"
                    )
                    return True
            # Path B: event slug list via JSON file (preferred)
            slugs = load_interest_slugs_from_json(CONFIG_INTEREST_JSON)
            if not slugs:
                # Fallback to ENV list if JSON empty or missing
                slugs = parse_config_interest_slugs(CONFIG_INTEREST_SLUGS)
            if not slugs:
                logger.warning(
                    "⚠️ 未提供有效的 'config_interest_slugs' 或 'config_asset_pairs'。"
                )
                return False

            added_pairs = 0
            skipped = 0
            for slug in slugs:
                try:
                    market_ids = get_market_from_slug(slug)
                except Exception as e:
                    logger.warning(f"⚠️ 获取 slug={slug} 的市场失败：{e}")
                    continue

                for mid in market_ids:
                    try:
                        token_ids = get_token_from_market(str(mid))
                    except Exception as e:
                        logger.debug(f"⏭️ 跳过市场 {mid}：无法解析 token ids（{e}）")
                        continue

                    if not token_ids or len(token_ids) < 2:
                        skipped += 1
                        continue

                    a0, a1 = str(token_ids[0]), str(token_ids[1])
                    if not a0 or not a1:
                        skipped += 1
                        continue

                    if not token_has_orderbook(a0) and not token_has_orderbook(a1):
                        skipped += 1
                        logger.debug(f"⏭️ Skipping pair without orderbooks: {a0} ↔ {a1}")
                        continue

                    state.add_asset_pair(a0, a1)
                    state.set_asset_meta(a0, slug or "ConfiguredPair", "Yes")
                    state.set_asset_meta(a1, slug or "ConfiguredPair", "No")
                    added_pairs += 1

            if state.is_initialized() and added_pairs > 0:
                logger.info(
                    f"✅ Config 初始化完成：来自 {len(slugs)} 个 slug，共初始化 {added_pairs * 2} 个资产（{added_pairs} 对），跳过 {skipped} 个。"
                )
                return True

            logger.warning(
                "⚠️ 未从配置的 slugs 生成任何资产对，请检查 slug 或市场状态。"
            )
            return False
        except Exception as e:
            logger.error(f"❌ Error during initialization (config mode): {e}")
            return False

    else:
        logger.error(f"❌ Unknown init_pair_mode: {INIT_PAIR_MODE}")
        return False
