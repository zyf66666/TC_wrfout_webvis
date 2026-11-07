"""绘图按中心方案比较（2026-11-02）：成员键“试验@方案”、成员展开、编号不变、样式与图例、数据管理页路径。"""


import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pytest

from wrf_webvis.app.core.registry import plotting_variable_type_registry
from wrf_webvis.app.core.schemas import PlotRequest, PlotType, SelectionSpec, VariableConfig
from wrf_webvis.app.plotting.member_styles import (
    member_display_name, resolve_experiment_color, scheme_linestyle, split_member_key,
)
from wrf_webvis.app.services.data_manager import member_limited_times, member_time_limit, request_member_names
from wrf_webvis.app.services.plot_service import PlotService
from wrf_webvis.app.services.processed_data import ProcessedDataReader

from v3_fixtures import save_v3


def _request(**kwargs):
    base = dict(
        member_name="WRF_315_land", member_names=["WRF_315_land", "WRF_315_sea"], fig_type=PlotType.TIME_SERIES,
        variables=[VariableConfig(name="rmw", plot_type="line")], time=SelectionSpec(values=[0, 1]),
    )
    return PlotRequest(**{**base, **kwargs})


def test_reader_paths_follow_center_scheme(tmp_path):
    reader = ProcessedDataReader(tmp_path)
    assert reader.variable_path("WRF_315_land", 3, "vt1_polar") == tmp_path / "WRF_315_land/d03/vt1_polar.nc"
    assert reader.variable_path("WRF_315_land@fpcc_v2", 3, "vt1_polar") == (
        tmp_path / "centers/fpcc_v2/WRF_315_land/d03/vt1_polar.nc"
    )
    # 与中心无关的变量各方案共用
    assert reader.variable_path("WRF_315_land@fpcc_v2", 3, "theta") == tmp_path / "WRF_315_land/d03/theta.nc"
    assert reader.variable_path("WRF_315_land@fpcc_v2", 1, "land_boundary") == tmp_path / "WRF_315_land/d01/land_boundary.nc"


def test_center_schemes_expand_members_and_keep_existing_plot_ids():
    service = PlotService()
    plain = _request()
    assert request_member_names(plain) == ["WRF_315_land", "WRF_315_sea"]
    assert request_member_names(_request(center_schemes=["base"])) == ["WRF_315_land", "WRF_315_sea"]
    compared = _request(center_schemes=["base", "fpcc_v2"])
    assert request_member_names(compared) == [
        "WRF_315_land", "WRF_315_land@fpcc_v2", "WRF_315_sea", "WRF_315_sea@fpcc_v2",
    ]
    explicit = _request(member_names=["WRF_315_land@pcc_vertical"], center_schemes=["base", "fpcc_v2"])
    assert request_member_names(explicit) == ["WRF_315_land@pcc_vertical"]
    # 不比较方案时编号与加字段前一致（center_schemes 不参与哈希）
    assert service.plot_id(plain) == service.plot_id(_request(center_schemes=["base"]))
    assert service.plot_id(plain) == service.plot_id(_request(center_schemes=[]))
    assert service.plot_id(plain) != service.plot_id(compared)
    from wrf_webvis.app.plotting.plot_spec import build_plot_spec

    spec = build_plot_spec(compared, "plot.png")
    assert spec.members == request_member_names(compared)


def test_member_end_hours_apply_to_every_scheme_of_an_experiment():
    limits = {"WRF_315_land": 1}
    assert member_time_limit(limits, "WRF_315_land@fpcc_v2") == 1.0
    assert member_time_limit({"WRF_315_land@fpcc_v2": 0.5, "WRF_315_land": 1}, "WRF_315_land@fpcc_v2") == 0.5
    assert member_time_limit(limits, "WRF_315_sea@fpcc_v2") is None
    assert member_limited_times([0, 1, 2], "WRF_315_land@fpcc_v2", {"member_time_limits": limits}) == [0, 1]


def test_styles_share_experiment_color_and_mark_scheme_by_linestyle():
    assert split_member_key("WRF_315_land@fpcc_v2") == ("WRF_315_land", "fpcc_v2")
    assert member_display_name("WRF_315_land@fpcc_v2") == "WRF_315_land [fpcc_v2]"
    assert member_display_name("WRF_315_land") == "WRF_315_land"
    assert resolve_experiment_color("WRF_315_land@fpcc_v2", 3, {}) == resolve_experiment_color("WRF_315_land", 0, {})
    assert scheme_linestyle("WRF_315_land", {}) is None
    assert scheme_linestyle("WRF_315_land@fpcc_v2", {}) != scheme_linestyle("WRF_315_land@pcc_vertical", {})
    assert scheme_linestyle("WRF_315_land@fpcc_v2", {"center_scheme_linestyles": {"fpcc_v2": "-"}}) == "-"


