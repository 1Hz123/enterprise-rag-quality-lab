"""Redis/Null cache abstraction with fail-fast, fail-open semantics.

The RAG core is synchronous and FastAPI runs sync endpoints in worker threads.
Redis is nevertheless accessed through ``redis.asyncio`` on one application-wide
background event loop; callers only see a thread-safe facade. This avoids a
synchronous Redis client in the request path and keeps the async connection pool
alive for the whole application lifetime.

Design constraints:
- Cache failures never raise into business code.
- Every Redis operation has a small timeout budget; a stuck Redis is skipped.
- After repeated errors the manager enters a cooldown and stops dialing Redis.
- Keys are always SHA-256 digests, never raw user text.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import contextvars
import hashlib
import json
import logging
import random
import struct
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from redis import asyncio as redis_asyncio

from app.config import Settings

logger = logging.getLogger(__name__)

CACHE_SCHEMA_VERSION = 1
EMBEDDING_DTYPE_FLOAT32 = 1
_EMBEDDING_HEADER = struct.Struct("<4sIHB")
_EMBEDDING_MAGIC = b"EMB1"
_MISSING = object()


class CacheBackend(Protocol):
    """Async backend contract. Callers outside this module must not import redis."""

    async def get(self, key: str) -> bytes | None: ...

    async def set(self, key: str, value: bytes, ttl: int) -> bool: ...

    async def set_many(self, values: Mapping[str, tuple[bytes, int]]) -> int: ...

    async def delete(self, key: str) -> int: ...

    async def delete_prefix(self, prefix: str) -> int: ...

    async def incr(self, key: str) -> int: ...

    async def ping(self) -> bool: ...

    async def close(self) -> None: ...


class NullCacheBackend:
    """No-op backend used when caching is disabled or unavailable."""

    async def get(self, key: str) -> bytes | None:
        return None

    async def set(self, key: str, value: bytes, ttl: int) -> bool:
        return False

    async def set_many(self, values: Mapping[str, tuple[bytes, int]]) -> int:
        return 0

    async def delete(self, key: str) -> int:
        return 0

    async def delete_prefix(self, prefix: str) -> int:
        return 0

    async def incr(self, key: str) -> int:
        return 0

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class RedisCacheBackend:
    """Redis backend with one shared async connection pool and binary-safe values."""

    def __init__(
        self,
        *,
        url: str,
        max_connections: int,
        connect_timeout: float,
        socket_timeout: float,
        health_check_interval: int,
        client: Any | None = None,
    ) -> None:
        self.url = url
        self._owns_client = client is None
        if client is not None:
            self._client = client
            self._pool = None
            return

        self._pool = redis_asyncio.ConnectionPool.from_url(
            url,
            max_connections=max_connections,
            socket_connect_timeout=connect_timeout,
            socket_timeout=socket_timeout,
            health_check_interval=health_check_interval,
            # Binary values are the primary path. Text layers decode explicitly.
            decode_responses=False,
        )
        self._client = redis_asyncio.Redis(
            connection_pool=self._pool,
            decode_responses=False,
            client_name="enterprise-rag-quality-lab",
        )

    async def get(self, key: str) -> bytes | None:
        value = await self._client.get(key)
        if value is None:
            return None
        return value if isinstance(value, bytes) else str(value).encode("utf-8")

    async def set(self, key: str, value: bytes, ttl: int) -> bool:
        result = await self._client.set(key, value, ex=max(1, int(ttl)))
        return bool(result)

    async def set_many(self, values: Mapping[str, tuple[bytes, int]]) -> int:
        if not values:
            return 0
        # transaction=True is redis-py's default and makes this batch atomic:
        # readers never observe a partially written embedding batch.
        async with self._client.pipeline(transaction=True) as pipe:
            for key, (value, ttl) in values.items():
                pipe.set(key, value, ex=max(1, int(ttl)))
            results = await pipe.execute()
        return sum(1 for result in results if result)

    async def delete(self, key: str) -> int:
        return int(await self._client.delete(key))

    async def delete_prefix(self, prefix: str) -> int:
        """Delete by prefix with SCAN + batched UNLINK, never KEYS."""

        cursor = 0
        deleted = 0
        while True:
            cursor, keys = await self._client.scan(
                cursor=cursor,
                match=f"{prefix}*",
                count=200,
            )
            if keys:
                deleted += int(await self._client.unlink(*keys))
            if cursor == 0:
                return deleted
            # Be a friendly citizen on a shared Redis instance.
            await asyncio.sleep(0.01)

    async def incr(self, key: str) -> int:
        return int(await self._client.incr(key))

    async def ping(self) -> bool:
        return bool(await self._client.ping())

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
            if self._pool is not None:
                await self._pool.disconnect()


@dataclass
class LayerMetrics:
    hits: int = 0
    misses: int = 0
    errors: int = 0
    cooldown_skips: int = 0
    singleflight_timeouts: int = 0
    saved_ms: float = 0.0
    saved_input_tokens: int = 0
    saved_output_tokens: int = 0
    saved_model_calls: int = 0
    saved_vector_calls: int = 0


@dataclass
class CacheMetrics:
    layers: dict[str, LayerMetrics] = field(default_factory=dict)

    def layer(self, name: str) -> LayerMetrics:
        return self.layers.setdefault(name, LayerMetrics())

    def snapshot(self) -> dict[str, Any]:
        totals = LayerMetrics()
        for metric in self.layers.values():
            totals.hits += metric.hits
            totals.misses += metric.misses
            totals.errors += metric.errors
            totals.cooldown_skips += metric.cooldown_skips
            totals.singleflight_timeouts += metric.singleflight_timeouts
            totals.saved_ms += metric.saved_ms
            totals.saved_input_tokens += metric.saved_input_tokens
            totals.saved_output_tokens += metric.saved_output_tokens
            totals.saved_model_calls += metric.saved_model_calls
            totals.saved_vector_calls += metric.saved_vector_calls
        request_count = totals.hits + totals.misses + totals.errors
        return {
            "hits": totals.hits,
            "misses": totals.misses,
            "errors": totals.errors,
            "cooldown_skips": totals.cooldown_skips,
            "singleflight_timeouts": totals.singleflight_timeouts,
            "requests": request_count,
            "hit_rate": round(totals.hits / request_count, 4) if request_count else 0.0,
            "saved_ms": round(totals.saved_ms, 2),
            "saved_input_tokens": totals.saved_input_tokens,
            "saved_output_tokens": totals.saved_output_tokens,
            "saved_model_calls": totals.saved_model_calls,
            "saved_vector_calls": totals.saved_vector_calls,
            "hit": totals.hits > 0,
            "layers": {
                name: {
                    "hits": metric.hits,
                    "misses": metric.misses,
                    "errors": metric.errors,
                    "cooldown_skips": metric.cooldown_skips,
                    "singleflight_timeouts": metric.singleflight_timeouts,
                    "saved_ms": round(metric.saved_ms, 2),
                    "saved_input_tokens": metric.saved_input_tokens,
                    "saved_output_tokens": metric.saved_output_tokens,
                    "saved_model_calls": metric.saved_model_calls,
                    "saved_vector_calls": metric.saved_vector_calls,
                }
                for name, metric in sorted(self.layers.items())
            },
        }


@dataclass(frozen=True, slots=True)
class CacheRead:
    """A cache read with an explicit hit flag, so a loader result is never
    mistaken for a hit or counted as saved tokens."""

    value: bytes | None
    hit: bool


@dataclass(frozen=True, slots=True)
class CacheRequest:
    key: str
    loader: Callable[[], bytes | None]
    saved_input_tokens: int = 0
    saved_output_tokens: int = 0
    saved_model_calls: int = 0
    saved_vector_calls: int = 0


class _CacheLoop:
    """Dedicated asyncio loop so sync callers never create a client per call."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_forever,
            name="rag-cache-asyncio",
            daemon=True,
        )
        self._thread.start()
        self._closed = False

    def _run_forever(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coroutine: Any, timeout: float) -> Any:
        if self._closed:
            raise RuntimeError("cache event loop is closed")
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise TimeoutError("cache operation exceeded its timeout budget") from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=2.0)
        if not self.loop.is_closed():
            self.loop.close()


