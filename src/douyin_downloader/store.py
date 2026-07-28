import secrets
import time
from collections import OrderedDict
from collections.abc import Callable

from douyin_downloader.domain import AppError, ParsedVideo


class ParseStore:
    def __init__(
        self,
        ttl_seconds: int = 600,
        max_items: int = 20,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_items = max_items
        self._clock = clock
        self._items: OrderedDict[str, tuple[float, ParsedVideo]] = OrderedDict()

    def put(self, video: ParsedVideo) -> str:
        self._purge()
        while len(self._items) >= self._max_items:
            self._items.popitem(last=False)
        token = secrets.token_urlsafe(32)
        self._items[token] = (self._clock() + self._ttl, video)
        return token

    def get(self, token: str) -> ParsedVideo:
        item = self._items.get(token)
        if item is None or item[0] <= self._clock():
            self._items.pop(token, None)
            raise AppError("PARSE_EXPIRED", "解析结果已过期，请重新解析。", 410)
        return item[1]

    def _purge(self) -> None:
        now = self._clock()
        for token, (expires_at, _) in tuple(self._items.items()):
            if expires_at <= now:
                del self._items[token]
