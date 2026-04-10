from target_pipeline.processors.plss import normalize_plss_key, parse_plss_components


def test_parse_t12s_r8w_sec14():
    comp = parse_plss_components("T12S R8W Sec 14", default_state="UT")
    assert comp is not None
    assert comp["state_abbr"] == "UT"
    assert comp["township"]
    assert comp["range"]
    assert comp.get("section")


def test_normalize_key_includes_state_when_prefixed():
    k = normalize_plss_key("UT T28S R11W S18", default_state="UT")
    assert k
    assert k.startswith("UT")


def test_sec_variants():
    for s in ("T30S R18W Sec 10", "Sec 10 T30S R18W"):
        comp = parse_plss_components(s, default_state="UT")
        assert comp is not None
        assert comp["township"]
        assert comp["range"]
