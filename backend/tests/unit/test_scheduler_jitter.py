import uuid
import pytest

from backend.src.services.scheduler.jitter import calculate_schedule_jitter


def test_jitter_determinism_and_range():
    """T065: Assert jitter is strictly deterministic and within [0, max_jitter_seconds)."""
    conn_id = uuid.uuid4()
    
    jitter_1 = calculate_schedule_jitter(conn_id, max_jitter_seconds=900)
    jitter_2 = calculate_schedule_jitter(conn_id, max_jitter_seconds=900)
    jitter_3 = calculate_schedule_jitter(conn_id, max_jitter_seconds=900)
    
    assert jitter_1 == jitter_2 == jitter_3
    assert 0 <= jitter_1 < 900


def test_jitter_supports_string_and_uuid_types():
    """T065: Assert jitter accepts both uuid.UUID and string representation equivalently."""
    conn_id = uuid.uuid4()
    jitter_uuid = calculate_schedule_jitter(conn_id, max_jitter_seconds=900)
    jitter_str = calculate_schedule_jitter(str(conn_id), max_jitter_seconds=900)
    
    assert jitter_uuid == jitter_str


def test_jitter_custom_max_jitter():
    """T065: Assert custom max_jitter_seconds boundaries are respected."""
    conn_id = uuid.uuid4()
    for max_val in [10, 60, 300, 1800]:
        offset = calculate_schedule_jitter(conn_id, max_jitter_seconds=max_val)
        assert 0 <= offset < max_val

    with pytest.raises(ValueError):
        calculate_schedule_jitter(conn_id, max_jitter_seconds=0)

    with pytest.raises(ValueError):
        calculate_schedule_jitter(conn_id, max_jitter_seconds=-10)


def test_jitter_uniform_dispersion_across_800_uuids():
    """T065: Assert uniform dispersion across 800 random UUIDs with no clustering."""
    uuids = [uuid.uuid4() for _ in range(800)]
    max_jitter = 900
    offsets = [calculate_schedule_jitter(u, max_jitter_seconds=max_jitter) for u in uuids]
    
    # 1. All offsets within bounds
    assert all(0 <= o < max_jitter for o in offsets)
    
    # 2. Rich distinct distribution (expected ~530 distinct values out of 800 in 900 slots)
    distinct_count = len(set(offsets))
    assert distinct_count > 450
    
    # 3. Quartile dispersion check across 4 buckets: [0, 225), [225, 450), [450, 675), [675, 900)
    bucket_size = max_jitter / 4.0
    buckets = [0, 0, 0, 0]
    for o in offsets:
        idx = min(int(o / bucket_size), 3)
        buckets[idx] += 1
        
    # Expected per bucket is 200; ensure no severe skew (each bucket has between 140 and 260)
    for count in buckets:
        assert 140 <= count <= 260, f"Bucket count {count} deviates significantly from uniform expectation (200)"
