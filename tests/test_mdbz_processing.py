from wrf_webvis.app.core.schemas import ProcessingRequest, SelectionSpec
from wrf_processing.processing_manager import ProcessingManager


def test_mdbz_plan_uses_single_two_dimensional_file_per_time(tmp_path):
    request = ProcessingRequest(
        processed_root=tmp_path, wrfout_root=tmp_path / 'run',
        member_name='member', domain=3, variables=['mdbz'],
        times=SelectionSpec(values=[0, 1]),
    )
    plan = ProcessingManager(tmp_path).check(request)
    files = [item for item in plan.required_files if item.variable == 'mdbz']
    assert len(files) == 2
    assert all(item.dimension == '2d' for item in files)
    assert {item.path.name for item in files} == {'0h.npy', '1h.npy'}


def test_mdbz_is_available_to_variable_selection_with_reflectivity_style():
    from wrf_webvis.app.core.registry import VARIABLE_REGISTRY

    assert 'mdbz' in VARIABLE_REGISTRY
    item = VARIABLE_REGISTRY['mdbz']
    assert item.wrf_name == 'mdbz'
    assert item.dimension.value == '2d'
    assert item.needs_polar and not item.needs_height_interp
    assert item.default_cmap == VARIABLE_REGISTRY['dbz'].default_cmap
