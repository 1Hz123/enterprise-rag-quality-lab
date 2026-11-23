"""Cache tests.

fakeredis covers the data path (SET/GET/TTL/SCAN/pipeline). It does not emulate
real socket stalls, transaction rollback edge cases or Redis vector search, so
the slow-connection and broken-connection tests use local async test doubles.
"""

import asyncio
import threading
import time
from datetime import date

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.cache import (
    CacheManager,
    CacheRequest,
    RedisCacheBackend,
    pack_embedding,
    unpack_embedding,
)
from app.knowledge.embeddings import HashingEmbeddings
from app.main import create_app


def _cache_settings(**overrides) -> Settings:
    values = {
        "cache_enabled": True,
        "cache_key_prefix": "raglab",
        "cache_env": "test",
        "cache_scope": "public",
        "cache_error_threshold": 1,
        "cache_fail_open_cooldown": 60,
        "cache_singleflight_timeout": 0.2,
        "redis_connect_timeout": 0.05,
        "redis_socket_timeout": 0.05,
        "redis_pool_wait_timeout": 0.05,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _manager(settings: Settings, fake=None) -> CacheManager:
    if fake is None:
        fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    backend = RedisCacheBackend(
        url="redis://fake:6379/0",
        max_connections=4,
        connect_timeout=0.05,
        socket_timeout=0.05,
        health_check_interval=10,
        client=fake,
    )
    manager = CacheManager(settings, backend=backend)
    manager.start()
    return manager


def test_disabled_cache_never_contacts_redis(tmp_path):
    settings = Settings(
        _env_file=None,
        cache_enabled=False,
        database_path=tmp_path / "db.sqlite",
    )
    manager = CacheManager(settings)
    manager.start()
    with manager.request_scope():
        value = manager.get_many_or_load(
            "embedding",
            [CacheRequest("k", lambda: b"value", saved_model_calls=1)],
            ttl=60,
        )
    assert value["k"].value == b"value"
    assert value["k"].hit is False
    assert manager.health()["status"] == "disabled"
    manager.close()


def test_hit_miss_ttl_and_key_does_not_leak_text():
    settings = _cache_settings()
    manager = _manager(settings)
    key = manager.key("retrieval", {"query": "这是我的私有问题", "top_k": 5})
    assert "私有问题" not in key
    assert key.startswith("raglab:test:retrieval:")

    assert manager.get("retrieval", key) is None
    assert manager.set("retrieval", key, b'{"items":[]}', 60) is True
    assert manager.get("retrieval", key) == b'{"items":[]}'
    assert manager._run(manager._backend._client.ttl(key)) > 0  # type: ignore[attr-defined]
    manager.close()


def test_embedding_float32_roundtrip_and_layer_metrics():
    vector = [0.1, -0.2, 0.3, 0.4]
    payload = pack_embedding(vector, dimension=4)
    decoded = unpack_embedding(payload, expected_dimension=4)
    assert decoded == pytest.approx(vector, abs=1e-6)

    settings = _cache_settings()
    manager = _manager(settings)
    embeddings = HashingEmbeddings(dimension=32, cache=manager)
    first = embeddings.embed(["Python代码"])
    second = embeddings.embed(["Python代码"])
    assert first[0] == pytest.approx(second[0], abs=1e-6)

    with manager.request_scope():
        embeddings.embed(["Python代码"])
        request_metrics = manager.request_metrics()
    assert request_metrics["hits"] >= 1
    assert request_metrics["layers"]["embedding"]["saved_model_calls"] >= 1
    manager.close()


def test_singleflight_double_check_prevents_duplicate_loader():
    settings = _cache_settings(cache_singleflight_timeout=1.0)
    manager = _manager(settings)
    key = manager.key("retrieval", {"query": "singleflight"})
    calls = 0
    calls_lock = threading.Lock()
    barrier = threading.Barrier(2)

    def loader() -> bytes:
        nonlocal calls
        time.sleep(0.15)
        with calls_lock:
            calls += 1
        return b"result"

    results: list[bytes | None] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=2)
            values = manager.get_many_or_load(
                "retrieval",
                [CacheRequest(key, loader)],
                ttl=60,
            )
            results.append(values[key].value)
        except BaseException as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    manager.close()

    assert not errors
    assert calls == 1
    assert results == [b"result", b"result"]


def test_singleflight_timeout_falls_back_to_direct_loader():
    settings = _cache_settings(cache_singleflight_timeout=0.05)
    manager = _manager(settings)
    key = manager.key("retrieval", {"query": "singleflight-timeout"})
    calls = 0
    calls_lock = threading.Lock()
    barrier = threading.Barrier(2)

    def loader() -> bytes:
        nonlocal calls
        time.sleep(0.2)
        with calls_lock:
            calls += 1
        return b"result"

    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=2)
            manager.get_many_or_load("retrieval", [CacheRequest(key, loader)], ttl=60)
        except BaseException as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    manager.close()

    assert not errors
    assert calls == 2


