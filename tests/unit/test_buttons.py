import pytest

from chanakya.core.buttons import decode, encode


def test_roundtrip():
    bid = encode("s", "C1", "slot", "mounting", "wall")
    assert bid == "s:C1:slot:mounting:wall"
    d = decode(bid)
    assert d.scope == "s" and d.parts == ("C1", "slot", "mounting", "wall")
    assert d.get(5) is None


def test_bad_ids():
    assert decode("hello") is None
    assert decode("z:1") is None
    with pytest.raises(ValueError):
        encode("q", "x")
    with pytest.raises(ValueError):
        encode("s", "C1", "नमस्ते")
    with pytest.raises(ValueError):
        encode("s", "x" * 300)
