"""同频道同本子下载：在飞合并 + 短时已送达幂等。"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger("astrbot")

# channel:comic_id -> Task 返回最终状态字符串
_inflight: dict[str, asyncio.Task] = {}
# channel:comic_id -> 送达 monotonic 时间
_delivered_at: dict[str, float] = {}
_DELIVERED_TTL_SEC = 15 * 60


def delivery_key(channel: str | None, comic_id: str) -> str:
    return f"{channel or 'private'}:{comic_id}"


def mark_delivered(channel: str | None, comic_id: str) -> None:
    key = delivery_key(channel, comic_id)
    _delivered_at[key] = time.monotonic()
    logger.info("jmcomic 标记已送达 key=%s", key)


def recently_delivered(channel: str | None, comic_id: str) -> bool:
    key = delivery_key(channel, comic_id)
    ts = _delivered_at.get(key)
    if ts is None:
        return False
    if time.monotonic() - ts > _DELIVERED_TTL_SEC:
        _delivered_at.pop(key, None)
        return False
    return True


def get_inflight(channel: str | None, comic_id: str) -> asyncio.Task | None:
    key = delivery_key(channel, comic_id)
    task = _inflight.get(key)
    if task is None:
        return None
    if task.done():
        _inflight.pop(key, None)
        return None
    return task


def set_inflight(channel: str | None, comic_id: str, task: asyncio.Task) -> None:
    key = delivery_key(channel, comic_id)
    _inflight[key] = task

    def _clear(t: asyncio.Task) -> None:
        cur = _inflight.get(key)
        if cur is t:
            _inflight.pop(key, None)

    task.add_done_callback(_clear)


def outcome_looks_delivered(outcome: str | None) -> bool:
    text = outcome or ""
    return "请查收" in text