def test_startup_ping_failure_degrades_without_raising():
    class PingFailBackend:
        async def ping(self) -> bool:
            return False

        async def close(self) -> None:
            return None

    settings = _cache_settings()
    manager = CacheManager(settings, backend=PingFailBackend())
    manager.start()
    assert manager.health(ping=False)["status"] == "cooldown"
    manager.close()


def test_slow_backend_fails_fast_and_enters_cooldown():
    class SlowBackend:
        async def get(self, key: str) -> bytes | None:
            await asyncio.sleep(1.0)
            return None

        async def ping(self) -> bool:
            return True

    settings = _cache_settings(
        redis_connect_timeout=0.01,
        redis_socket_timeout=0.01,
        redis_pool_wait_timeout=0.01,
    )
    manager = CacheManager(settings, backend=SlowBackend())
    manager.start()
    started = time.perf_counter()
    assert manager.get("retrieval", "slow") is None
    elapsed = time.perf_counter() - started
    manager.close()

    assert elapsed < 0.8
    # SlowBackend inherits a healthy ping, then the first timed-out get opens
    # cooldown because cache_error_threshold=1.
    assert manager.health(ping=False)["status"] == "cooldown"


def test_fail_open_cooldown_and_loader_still_runs():
    class BrokenBackend:
        async def get(self, key: str) -> bytes | None:
            raise OSError("redis down")

        async def set(self, key: str, value: bytes, ttl: int) -> bool:
            raise OSError("redis down")

        async def ping(self) -> bool:
            return True

    settings = _cache_settings()
    manager = CacheManager(settings, backend=BrokenBackend())
    manager.start()
    with manager.request_scope():
        values = manager.get_many_or_load(
            "answer",
            [CacheRequest("answer-key", lambda: b"generated")],
            ttl=60,
        )
        metrics = manager.request_metrics()
    assert values["answer-key"].value == b"generated"
    assert metrics["errors"] >= 1
    assert manager.health(ping=False)["status"] == "cooldown"
    manager.close()


def test_delete_prefix_uses_scan_without_keys(monkeypatch):
    settings = _cache_settings()
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    manager = _manager(settings, fake)
    manager.set("embedding", "raglab:test:embedding:a", b"a", 60)
    manager.set("embedding", "raglab:test:embedding:b", b"b", 60)

    def forbidden_keys(*args, **kwargs):
        raise AssertionError("KEYS must not be used")

    monkeypatch.setattr(fake, "keys", forbidden_keys)
    deleted = manager.delete_prefix("raglab:test:embedding:")
    manager.close()
    assert deleted == 2


def test_negative_cache_is_opt_in_and_short_ttl():
    settings = _cache_settings(negative_cache_enabled=False)
    manager = _manager(settings)
    key = manager.key("retrieval", {"query": "empty"})

    manager.get_many_or_load(
        "retrieval",
        [CacheRequest(key, lambda: b'{"items":[]}')],
        ttl=60,
        empty_check=lambda value: value == b'{"items":[]}',
        empty_ttl=None,
    )
    assert manager.get("retrieval", key) is None

    settings2 = _cache_settings(
        negative_cache_enabled=True,
        retrieval_negative_ttl=5,
    )
    manager2 = _manager(settings2)
    key2 = manager2.key("retrieval", {"query": "empty"})
    manager2.get_many_or_load(
        "retrieval",
        [CacheRequest(key2, lambda: b'{"items":[]}')],
        ttl=60,
        empty_check=lambda value: value == b'{"items":[]}',
        empty_ttl=5,
    )
    assert manager2.get("retrieval", key2) == b'{"items":[]}'
    assert manager2._run(manager2._backend._client.ttl(key2)) <= 5  # type: ignore[attr-defined]
    manager.close()
    manager2.close()


def test_chat_uses_cache_and_health_reports_it(settings):
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    backend = RedisCacheBackend(
        url="redis://fake:6379/0",
        max_connections=4,
        connect_timeout=0.05,
        socket_timeout=0.05,
        health_check_interval=10,
        client=fake,
    )
    cached_settings = settings.model_copy(
        update={
            "cache_enabled": True,
            "experience_reuse_enabled": False,
            "auto_ingest_sample_docs": True,
        }
    )
    app = create_app(cached_settings, cache_backend=backend)
    with TestClient(app) as client:
        payload = {"question": "员工入职满三年，年假有多少天？", "as_of": "2026-06-01"}
        first = client.post("/api/v1/chat", json=payload)
        second = client.post("/api/v1/chat", json=payload)
        assert first.status_code == 200
        assert second.status_code == 200
        metrics = client.app.state.cache.metrics_snapshot()
        assert metrics["hits"] > 0
        assert metrics["layers"]["retrieval"]["hits"] >= 1
        assert metrics["layers"]["answer"]["hits"] >= 1
        health = client.get("/health").json()
        assert health["cache"]["status"] == "ok"


