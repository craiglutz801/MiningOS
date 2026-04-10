from target_pipeline.filters import match_target_mineral


def test_uranium_variants():
    assert match_target_mineral("uranium") == "Uranium"
    assert match_target_mineral("Some U3O8 ore") == "Uranium"
    assert match_target_mineral("commodity | u") == "Uranium"


def test_tungsten_and_scandium():
    assert match_target_mineral("tungsten ore") == "Tungsten"
    assert match_target_mineral("scandium") == "Scandium"


def test_no_match_gold():
    assert match_target_mineral("gold silver") is None
