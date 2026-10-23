"""Bounded process-local cache; concurrent misses share work only for the same key."""
from collections import OrderedDict
from concurrent.futures import Future
from threading import Lock


class BoundedCache:
    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._values = OrderedDict()
        self._pending = {}
        self._lock = Lock()

    def get_or_create(self, key, factory):
        with self._lock:
            if key in self._values:
                self._values.move_to_end(key)
                return self._values[key]
            future = self._pending.get(key)
            owner = future is None
            if owner:
                future = self._pending[key] = Future()
        if not owner:
            return future.result()
        try:
            value = factory()
        except BaseException as exc:
            with self._lock:
                del self._pending[key]
            future.set_exception(exc)
            raise
        with self._lock:
            self._values[key] = value
            while len(self._values) > self.capacity:
                self._values.popitem(last=False)
            del self._pending[key]
        future.set_result(value)
        return value
