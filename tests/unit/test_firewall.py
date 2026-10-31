from chanakya.truth.firewall import NumberRegistry, normalise


def test_registered_numbers_pass():
    r = NumberRegistry()
    s = r.inr(62990, "price:TV-MER-55-QL120@RD-JUHU")
    assert s == "₹62,990"
    assert r.check(f"Daam {s} hai") == []
    assert r.sources == ["price:TV-MER-55-QL120@RD-JUHU"]


def test_unregistered_number_is_violation():
    r = NumberRegistry()
    r.inr(62990, "x")
    assert r.check("Sirf ₹40,000 mein") == ["40,000"]


def test_static_text_numerals_allowed():
    r = NumberRegistry()
    r.static("Bas 2 sawaal, 4K")
    assert r.check("2 sawaal") == []
    assert r.check("4K TV") == []
    assert normalise("7.50") == "7.5" and normalise("1,09,990") == "109990"
