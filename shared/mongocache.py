import time
from collections import OrderedDict
from collections.abc import ItemsView, ValuesView
from typing import Any, Callable, Optional, Tuple, Type

import attrs
import cattrs

from interactions.client.utils.cache import KT, VT, TTLItem, _CacheValuesView, _CacheItemsView
from interactions.client.mixins.serialization import DictSerializationMixin
from pymongo.collection import Collection


def to_dict(value: Any) -> dict:
    if isinstance(value, DictSerializationMixin):
        return value.to_dict()
    unstr = cattrs.unstructure(value)
    if isinstance(unstr, dict):
        return unstr
    return {"_value": unstr}


def from_dict(value: dict, factory: Type[VT]) -> VT:
    if hasattr(factory, "from_dict"):
        return factory.from_dict(value)
    if "_value" in value:
        return cattrs.structure(value["_value"], factory)
    return cattrs.structure(value, factory)


@attrs.define(eq=False, order=False, hash=False, kw_only=False)
class DiffableTTLItem(TTLItem[VT]):
    initial_raw_value: dict = attrs.field(repr=False)

    def diff(self) -> dict:
        return {k: v for k, v in to_dict(self.value).items() if self.initial_raw_value.get(k) != v}


class ExternalTTLCache(OrderedDict[KT, DiffableTTLItem[VT]]):
    def __init__(
        self,
        factory: Type[VT],
        ttl: int = 600,
        soft_limit: int = 50,
        hard_limit: int = 250,
        on_expire: Optional[Callable] = None,
    ) -> None:
        super().__init__()
        self.factory = factory
        self.ttl = ttl
        self.hard_limit = hard_limit
        self.soft_limit = min(soft_limit, hard_limit)
        self.on_expire = on_expire

    def __setitem__(self, key: KT, value: VT) -> None:
        expire = time.monotonic() + self.ttl
        if isinstance(value, DiffableTTLItem):
            item = value
            item.expire = expire
        else:
            item = DiffableTTLItem(value, expire, {})
            self.write_to_db(key, item)
        super().__setitem__(key, item)
        self.move_to_end(key)

        self.expire()

    def __getitem__(self, key: KT) -> VT:
        # Will not (should not) reset expiration!
        item = super().__getitem__(key)
        # self._reset_expiration(key, item)
        return item.value

    def pop(self, key: KT, default=attrs.NOTHING) -> VT:
        if key in self:
            item = self[key]
            del self[key]
            return item

        if default is attrs.NOTHING:
            raise KeyError(key)

        return default

    def get(self, key: KT, default: Optional[VT] = None, reset_expiration: bool = True) -> VT:
        item = super().get(key, default)
        if item is not default:
            if reset_expiration:
                self._reset_expiration(key, item)
            return item.value
        item = self.load_from_db(key)
        if item is not None:
            self[key] = item
            return item.value

        return default

    def values(self) -> ValuesView[VT]:
        return _CacheValuesView(self)

    def items(self) -> ItemsView:
        return _CacheItemsView(self)

    def _reset_expiration(self, key: KT, item: TTLItem) -> None:
        self.move_to_end(key)
        item.expire = time.monotonic() + self.ttl

    def _first_item(self) -> Tuple[KT, TTLItem[VT]]:
        return next(super().items().__iter__())

    def expire(self) -> None:
        """Removes expired elements from the cache."""
        if self.soft_limit and len(self) <= self.soft_limit:
            return

        if self.hard_limit:
            while len(self) > self.hard_limit:
                self._expire_first()

        timestamp = time.monotonic()
        while True:
            key, item = self._first_item()
            if item.is_expired(timestamp):
                self._expire_first()
            else:
                break

    def _expire_first(self) -> None:
        key, value = self.popitem(last=False)
        self.write_to_db(key, value)
        if self.on_expire:
            self.on_expire(key, value)

    def write_to_db(self, key: KT, item: DiffableTTLItem[VT]):
        pass

    def load_from_db(self, key: KT) -> Optional[DiffableTTLItem[VT]]:
        pass

    def flush(self) -> None:
        """Flushes the cache."""
        for key, item in self.items():
            self.write_to_db(key, item)


class MongoCache(ExternalTTLCache):
    def __init__(
        self,
        factory: Type[VT],
        collection: Collection,
        ttl: int = 600,
        soft_limit: int = 50,
        hard_limit: int = 250,
        on_expire: Optional[Callable] = None,
    ) -> None:
        super().__init__(factory=factory, ttl=ttl, soft_limit=soft_limit, hard_limit=hard_limit, on_expire=on_expire)
        self.collection = collection

    def write_to_db(self, key, item):
        self.collection.update_one({"_key": key}, {"$set": item.diff()}, upsert=True)
        item.initial_raw_value = to_dict(item.value)

    def load_from_db(self, key):
        raw = self.collection.find_one({"_key": key})
        if raw is None:
            return None
        return DiffableTTLItem(from_dict(raw, self.factory), time.monotonic() + self.ttl, raw)
