from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class RuntimeState:
    user_rate_limit: dict[tuple[int, str], float] = field(default_factory=dict)
    global_request_timestamps: list[float] = field(default_factory=list)

    def allow_global_request(self, per_minute_limit: int, now: float | None = None) -> bool:
        now = now or time.time()
        self.global_request_timestamps = [ts for ts in self.global_request_timestamps if now - ts < 60]
        if len(self.global_request_timestamps) >= per_minute_limit:
            return False
        self.global_request_timestamps.append(now)
        return True

    def allow_user_action(self, user_id: int, bucket: str, cooldown_seconds: int, now: float | None = None) -> bool:
        now = now or time.time()
        key = (user_id, bucket)
        last_time = self.user_rate_limit.get(key, 0)
        if now - last_time < cooldown_seconds:
            return False
        self.user_rate_limit[key] = now
        return True

    def prune_user_entries(self, older_than_seconds: int, now: float | None = None) -> None:
        now = now or time.time()
        stale_keys = [key for key, ts in self.user_rate_limit.items() if now - ts > older_than_seconds]
        for key in stale_keys:
            del self.user_rate_limit[key]
