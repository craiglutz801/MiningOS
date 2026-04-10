from target_pipeline.models import StandardRecord
from target_pipeline.targets.builder import build_targets
from target_pipeline.targets.scorer import score_target


def _rec(
    *,
    plss: str,
    pnorm: str,
    commodity: str,
    rtype: str = "deposit",
    name: str = "Site A",
) -> StandardRecord:
    return StandardRecord(
        source="usgs",
        record_type=rtype,  # type: ignore[arg-type]
        raw_name=name,
        normalized_name=name,
        state="UT",
        county="Beaver",
        commodity=commodity,
        plss=plss,
        plss_normalized=pnorm,
        reports=["https://example.com/r1"],
        review_flags=[],
        raw={},
    )


def test_group_by_plss_and_commodity():
    rows = [
        _rec(plss="UT T1S R2W Sec 3", pnorm="UT 0010S 0020W 003", commodity="Uranium"),
        _rec(plss="UT T1S R2W Sec 3", pnorm="UT 0010S 0020W 003", commodity="Tungsten", name="Other"),
        _rec(plss="UT T1S R2W Sec 3", pnorm="UT 0010S 0020W 003", commodity="Uranium", name="Second"),
    ]
    targets = build_targets(rows)
    assert len(targets) == 2
    by_c = {t["commodity"]: t for t in targets}
    assert len(by_c["Uranium"]["deposits"]) == 2
    assert len(by_c["Tungsten"]["deposits"]) == 1


def test_claims_attach_to_same_key():
    rows = [
        _rec(plss="UT T1S R2W Sec 3", pnorm="UT 0010S 0020W 003", commodity="Uranium"),
        StandardRecord(
            source="mlrs",
            record_type="claim",
            raw_name="C1",
            normalized_name="C1",
            state="UT",
            commodity="Uranium",
            plss="UT T1S R2W Sec 3",
            plss_normalized="UT 0010S 0020W 003",
            reports=[],
            review_flags=[],
            raw={"properties": {"serial_num": "SN123"}},
        ),
    ]
    t = build_targets(rows)[0]
    assert len(t["claims"]) == 1
    st = score_target(dict(t))
    assert st["score"] >= 4
