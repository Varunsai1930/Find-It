from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from findit.web.cache import BoundedCache


def test_unrelated_cache_miss_does_not_wait_for_expensive_key():
    cache = BoundedCache(2)
    entered, release = Event(), Event()
    calls = []
    def slow():
        calls.append("slow")
        entered.set()
        assert release.wait(5)
        return "result"
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(cache.get_or_create, "slow", slow)
        try:
            assert entered.wait(2)
            same = pool.submit(cache.get_or_create, "slow", slow)
            fast = pool.submit(cache.get_or_create, "fast", lambda: "fast result")
            assert fast.result(timeout=2) == "fast result"
        finally:
            release.set()
        assert first.result(timeout=2) == same.result(timeout=2) == "result"
    assert calls == ["slow"]


def test_failed_cache_work_can_retry_and_values_are_bounded():
    cache = BoundedCache(1)
    def fail():
        raise ValueError("failed calculation")
    with pytest.raises(ValueError):
        cache.get_or_create("a", fail)
    assert cache.get_or_create("a", lambda: None) is None
    assert cache.get_or_create("a", fail) is None
    assert cache.get_or_create("b", lambda: 2) == 2
    assert cache.get_or_create("a", lambda: 3) == 3
