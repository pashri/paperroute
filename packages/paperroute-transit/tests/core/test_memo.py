"""Tests for the same-object memo."""

from paperroute_transit.core.memo import SameMemo


def test_same_memo_finds_only_the_same_objects() -> None:
    """A result is found for the very inputs, not for equal copies."""
    memo: SameMemo[int] = SameMemo(size=2)
    first = [1, 2]
    assert memo.get(inputs=(first,)) is None
    assert memo.put(inputs=(first,), value=3) == 3
    assert memo.get(inputs=(first,)) == 3
    assert memo.get(inputs=([1, 2],)) is None


def test_same_memo_drops_the_oldest() -> None:
    """Past its size the least recently used result goes."""
    memo: SameMemo[str] = SameMemo(size=2)
    one, two, three = [1], [2], [3]
    memo.put(inputs=(one,), value='one')
    memo.put(inputs=(two,), value='two')
    memo.get(inputs=(one,))
    memo.put(inputs=(three,), value='three')
    assert memo.get(inputs=(two,)) is None
    assert memo.get(inputs=(one,)) == 'one'


def test_same_memo_compares_same_values_by_equality() -> None:
    """Equal `same` values hit; a different one misses."""
    memo: SameMemo[int] = SameMemo(size=2)
    held = [1]
    memo.put(inputs=(held,), value=7, same=(1000, 'a'))
    assert memo.get(inputs=(held,), same=(1000, 'a')) == 7
    assert memo.get(inputs=(held,), same=(1001, 'a')) is None
    assert memo.get(inputs=([1],), same=(1000, 'a')) is None