def test_reindex_changes_index_version_and_avoids_old_retrieval_key(settings):
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    backend = RedisCacheBackend(
        url="redis://fake:6379/0",
        max_connections=4,
        connect_timeout=0.05,
        socket_timeout=0.05,
        health_check_interval=10,
        client=fake,
    )
    cached_settings = settings.model_copy(
        update={"cache_enabled": True, "auto_ingest_sample_docs": True}
    )
    app = create_app(cached_settings, cache_backend=backend)
    with TestClient(app) as client:
        retriever = client.app.state.retriever
        question = "员工入职满三年，年假有多少天？"
        retriever.retrieve(question, top_k=5, as_of=date(2026, 6, 1))
        before = retriever.index_version()

        document_id = client.get("/api/v1/documents").json()["items"][0]["id"]
        assert client.post(f"/api/v1/documents/{document_id}/reindex").status_code == 200
        after = retriever.index_version()
        assert before != after

        before_misses = client.app.state.cache.metrics_snapshot()["layers"]["retrieval"][
            "misses"
        ]
        retriever.retrieve(question, top_k=5, as_of=date(2026, 6, 1))
        after_misses = client.app.state.cache.metrics_snapshot()["layers"]["retrieval"][
            "misses"
        ]
        assert after_misses == before_misses + 1


def test_unavailable_redis_does_not_break_startup_or_chat(settings):
    cached_settings = settings.model_copy(
        update={
            "cache_enabled": True,
            "redis_url": "redis://127.0.0.1:1/0",
            "redis_connect_timeout": 0.05,
            "redis_socket_timeout": 0.05,
            "cache_error_threshold": 1,
            "cache_fail_open_cooldown": 60,
            "auto_ingest_sample_docs": True,
        }
    )
    app = create_app(cached_settings)
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["cache"]["status"] in {"cooldown", "degraded"}
        response = client.post(
            "/api/v1/chat",
            json={"question": "员工入职满三年，年假有多少天？", "as_of": "2026-06-01"},
        )
        assert response.status_code == 200
        assert response.json()["answer"]


def test_slow_redis_keeps_api_within_budget(settings):
    class SlowBackend:
        async def get(self, key: str) -> bytes | None:
            await asyncio.sleep(1.0)
            return None

        async def set(self, key: str, value: bytes, ttl: int) -> bool:
            await asyncio.sleep(1.0)
            return False

        async def ping(self) -> bool:
            return True

        async def close(self) -> None:
            return None

    cached_settings = settings.model_copy(
        update={
            "cache_enabled": True,
            "cache_error_threshold": 1,
            "cache_fail_open_cooldown": 60,
            "auto_ingest_sample_docs": True,
        }
    )
    app = create_app(cached_settings, cache_backend=SlowBackend())
    with TestClient(app) as client:
        started = time.perf_counter()
        response = client.post(
            "/api/v1/chat",
            json={"question": "员工入职满三年，年假有多少天？", "as_of": "2026-06-01"},
        )
        elapsed = time.perf_counter() - started
        assert response.status_code == 200
        assert elapsed < 2.0


def test_cached_evaluation_matches_uncached_core_metrics(settings):
    from app.evaluation.dataset import load_dataset

    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    backend = RedisCacheBackend(
        url="redis://fake:6379/0",
        max_connections=4,
        connect_timeout=0.05,
        socket_timeout=0.05,
        health_check_interval=10,
        client=fake,
    )
    on_settings = settings.model_copy(
        update={
            "database_path": settings.database_path.with_name("cache-eval-on.db"),
            "cache_enabled": True,
            "auto_ingest_sample_docs": True,
        }
    )
    off_settings = settings.model_copy(
        update={
            "database_path": settings.database_path.with_name("cache-eval-off.db"),
            "cache_enabled": False,
            "auto_ingest_sample_docs": True,
        }
    )
    dataset = load_dataset(settings.eval_dataset_path)
    app_on = create_app(on_settings, cache_backend=backend)
    app_off = create_app(off_settings)
    with TestClient(app_on), TestClient(app_off):
        app_on.state.evaluator.evaluate(dataset, name="cache-warmup")
        report_on = app_on.state.evaluator.evaluate(dataset, name="cache-on")
        report_off = app_off.state.evaluator.evaluate(dataset, name="cache-off")

    assert report_on.metrics.pass_rate == report_off.metrics.pass_rate
    assert report_on.metrics.retrieval_hit_rate == report_off.metrics.retrieval_hit_rate
    assert report_on.metrics.citation_precision == report_off.metrics.citation_precision
    assert report_on.metrics.invalid_citation_rate == report_off.metrics.invalid_citation_rate
    assert report_on.metrics.cache_hit_cases > 0
    assert report_off.metrics.cache_hit_cases == 0


def test_index_version_changes_retrieval_namespace():
    settings = _cache_settings()
    manager = _manager(settings)
    one = manager.key("retrieval", {"query": "q"}, index_version="manifest-a")
    two = manager.key("retrieval", {"query": "q"}, index_version="manifest-b")
    manager.close()
    assert one != two
    assert ":retrieval:manifest-a:" in one
    assert ":retrieval:manifest-b:" in two
