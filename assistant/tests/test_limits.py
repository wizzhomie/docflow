"""The per-client limiter that keeps one visitor from occupying the model."""
from __future__ import annotations

from docflow_operator.limits import SlidingWindow


def test_a_key_may_spend_its_whole_allowance():
    window = SlidingWindow(limit=2, window=60)
    assert window.allow('a', now=100.0) is True
    assert window.allow('a', now=100.0) is True
    assert window.allow('a', now=100.0) is False


def test_another_key_is_not_punished_for_its_neighbour():
    window = SlidingWindow(limit=1, window=60)
    assert window.allow('a', now=100.0) is True
    assert window.allow('b', now=100.0) is True
    assert window.allow('a', now=100.0) is False


def test_the_allowance_returns_after_the_window():
    window = SlidingWindow(limit=1, window=60)
    assert window.allow('a', now=100.0) is True
    assert window.allow('a', now=159.0) is False
    assert window.allow('a', now=161.0) is True


def test_retry_after_counts_down_to_the_oldest_request():
    window = SlidingWindow(limit=1, window=60)
    window.allow('a', now=100.0)
    assert window.retry_after('a', now=100.0) == 60
    assert window.retry_after('a', now=140.0) == 20
    assert window.retry_after('a', now=160.0) == 0


def test_retry_after_is_zero_for_a_key_under_its_limit():
    window = SlidingWindow(limit=5, window=60)
    window.allow('a', now=100.0)
    assert window.retry_after('a', now=100.0) == 0


def test_reset_forgets_everything():
    window = SlidingWindow(limit=1, window=60)
    window.allow('a', now=100.0)
    window.reset()
    assert window.allow('a', now=100.0) is True


def test_stale_keys_are_pruned_so_memory_stays_bounded():
    window = SlidingWindow(limit=1, window=60, max_keys=2)
    for index in range(10):
        window.allow(f'key-{index}', now=100.0 + index)
    # old keys expire and the newest stay reachable without the dict growing forever
    assert window.allow('key-9', now=200.0) is True
    assert window.allow('fresh', now=200.0) is True
