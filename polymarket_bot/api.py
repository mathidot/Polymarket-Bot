import time
import requests
from typing import Dict, List
from .config import API_TIMEOUT, MAX_RETRIES, YOUR_PROXY_WALLET
from .exceptions import NetworkError, ValidationError
from .types import PositionInfo
from .logger import logger

def fetch_positions_with_retry(max_retries: int = MAX_RETRIES) -> Dict[str, List[PositionInfo]]:
    for attempt in range(max_retries):
        try:
            url = f"https://data-api.polymarket.com/positions?user={YOUR_PROXY_WALLET}"
            logger.info(f"🔄 Fetching positions from {url} (attempt {attempt + 1}/{max_retries})")
            response = requests.get(url, timeout=API_TIMEOUT)
            logger.info(f"📡 API Response Status: {response.status_code}")
            if response.status_code != 200:
                logger.error(f"❌ API Error: {response.status_code} - {response.text}")
                raise NetworkError(f"API returned status code {response.status_code}")
            data = response.json()
            if not isinstance(data, list):
                logger.error(f"❌ Invalid response format: {type(data)}")
                logger.error(f"Response content: {data}")
                raise ValidationError(f"Invalid response format from API: {type(data)}")
            if not data:
                logger.warning("⚠️ No positions found in API response. Waiting for positions...")
                return {}
            positions: Dict[str, List[PositionInfo]] = {}
            for pos in data:
                event_id = pos.get("conditionId") or pos.get("eventId") or pos.get("marketId")
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
                        realized_pnl=float(pos.get("realizedPnl", 0))
                    )
                    positions[event_id].append(position_info)
                except (ValueError, TypeError) as e:
                    logger.error(f"❌ Error parsing position data: {e}")
                    logger.error(f"Problematic position data: {pos}")
                    continue
            logger.info(f"✅ Successfully fetched {len(positions)} positions")
            return positions
        except requests.RequestException as e:
            logger.error(f"❌ Network error in fetch_positions (attempt {attempt + 1}/{max_retries}): {str(e)}")
            if attempt == max_retries - 1:
                raise NetworkError(f"Failed to fetch positions after {max_retries} attempts: {e}")
            time.sleep(2 ** attempt)
        except (ValueError, ValidationError) as e:
            logger.error(f"❌ Validation error in fetch_positions (attempt {attempt + 1}/{max_retries}): {str(e)}")
            if attempt == max_retries - 1:
                raise ValidationError(f"Invalid data received from API: {e}")
            time.sleep(2 ** attempt)
        except Exception as e:
            logger.error(f"❌ Unexpected error in fetch_positions (attempt {attempt + 1}/{max_retries}): {str(e)}")
            if attempt == max_retries - 1:
                raise NetworkError(f"Failed to fetch positions after {max_retries} attempts: {e}")
            time.sleep(2 ** attempt)
    raise NetworkError("Failed to fetch positions after maximum retries")