def test_overlay_time_series_compares_center_schemes(tmp_path):
    from wrf_webvis.app.plotting.draw_main import draw_overlay_member_time_series

    for member, values in {"WRF_315_land": [30.0, 28.0], "WRF_315_land@fpcc_v2": [35.0, 31.0]}.items():
        for hour, value in enumerate(values):
            save_v3(tmp_path, member, "rmw", value, time_hour=hour)
    assert (tmp_path / "centers/fpcc_v2/WRF_315_land/d03/rmw.nc").is_file()
    fig, ax = plt.subplots()
    try:
        draw_overlay_member_time_series(
            ax=ax, member_names=["WRF_315_land", "WRF_315_land@fpcc_v2"], domain=3,
            variables={"rmw": {"enabled": True, "plot_type": "line"}}, data_root=tmp_path,
            domain_config={3: {"dx_km": 1.0, "dy_km": 1.0}}, selection={"times": [0, 1]},
            polar_dir_style="same", polar_radius_resolution_km=None,
            variable_registry=plotting_variable_type_registry(),
            format_config={"time_series": {"overlay_members": True}},
        )
        lines = {line.get_label(): line for line in ax.lines}
        assert set(lines) == {"WRF_315_land", "WRF_315_land [fpcc_v2]"}
        np.testing.assert_allclose(lines["WRF_315_land"].get_ydata(), [30.0, 28.0])
        np.testing.assert_allclose(lines["WRF_315_land [fpcc_v2]"].get_ydata(), [35.0, 31.0])
        assert lines["WRF_315_land"].get_color() == lines["WRF_315_land [fpcc_v2]"].get_color()
        assert lines["WRF_315_land"].get_linestyle() != lines["WRF_315_land [fpcc_v2]"].get_linestyle()
    finally:
        plt.close(fig)


def test_missing_scheme_data_is_reported_with_scheme_path(tmp_path):
    save_v3(tmp_path, "WRF_315_land", "rmw", 30.0, time_hour=0)  # 只有 base
    request = _request(member_names=["WRF_315_land"], center_schemes=["base", "fpcc_v2"],
                       data_root=str(tmp_path), time=SelectionSpec(values=[0]))
    result = PlotService(tmp_path / "figures").dry_run(request)
    missing = {item["path"] for item in result["plan"]["missing_files"]}
    assert str(tmp_path / "centers/fpcc_v2/WRF_315_land/d03/rmw.nc") in missing
    assert str(tmp_path / "WRF_315_land/d03/rmw.nc") not in missing


def test_data_management_paths_inventory_and_scheme_requests(tmp_path):
    from wrf_webvis.app.services.v3_management import V3ManagementService
    import 处理数据_v3.project_config as project_config

    output = tmp_path / "processed_v3"
    for relative in ("WRF_315_land/d03/ua.nc", "WRF_315_land/d03/vt1_polar.nc", "centers/fpcc_v2/WRF_315_land/d03/vt1_polar.nc"):
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    service = V3ManagementService(output_root=output, source_directories={"WRF_315_land": str(tmp_path)},
                                  default_options=project_config.OPTIONS)
    assert service._data_path("default", "WRF_315_land@fpcc_v2", 3, "vt1_polar") == output / "centers/fpcc_v2/WRF_315_land/d03/vt1_polar.nc"
    assert service._data_path("default", "WRF_315_land@fpcc_v2", 3, "ua") == output / "WRF_315_land/d03/ua.nc"
    items = {(item["experiment"], item["variable"]) for item in service.inventory()["items"]}
    assert items == {("WRF_315_land", "ua"), ("WRF_315_land", "vt1_polar"), ("WRF_315_land@fpcc_v2", "vt1_polar")}
    assert [item["id"] for item in service.center_schemes()][0] == "base"

    payload = {"experiment": "WRF_315_land", "domain": 3, "variables": ["vt1_polar"], "times_minutes": [0],
               "heights_m": [1000], "center_scheme": "fpcc_v2"}
    request, profile = service._request(payload, dry_run=True)
    assert request.center_scheme == "fpcc_v2" and profile["center_scheme"] == "fpcc_v2"
    assert request.options["center_options_by_domain"][3]["algorithm"] == "smooth_fg_float_pcc_v2"
    same, _ = service._request({**payload, "center_scheme": None, "experiment": "WRF_315_land@fpcc_v2"}, dry_run=True)
    assert same.center_scheme == "fpcc_v2"
    legacy, _ = service._request({**payload, "center_scheme": None}, dry_run=True)
    assert legacy.center_scheme is None and "center_scheme" not in legacy.options
    with pytest.raises(ValueError, match="不能再改中心参数"):
        service._request({**payload, "options": {"center_options_by_domain": {3: {"search_radius_km": 30}}}}, dry_run=True)


def test_profile_label_lists_parameters_that_differ_from_default(tmp_path):
    """参数配置 p-xxxx 的可读名字（2026-11-02）：只列与默认不同的科学参数。"""
    from wrf_webvis.app.services.v3_management import V3ManagementService
    import 处理数据_v3.project_config as project_config

    service = V3ManagementService(output_root=tmp_path, source_directories={}, default_options=project_config.OPTIONS)
    options = {**project_config.OPTIONS, "radius_km_by_domain": {2: 800.0, 3: 120.0}, "radius_resolution_km": 2.0}
    assert service.profile_label(options) == "d03 极坐标半径 120 km，径向间隔 2 km"
    assert service.profile_label(dict(project_config.OPTIONS)) == "与默认配置相同"
    shifted = {**project_config.OPTIONS, "center_options_by_domain": {2: {"search_radius_km": 30.0}, 3: {"search_radius_km": 30.0}},
               "theta_environment_ring_km": [300.0, 400.0]}
    label = service.profile_label(shifted)
    assert "d03 中心搜索半径 30 km" in label and "环境环 300–400 km" in label
