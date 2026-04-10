from target_pipeline.processors.commodities import canonical_commodity


def test_gold_aliases():
    assert canonical_commodity("au") == "Gold"
    assert canonical_commodity("GOLD") == "Gold"


def test_unknown_title_case():
    assert canonical_commodity("vanadium") == "Vanadium"


def test_empty():
    assert canonical_commodity(None) is None
    assert canonical_commodity("  ") is None
