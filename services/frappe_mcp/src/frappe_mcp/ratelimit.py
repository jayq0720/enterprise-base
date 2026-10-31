"""进程内限流：按键（员工 userid、进件 Token）数最近一分钟和当天的请求数。

本服务只跑一个进程（Dockerfile 里 uvicorn 不开多 worker），计数放内存即可；重启后清零。
"""

import datetime
import time
from collections import deque
from collections.abc import Callable

BEIJING = datetime.timezone(datetime.timedelta(hours=8))
MAX_KEYS = 10_000  # 防止伪造大量不同 userid 把内存撑大


def beijing_today() -> str:
    return datetime.datetime.now(BEIJING).date().isoformat()


class Limiter:
    def __init__(
        self,
        per_minute: int,
        per_day: int = 0,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], str] = beijing_today,
    ) -> None:
        # 0 表示不限
        self._clock, self._today = clock, today
        self._per_minute = per_minute
        self._per_day = per_day
        self._recent: dict[str, deque[float]] = {}
        self._daily: dict[str, int] = {}
        self._day = ""

    def allow(self, key: str) -> bool:
        if not self._per_minute and not self._per_day:
            return True
        now = self._clock()
        today = self._today()
        if today != self._day:
            self._day, self._daily = today, {}
        if self._per_day and self._daily.get(key, 0) >= self._per_day:
            return False
        if self._per_minute:
            if key not in self._recent and len(self._recent) >= MAX_KEYS:
                self._prune(now)
            hits = self._recent.setdefault(key, deque())
            while hits and now - hits[0] >= 60:
                hits.popleft()
            if len(hits) >= self._per_minute:
                return False
            hits.append(now)
        if self._per_day:
            self._daily[key] = self._daily.get(key, 0) + 1
        return True

    def _prune(self, now: float) -> None:
        for key in [k for k, v in self._recent.items() if not v or now - v[-1] >= 60]:
            del self._recent[key]
        if len(self._recent) >= MAX_KEYS:
            self._recent.clear()
