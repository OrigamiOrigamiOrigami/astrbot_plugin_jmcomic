"""全局下载闸门：全实例同时只跑有限个本子下载（默认 1）。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

logger = logging.getLogger("astrbot")

_gate: "GlobalDownloadGate | None" = None


class GlobalDownloadGate:
    def __init__(self, limit: int = 1):
        self.limit = max(1, int(limit or 1))
        self._sem = asyncio.Semaphore(self.limit)
        self._lock = asyncio.Lock()
        self._queued = 0
        self._active: list[str] = []

    def snapshot(self) -> dict:
        """排队人数口径：ahead = 正在下的 + 已在排队的（新加入者前面还有几个）。"""
        active_n = len(self._active)
        queued = int(self._queued)
        return {
            "limit": self.limit,
            "active_count": active_n,
            "queued": queued,
            "ahead": active_n + queued,
            "busy": active_n > 0 or queued > 0,
        }

    @asynccontextmanager
    async def slot(self, label: str) -> AsyncIterator[int]:
        """占用一个下载槽。yield「加入时前面还有几个任务」（0=马上开干）。"""
        label = str(label or "download")
        async with self._lock:
            ahead = self._queued + len(self._active)
            self._queued += 1
        try:
            await self._sem.acquire()
        except BaseException:
            async with self._lock:
                self._queued = max(0, self._queued - 1)
            raise

        async with self._lock:
            self._queued = max(0, self._queued - 1)
            self._active.append(label)
            logger.info(
                "jmcomic 全局下载占用 label=%s ahead_was=%s active=%s queued=%s",
                label,
                ahead,
                len(self._active),
                self._queued,
            )
        try:
            yield ahead
        finally:
            async with self._lock:
                if label in self._active:
                    self._active.remove(label)
                elif self._active:
                    self._active.pop(0)
                logger.info(
                    "jmcomic 全局下载释放 label=%s active=%s queued=%s",
                    label,
                    len(self._active),
                    self._queued,
                )
            self._sem.release()


def get_download_gate(limit: int = 1) -> GlobalDownloadGate:
    """进程内单例；插件重载时可传新 limit 重建。"""
    global _gate
    want = max(1, int(limit or 1))
    if _gate is None or _gate.limit != want:
        if _gate is not None and (_gate.snapshot().get("busy")):
            logger.warning(
                "jmcomic 全局下载闸门 limit %s→%s，但仍有任务在跑，沿用旧闸门",
                _gate.limit,
                want,
            )
            return _gate
        _gate = GlobalDownloadGate(limit=want)
        logger.info("jmcomic 全局下载闸门已创建 limit=%s", _gate.limit)
    return _gate


def reset_download_gate_for_tests() -> None:
    global _gate
    _gate = None
