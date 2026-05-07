from __future__ import annotations

from mining_os.services import areas_of_focus as aof


def test_update_area_status_accepts_account_id(monkeypatch):
    captured: dict[str, object] = {}

    class _Result:
        rowcount = 1

    class _Conn:
        def execute(self, _stmt, params):
            captured.update(params)
            return _Result()

    class _Begin:
        def __enter__(self):
            return _Conn()

        def __exit__(self, exc_type, exc, tb):
            return False

    class _Engine:
        def begin(self):
            return _Begin()

    monkeypatch.setattr(aof, "_effective_account_id", lambda account_id=None: int(account_id or 77))
    monkeypatch.setattr(aof, "get_engine", lambda: _Engine())

    ok = aof.update_area_status(
        12,
        status="paid",
        blm_serial_number="UT123",
        blm_case_url="https://example.test/case",
        account_id=5,
    )

    assert ok is True
    assert captured["id"] == 12
    assert captured["status"] == "paid"
    assert captured["blm_serial_number"] == "UT123"
    assert captured["blm_case_url"] == "https://example.test/case"
    assert captured["account_id"] == 5
