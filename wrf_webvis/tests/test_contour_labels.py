from wrf_webvis.app.plotting.draw_variables import normalize_variable_config


# 功能说明：建立等值线绘图配置，确认无论其他设置如何都启用线上的数值标签。
def test_contour_always_enables_contour_labels():
    config = normalize_variable_config(
        "wa",
        {
            "enabled": True,
            "plot_type": "contour",
            "clabel": False,
        },
    )

    assert config["clabel"] is True
