from __future__ import annotations

from pathlib import Path


def test_experiments_can_override_default_end_time_in_minutes(monkeypatch):
    from 处理数据_v3 import process_data

    monkeypatch.setattr(process_data, "VARIABLES", ("dbz_polar",))
    monkeypatch.setattr(
        process_data,
        "TIMES_MINUTES_BY_EXPERIMENT",
        {"WRF_315_land": tuple(range(0, 48 * 60 + 1, 60))},
        raising=False,
    )

    land, sea = process_data.build_requests()

    assert land.times_minutes == tuple(range(0, 48 * 60 + 1, 60))
    assert sea.times_minutes == tuple(range(0, 120 * 60 + 1, 60))


def test_v1_experiment_end_hours_are_preserved_in_v3_minutes():
    from 处理数据_v3 import process_data

    expected_end_hours = {
        "WRF_0_land": 120,
        "WRF_0_sea": 120,
        "WRF_45_land": 120,
        "WRF_45_sea": 120,
        "WRF_90_land": 84,
        "WRF_90_sea": 120,
        "WRF_135_land": 48,
        "WRF_135_sea": 69,
        "WRF_180_land": 39,
        "WRF_180_sea": 59,
        "WRF_225_land": 42,
        "WRF_225_sea": 69,
        "WRF_270_land": 66,
        "WRF_270_sea": 84,
        "WRF_315_land": 102,
        "WRF_315_sea": 102,
    }

    assert set(process_data.TIMES_MINUTES_BY_EXPERIMENT) == set(expected_end_hours)
    for experiment, end_hour in expected_end_hours.items():
        assert process_data.build_request(experiment).times_minutes == tuple(
            range(0, end_hour * 60 + 1, 60)
        )


def test_default_requests_cover_only_wrf315_land_and_sea_full_selection(monkeypatch):
    from 处理数据_v3 import process_data

    # 变量清单是用户每次批处理前会改的，这里固定一个普通变量，只检查请求的其余设置。
    monkeypatch.setattr(process_data, "VARIABLES", ("dbz_polar",))
    requests = process_data.build_requests()

    assert [request.experiments for request in requests] == [
        ("WRF_315_land",),
        ("WRF_315_sea",),
    ]
    assert all(request.domains == (3,) for request in requests)
    assert all(request.times_minutes == tuple(range(0, 102 * 60 + 1, 60)) for request in requests)
    expected_heights = tuple(range(100, 1001, 100)) + tuple(range(1250, 10001, 250))
    assert all(request.heights_m == expected_heights for request in requests)
    assert all(
        request.variables == ("dbz_polar",)
        for request in requests
    )
    assert all(request.source_root == Path("/Volumes/sea_2") for request in requests)
    assert all(request.output_root == Path("/Volumes/land_1/processed_v3") for request in requests)
    assert [request.options["wrfout_template"] for request in requests] == [
        "/Volumes/sea_2/WRF_315_land/run",
        "/Volumes/sea_2/WRF_315_sea/run",
    ]
    assert all(request.dry_run is True for request in requests)


def test_run_flag_is_required_before_entrypoint_writes(monkeypatch, capsys):
    from 处理数据_v3 import process_data

    seen = []

    class FakeManager:
        def process(self, request):
            output_seen_when_processing_started.append(capsys.readouterr().out)
            seen.append(request)
            return {"planned": [], "computed": [], "reused": [], "skipped": [], "reports": []}

    monkeypatch.setattr(process_data, "ProcessingManager", FakeManager)
    output_seen_when_processing_started = []

    process_data.main([])
    process_data.main(["--run"])

    assert [request.experiments for request in seen] == [
        ("WRF_315_land",),
        ("WRF_315_sea",),
        ("WRF_315_land",),
        ("WRF_315_sea",),
    ]
    assert [request.dry_run for request in seen] == [True, True, False, False]
    assert all("开始" in output for output in output_seen_when_processing_started)


def test_warm_core_variables_are_processed_to_15km_and_others_to_10km(monkeypatch):
    """2026-10-16：暖心（位温异常）算到 15000 m，其余变量算到 10000 m；混选时按高度拆成两组请求。"""
    from 处理数据_v3 import process_data

    normal = tuple(range(100, 1001, 100)) + tuple(range(1250, 10001, 250))
    warm = normal + tuple(range(10250, 15001, 250))
    assert process_data.HEIGHTS_M == normal
    assert process_data.WARM_CORE_HEIGHTS_M == warm

    monkeypatch.setattr(process_data, "VARIABLES", ("vt_polar", "theta_anomaly_polar", "dbz", "theta_anomaly1_polar"))
    requests = process_data.build_requests()
    assert [(r.experiments[0], tuple(r.variables), r.heights_m[-1]) for r in requests] == [
        ("WRF_315_land", ("vt_polar", "dbz"), 10000),
        ("WRF_315_land", ("theta_anomaly_polar", "theta_anomaly1_polar"), 15000),
        ("WRF_315_sea", ("vt_polar", "dbz"), 10000),
        ("WRF_315_sea", ("theta_anomaly_polar", "theta_anomaly1_polar"), 15000),
    ]
    assert process_data.build_request("WRF_315_land", variables=("theta_anomaly_polar",)).heights_m == warm
    assert process_data.build_request("WRF_315_land", variables=("vt_polar",)).heights_m == normal
    import pytest
    with pytest.raises(ValueError, match="build_requests"):
        process_data.build_request("WRF_315_land", variables=("vt_polar", "theta_anomaly_polar"))
