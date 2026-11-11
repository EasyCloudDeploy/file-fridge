"""FridgeFS chunk cache unit tests."""

from app.services.fridgefs.chunk_cache import ChunkCache


def test_chunk_cache_put_get_evict(tmp_path):
    cache = ChunkCache(tmp_path / "c", max_bytes=64)
    cache.put("file-a", 0, 4, b"abcd")
    assert cache.get("file-a", 0, 4) == b"abcd"
    assert cache.get("file-a", 0, 5) is None

    # Force eviction
    cache.put("file-b", 0, 64, b"x" * 64)
    stats = cache.stats()
    assert stats["used_bytes"] <= stats["max_bytes"]
    assert cache.get("file-a", 0, 4) is None
    assert cache.get("file-b", 0, 64) == b"x" * 64