class CacheManager:
    """Thread-safe facade around an async backend with metrics and singleflight."""

    def __init__(
        self,
        settings: Settings,
        *,
        backend: CacheBackend | None = None,
    ) -> None:
        self.settings = settings
        self.enabled = bool(settings.cache_enabled)
        self._backend = backend
        self._runtime: _CacheLoop | None = None
        self._started = False
        self._closed = False
        self._consecutive_errors = 0
        self._last_error: str | None = None
        self._cooldown_until = 0.0
        self._global_metrics = CacheMetrics()
        self._local = contextvars.ContextVar[CacheMetrics | None](
            "cache_request_metrics",
            default=None,
        )
        # Sync core calls are serialized per key in this process. Multi-instance
        # deployments still issue up to one upstream call per instance.
        self._flights: dict[str, tuple[threading.Lock, int]] = {}
        self._flights_guard = threading.Lock()
        self._latency_samples: dict[str, list[float]] = {}
        self._operation_timeout = max(
            0.2,
            float(settings.redis_connect_timeout)
            + float(settings.redis_socket_timeout)
            + float(settings.redis_pool_wait_timeout),
        )

        if self._backend is None:
            if not self.enabled:
                self._backend = NullCacheBackend()
            else:
                try:
                    self._backend = RedisCacheBackend(
                        url=settings.redis_url,
                        max_connections=settings.redis_max_connections,
                        connect_timeout=settings.redis_connect_timeout,
                        socket_timeout=settings.redis_socket_timeout,
                        health_check_interval=settings.redis_health_check_interval,
                    )
                except Exception as exc:  # invalid URL/import must not break startup
                    self._last_error = f"{type(exc).__name__}: {exc}"[:300]
                    self._backend = NullCacheBackend()
                    logger.warning("cache backend construction failed: %s", self._last_error)

        if self.enabled and not isinstance(self._backend, NullCacheBackend):
            self._runtime = _CacheLoop()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        if self.settings.semantic_cache_enabled:
            logger.warning(
                "SEMANTIC_CACHE_ENABLED is reserved and not part of the exact-cache path; "
                "semantic reuse is not enabled"
            )
        if not self.enabled:
            logger.info("cache disabled; using no-op backend")
            return
        if isinstance(self._backend, NullCacheBackend):
            logger.warning("cache backend unavailable; using no-op backend")
            return
        try:
            result = self._run(self._backend.ping())
            if not result:
                raise RuntimeError("REDIS PING returned false")
            self._last_error = None
            self._consecutive_errors = 0
            logger.info(
                "cache ready backend=redis env=%s prefix=%s",
                self.settings.cache_env,
                self.settings.cache_key_prefix,
            )
        except Exception as exc:
            self._record_error("cache", exc)
            self._open_cooldown()
            logger.warning(
                "cache startup unavailable; running without cache: %s",
                type(exc).__name__,
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._runtime is not None and not isinstance(self._backend, NullCacheBackend):
            with contextlib.suppress(Exception):
                self._run(self._backend.close(), timeout=2.0)
            self._runtime.close()
            self._runtime = None

    # ------------------------------------------------------------------
    # Request scoped metrics
    # ------------------------------------------------------------------
    @contextlib.contextmanager
    def request_scope(self) -> Iterator[None]:
        existing = self._local.get()
        if existing is not None:
            yield
            return
        self._local.set(CacheMetrics())
        try:
            yield
        finally:
            self._local.set(None)

    def request_metrics(self) -> dict[str, Any]:
        metrics = self._local.get()
        return metrics.snapshot() if metrics is not None else CacheMetrics().snapshot()

    def metrics_snapshot(self) -> dict[str, Any]:
        return self._global_metrics.snapshot()

    # ------------------------------------------------------------------
    # Keys / TTL
    # ------------------------------------------------------------------
    def key(
        self,
        layer: str,
        payload: Mapping[str, Any],
        *,
        index_version: str | None = None,
    ) -> str:
        canonical = json.dumps(
            {
                "cache_schema": CACHE_SCHEMA_VERSION,
                "scope": self.settings.cache_scope,
                **payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        namespace = f"{layer}:{index_version}" if index_version else layer
        return f"{self.settings.cache_key_prefix}:{self.settings.cache_env}:{namespace}:{digest}"

    def ttl(self, layer: str, *, empty: bool = False) -> int:
        if empty:
            base = self.settings.retrieval_negative_ttl
        else:
            base = {
                "embedding": self.settings.embedding_ttl,
                "retrieval": self.settings.retrieval_ttl,
                "answer": self.settings.answer_ttl,
            }.get(layer, self.settings.retrieval_ttl)
        jitter = float(self.settings.cache_ttl_jitter)
        factor = 1.0 + random.uniform(-jitter, jitter)
        return max(1, round(base * factor))

    # ------------------------------------------------------------------
    # Public sync operations
    # ------------------------------------------------------------------
    def get(
        self,
        layer: str,
        key: str,
        *,
        saved_input_tokens: int = 0,
        saved_output_tokens: int = 0,
        saved_model_calls: int = 0,
        saved_vector_calls: int = 0,
    ) -> bytes | None:
        if not self._available(layer):
            return None
        started = time.perf_counter()
        try:
            value = self._run(self._backend.get(key))
        except Exception as exc:
            self._record_error(layer, exc)
            return None
        elapsed = (time.perf_counter() - started) * 1000
        if value is None:
            self._record_miss(layer, elapsed)
            return None
        self._record_hit(
            layer,
            elapsed,
            saved_input_tokens=saved_input_tokens,
            saved_output_tokens=saved_output_tokens,
            saved_model_calls=saved_model_calls,
            saved_vector_calls=saved_vector_calls,
        )
        return value

    def set(self, layer: str, key: str, value: bytes, ttl: int) -> bool:
        if not self._available(layer):
            return False
        try:
            return bool(self._run(self._backend.set(key, value, ttl)))
        except Exception as exc:
            self._record_error(layer, exc)
            return False

    def set_many(self, layer: str, values: Mapping[str, tuple[bytes, int]]) -> int:
        if not values or not self._available(layer):
            return 0
        try:
            return int(self._run(self._backend.set_many(values)))
        except Exception as exc:
            self._record_error(layer, exc)
            return 0

    def record_saved(
        self,
        layer: str,
        *,
        saved_input_tokens: int = 0,
        saved_output_tokens: int = 0,
        saved_model_calls: int = 0,
        saved_vector_calls: int = 0,
    ) -> None:
        """Record savings discovered after a hit (e.g. replayed answer tokens)."""

        self._bump(layer, "saved_input_tokens", saved_input_tokens)
        self._bump(layer, "saved_output_tokens", saved_output_tokens)
        self._bump(layer, "saved_model_calls", saved_model_calls)
        self._bump(layer, "saved_vector_calls", saved_vector_calls)
        logger.info(
            "cache_saved layer=%s saved_input_tokens=%s saved_output_tokens=%s "
            "saved_model_calls=%s saved_vector_calls=%s",
            layer,
            saved_input_tokens,
            saved_output_tokens,
            saved_model_calls,
            saved_vector_calls,
        )

    def delete(self, layer: str, key: str) -> int:
        if not self._available(layer):
            return 0
        try:
            return int(self._run(self._backend.delete(key)))
        except Exception as exc:
            self._record_error(layer, exc)
            return 0

    def delete_prefix(self, prefix: str, *, layer: str = "admin") -> int:
        if not self._available(layer):
            return 0
        try:
            return int(
                self._run(
                    self._backend.delete_prefix(prefix),
                    timeout=max(5.0, self._operation_timeout),
                )
            )
        except Exception as exc:
            self._record_error(layer, exc)
            return 0

    def get_many_or_load(
        self,
        layer: str,
        requests: list[CacheRequest],
        *,
        ttl: int | None = None,
        empty_check: Callable[[bytes], bool] | None = None,
        empty_ttl: int | None = None,
    ) -> dict[str, CacheRead]:
        """Return values for keys, using singleflight on misses.

        Loader exceptions are never swallowed: they are business failures, not
        cache failures. Redis failures return ``CacheRead(None, False)`` and the
        caller runs its normal uncached path.
        """

        results: dict[str, CacheRead] = {}
        if not requests:
            return results
        if not self.enabled or not self._available(layer):
            # Disabled/cooldown is fail-open: run the exact same workload, but
            # mark it explicitly as not-a-hit so saved-token metrics stay honest.
            return {
                request.key: CacheRead(request.loader(), False)
                for request in requests
            }

        misses: list[CacheRequest] = []
        for request in requests:
            started = time.perf_counter()
            try:
                value = self._run(self._backend.get(request.key))
            except Exception as exc:
                self._record_error(layer, exc)
                results[request.key] = CacheRead(request.loader(), False)
                continue
            elapsed = (time.perf_counter() - started) * 1000
            if value is None:
                misses.append(request)
                continue
            self._record_hit(
                layer,
                elapsed,
                saved_input_tokens=request.saved_input_tokens,
                saved_output_tokens=request.saved_output_tokens,
                saved_model_calls=request.saved_model_calls,
                saved_vector_calls=request.saved_vector_calls,
            )
            results[request.key] = CacheRead(value, True)

        to_write: dict[str, tuple[bytes, int]] = {}
        for request in misses:
            lock, _users = self._acquire_flight(request.key)
            acquired = False
            try:
                acquired = lock.acquire(timeout=float(self.settings.cache_singleflight_timeout))
                if acquired:
                    # Double-check after acquiring: another thread may have
                    # populated the key while we waited.
                    try:
                        value = self._run(self._backend.get(request.key))
                    except Exception as exc:
                        self._record_error(layer, exc)
                        value = _MISSING
                    if value is not _MISSING and value is not None:
                        self._record_hit(
                            layer,
                            0.0,
                            saved_input_tokens=request.saved_input_tokens,
                            saved_output_tokens=request.saved_output_tokens,
                            saved_model_calls=request.saved_model_calls,
                            saved_vector_calls=request.saved_vector_calls,
                        )
                        results[request.key] = CacheRead(value, True)
                        continue
                else:
                    # Waiting longer than the budget is worse than duplicate
                    # work. Fall through to the loader without the lock.
                    self._bump(layer, "singleflight_timeouts")
                    logger.info("cache_singleflight_timeout layer=%s", layer)

                loader_started = time.perf_counter()
                value = request.loader()
                self._record_miss(layer, (time.perf_counter() - loader_started) * 1000)
                if value is None:
                    results[request.key] = CacheRead(None, False)
                    continue
                results[request.key] = CacheRead(value, False)
                if empty_check is not None and empty_check(value):
                    if empty_ttl is None:
                        continue
                    to_write[request.key] = (value, empty_ttl)
                else:
                    to_write[request.key] = (value, ttl or self.ttl(layer))
            finally:
                if acquired:
                    lock.release()
                self._release_flight(request.key)

        if to_write:
            self.set_many(layer, to_write)
        return results

    # ------------------------------------------------------------------
    # Health / metrics
    # ------------------------------------------------------------------
    def health(self, *, ping: bool = True) -> dict[str, Any]:
        if not self.enabled:
            status = "disabled"
        elif self._cooldown_active():
            status = "cooldown"
        elif isinstance(self._backend, NullCacheBackend):
            status = "degraded"
        elif ping:
            try:
                if self._run(self._backend.ping()):
                    self._last_error = None
                    self._consecutive_errors = 0
                    status = "ok"
                else:
                    status = "degraded"
            except Exception as exc:
                self._record_error("health", exc)
                status = "degraded"
        elif self._last_error:
            status = "degraded"
        else:
            status = "ok"
        return {
            "enabled": self.enabled,
            "backend": "null" if isinstance(self._backend, NullCacheBackend) else "redis",
            "status": status,
            "env": self.settings.cache_env,
            "scope": self.settings.cache_scope,
            "semantic_cache_enabled": self.settings.semantic_cache_enabled,
            "last_error": self._last_error,
            "consecutive_errors": self._consecutive_errors,
            "cooldown_remaining_s": round(self._cooldown_remaining(), 2),
            "operation_timeout_ms": round(self._operation_timeout * 1000, 1),
            "metrics": self._global_metrics.snapshot(),
        }

    def _available(self, layer: str) -> bool:
        if not self.enabled or isinstance(self._backend, NullCacheBackend):
            return False
        if self._cooldown_active():
            self._record_cooldown_skip(layer)
            return False
        return True

    def _record_hit(
        self,
        layer: str,
        latency_ms: float,
        *,
        saved_input_tokens: int,
        saved_output_tokens: int,
        saved_model_calls: int,
        saved_vector_calls: int,
    ) -> None:
        saved_ms = self._p95_ms(layer)
        self._bump(layer, "hits")
        self._bump(layer, "saved_ms", saved_ms)
        self._bump(layer, "saved_input_tokens", saved_input_tokens)
        self._bump(layer, "saved_output_tokens", saved_output_tokens)
        self._bump(layer, "saved_model_calls", saved_model_calls)
        self._bump(layer, "saved_vector_calls", saved_vector_calls)
        self._last_error = None
        self._consecutive_errors = 0
        logger.info(
            "cache_hit layer=%s latency_ms=%.2f saved_ms=%.2f saved_input_tokens=%s "
            "saved_output_tokens=%s saved_model_calls=%s saved_vector_calls=%s",
            layer,
            latency_ms,
            saved_ms,
            saved_input_tokens,
            saved_output_tokens,
            saved_model_calls,
            saved_vector_calls,
        )

    def _record_miss(self, layer: str, latency_ms: float) -> None:
        self._bump(layer, "misses")
        self._last_error = None
        self._consecutive_errors = 0
        samples = self._latency_samples.setdefault(layer, [])
        samples.append(max(0.0, latency_ms))
        if len(samples) > 200:
            del samples[: len(samples) - 200]
        logger.info("cache_miss layer=%s latency_ms=%.2f", layer, latency_ms)

    def _p95_ms(self, layer: str) -> float:
        samples = sorted(self._latency_samples.get(layer, []))
        if not samples:
            return 0.0
        index = max(0, (len(samples) * 95 + 99) // 100 - 1)
        return samples[min(index, len(samples) - 1)]

    def _record_error(self, layer: str, exc: Exception) -> None:
        self._bump(layer, "errors")
        self._consecutive_errors += 1
        self._last_error = f"{type(exc).__name__}: {exc}"[:300]
        logger.warning(
            "cache_error layer=%s error=%s consecutive_errors=%s",
            layer,
            type(exc).__name__,
            self._consecutive_errors,
        )
        if self._consecutive_errors >= self.settings.cache_error_threshold:
            self._open_cooldown()

    def _record_cooldown_skip(self, layer: str) -> None:
        self._bump(layer, "cooldown_skips")
        logger.info("cache_cooldown_skip layer=%s", layer)

    def _bump(self, layer: str, name: str, value: float = 1) -> None:
        global_metric = self._global_metrics.layer(layer)
        setattr(global_metric, name, getattr(global_metric, name) + value)
        local = self._local.get()
        if local is not None:
            local_metric = local.layer(layer)
            setattr(local_metric, name, getattr(local_metric, name) + value)

    def _open_cooldown(self) -> None:
        self._cooldown_until = time.monotonic() + float(self.settings.cache_fail_open_cooldown)
        logger.warning(
            "cache entered fail-open cooldown for %.1fs after %s consecutive errors",
            self.settings.cache_fail_open_cooldown,
            self._consecutive_errors,
        )

    def _cooldown_active(self) -> bool:
        return time.monotonic() < self._cooldown_until

    def _cooldown_remaining(self) -> float:
        return max(0.0, self._cooldown_until - time.monotonic())

    def _run(self, coroutine: Any, timeout: float | None = None) -> Any:
        if self._runtime is None:
            raise RuntimeError("cache event loop is not running")
        return self._runtime.run(coroutine, timeout or self._operation_timeout)

    # ------------------------------------------------------------------
    # Singleflight bookkeeping
    # ------------------------------------------------------------------
    def _acquire_flight(self, key: str) -> tuple[threading.Lock, int]:
        with self._flights_guard:
            lock, users = self._flights.get(key, (threading.Lock(), 0))
            self._flights[key] = (lock, users + 1)
            return lock, users + 1

    def _release_flight(self, key: str) -> None:
        with self._flights_guard:
            current = self._flights.get(key)
            if current is None:
                return
            lock, users = current
            remaining = users - 1
            if remaining <= 0 and not lock.locked():
                self._flights.pop(key, None)
            else:
                self._flights[key] = (lock, max(0, remaining))


def pack_embedding(vector: list[float], *, dimension: int, normalized: bool = True) -> bytes:
    """Pack float32 vector with explicit magic/dimension/dtype/normalization."""

    if len(vector) != dimension:
        raise ValueError(f"embedding dimension mismatch: {len(vector)} != {dimension}")
    payload = struct.pack(f"<{dimension}f", *vector)
    return _EMBEDDING_HEADER.pack(
        _EMBEDDING_MAGIC,
        dimension,
        EMBEDDING_DTYPE_FLOAT32,
        1 if normalized else 0,
    ) + payload


def unpack_embedding(payload: bytes, *, expected_dimension: int) -> list[float]:
    """Unpack and validate an embedding cache value."""

    header_size = _EMBEDDING_HEADER.size
    if len(payload) < header_size:
        raise ValueError("embedding cache value is truncated")
    magic, dimension, dtype, _normalized = _EMBEDDING_HEADER.unpack(
        payload[:header_size]
    )
    if magic != _EMBEDDING_MAGIC or dtype != EMBEDDING_DTYPE_FLOAT32:
        raise ValueError("unsupported embedding cache format")
    if dimension != expected_dimension:
        raise ValueError(
            f"embedding cache dimension mismatch: {dimension} != {expected_dimension}"
        )
    expected_size = header_size + dimension * 4
    if len(payload) != expected_size:
        raise ValueError("embedding cache value has an unexpected length")
    return list(struct.unpack(f"<{dimension}f", payload[header_size:]))


def build_cache(settings: Settings, *, backend: CacheBackend | None = None) -> CacheManager:
    return CacheManager(settings, backend=backend)
