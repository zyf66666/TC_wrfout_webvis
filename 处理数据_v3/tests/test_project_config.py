from __future__ import annotations


def test_web_and_cli_share_v3_source_and_science_defaults():
    from 处理数据_v3 import process_data, project_config
    from wrf_webvis.app.services.v3_management import V3ManagementService

    service = V3ManagementService()
    assert process_data.SOURCE_DIRECTORIES is project_config.SOURCE_DIRECTORIES
    assert process_data.OPTIONS is project_config.OPTIONS
    assert str(service.source_directories["WRF_315_land"]) == project_config.SOURCE_DIRECTORIES["WRF_315_land"]
    assert service.default_options["radius_km_by_domain"] == {"2": 800.0, "3": 300.0}
