from pathlib import Path
import json
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch

import numpy as np

from wrf_webvis.app.core.paths import PathManager, metadata_path
from wrf_webvis.app.core.schemas import ProcessingRequest, SelectionSpec, VariableDimension
from wrf_processing.data_manager import DataManager, ProcessingPlan
from wrf_processing.processing_manager import ProcessingManager
from wrf_processing.processing_utils import processed_file_is_current, resolve_wrfout_root


class ProcessingCacheTests(unittest.TestCase):
    # 功能说明：只准备已保存的10米东西、南北风分量，检查无需原始WRF文件也能算出5、0、13、17的合成风速。
    def test_wind_speed_10_is_derived_from_cached_components_without_wrfout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProcessingManager(tmpdir)
            member_name = "member"
            for variable, values in (
                ("U10", np.asarray([[3.0, 0.0], [5.0, 8.0]], dtype=np.float32)),
                ("V10", np.asarray([[4.0, 0.0], [12.0, 15.0]], dtype=np.float32)),
            ):
                path = manager.path_manager.variable_path(
                    member_name=member_name,
                    domain=3,
                    variable=variable,
                    time_hour=0,
                    dimension=VariableDimension.TWO_D,
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                np.save(path, values)
                manager.path_manager.write_metadata(path, {"variable": variable, "time": 0})

            request = ProcessingRequest(
                processed_root=tmpdir,
                member_name=member_name,
                domain=3,
                variables=["wind_speed_10"],
                times=SelectionSpec(values=[0]),
            )

            result = manager.run(request)

            output = Path(result["processed_files"][0])
            np.testing.assert_allclose(
                np.load(output),
                np.asarray([[5.0, 0.0], [13.0, 17.0]], dtype=np.float32),
            )

    # 功能说明：请求10米风速极坐标结果，检查系统先用缓存分量生成风速5的普通网格，再把它交给极坐标转换。
    def test_wind_speed_10_polar_routes_through_cached_derived_variable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProcessingManager(tmpdir)
            member_name = "member"
            for variable, values in (
                ("U10", np.full((2, 2), 3.0, dtype=np.float32)),
                ("V10", np.full((2, 2), 4.0, dtype=np.float32)),
            ):
                path = manager.path_manager.variable_path(
                    member_name=member_name,
                    domain=3,
                    variable=variable,
                    time_hour=0,
                    dimension=VariableDimension.TWO_D,
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                np.save(path, values)
                manager.path_manager.write_metadata(path, {"variable": variable, "time": 0})

            request = ProcessingRequest(
                processed_root=tmpdir,
                member_name=member_name,
                domain=3,
                variables=["wind_speed_10_polar"],
                times=SelectionSpec(values=[0]),
            )
            output = Path(tmpdir) / "wind_speed_10_polar.npy"

            # 功能说明：代替真实极坐标转换，先检查合成风速文件已经生成且处处为5，再交回约定的输出路径。
            def fake_transform_2d(**kwargs):
                source = manager.path_manager.existing_variable(
                    member_name=member_name,
                    domain=3,
                    variable="wind_speed_10",
                    time_hour=0,
                    dimension=VariableDimension.TWO_D,
                )
                self.assertIsNotNone(source)
                np.testing.assert_allclose(np.load(source.path), np.full((2, 2), 5.0))
                return output

            manager._ensure_slp_center = lambda *_args, **_kwargs: Path(tmpdir) / "center.npy"  # type: ignore[method-assign]
            with patch(
                "wrf_processing.processing_manager.PolarTransformer.transform_2d",
                side_effect=fake_transform_2d,
            ):
                processed = manager._run_operation_variable(request, "wind_speed_10_polar")

            self.assertEqual(processed, [str(output)])

    # 功能说明：模拟读取反射率、湿度和气压，检查共生成五份结果，但同一个原始文件只打开一次、高度坐标也只读取一次。
    def test_standard_variables_share_dataset_and_vertical_coordinate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_file = Path(tmpdir) / "wrfout_d03_test"
            source_file.touch()
            manager = ProcessingManager(tmpdir)
            manager.check = lambda _request: ProcessingPlan(  # type: ignore[method-assign]
                domain_config={},
                required_files=[],
                missing_files=[object()],  # type: ignore[list-item]
                warnings=[],
            )
            manager._find_wrfout_files = lambda _request: [source_file]  # type: ignore[method-assign]
            manager._source_for_time = lambda _files, _time, _request: (source_file, 0)  # type: ignore[method-assign]

            dataset_open_count = 0
            requested_variables: list[str] = []

            class FakeDataset:
                # 功能说明：模拟打开一份气象数据文件，并把打开次数加1，便于测试是否重复读取。
                def __init__(self, _path: Path, _mode: str) -> None:
                    nonlocal dataset_open_count
                    dataset_open_count += 1

                # 功能说明：进入模拟文件的使用范围时返回对象自身，供后续读取变量的测试使用。
                def __enter__(self):
                    return self

                # 功能说明：结束模拟文件的使用范围，返回不吞掉异常的标记，让真实测试错误继续暴露。
                def __exit__(self, *_args):
                    return False

            # 功能说明：记录请求了哪个气象变量，并返回全1的小数组；气压用二维数组，其他变量用含高度的三维数组。
            def fake_getvar(_dataset, variable: str, **_kwargs):
                requested_variables.append(variable)
                if variable == "slp":
                    return np.ones((2, 2), dtype=np.float32)
                return np.ones((2, 2, 2), dtype=np.float32)

            # 功能说明：用第一层数据复制出请求数量的高度层，代替真实高度插值，帮助只测试文件共享流程。
            def fake_interplevel(data, _z, heights):
                return np.stack([np.asarray(data)[0]] * len(heights))

            netcdf_module = ModuleType("netCDF4")
            netcdf_module.Dataset = FakeDataset  # type: ignore[attr-defined]
            wrf_module = ModuleType("wrf")
            wrf_module.getvar = fake_getvar  # type: ignore[attr-defined]
            wrf_module.interplevel = fake_interplevel  # type: ignore[attr-defined]
            wrf_module.to_np = np.asarray  # type: ignore[attr-defined]

            request = ProcessingRequest(
                processed_root=tmpdir,
                wrfout_file=source_file,
                member_name="member",
                domain=3,
                variables=["dbz", "rh", "slp"],
                times=SelectionSpec(values=[0]),
                heights=SelectionSpec(values=[1000, 2000]),
            )
            with patch.dict(sys.modules, {"netCDF4": netcdf_module, "wrf": wrf_module}), patch(
                "wrf_processing.processing_manager.prepare_wrf_import"
            ):
                processed = manager._run_standard_variables(request, request.variables)

            self.assertEqual(len(processed), 5)
            self.assertEqual(dataset_open_count, 1)
            self.assertEqual(requested_variables.count("z"), 1)

    # 功能说明：在数据说明中记录一个原始文件及时次，检查请求换成另一份来源时拒绝复用，来源一致时允许复用。
    def test_processed_cache_rejects_changed_wrf_source_mapping(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata = Path(tmpdir) / "3h.npy.json"
            metadata.write_text(
                json.dumps(
                    {
                        "source_wrfout": "wrfout_d01_2023-09-01_09:00:00",
                        "source_time_index": 0,
                    }
                ),
                encoding="utf-8",
            )

            self.assertFalse(
                processed_file_is_current(
                    "RAINNC",
                    metadata,
                    source_file=Path("wrfout_d01_2023-09-01_00:00:00"),
                    source_time_index=1,
                )
            )
            self.assertTrue(
                processed_file_is_current(
                    "RAINNC",
                    metadata,
                    source_file=Path("wrfout_d01_2023-09-01_09:00:00"),
                    source_time_index=0,
                )
            )

    # 功能说明：让原始文件移动挂载目录且时间文件名去掉冒号，检查时刻相同、移动订正已记录的风数据仍可复用。
    def test_processed_cache_accepts_the_same_wrfout_after_a_mount_path_change(self):
        """Moving a mounted source must not invalidate an otherwise matching cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata = Path(tmpdir) / "2h.npy.json"
            metadata.write_text(
                json.dumps(
                    {
                        "source_wrfout": "/online1/archive/WRF_0_sea/run/wrfout_d03_2023-09-01_02:00:00",
                        "source_time_index": 0,
                        "motion_corrected": True,
                    }
                ),
                encoding="utf-8",
            )

            self.assertTrue(
                processed_file_is_current(
                    "ua",
                    metadata,
                    source_file=Path("/Volumes/sea_1/WRF_0_sea/run/wrfout_d03_2023-09-01_020000"),
                    source_time_index=0,
                )
            )

    # 功能说明：准备来源目录迁移后仍匹配的风缓存，运行基础变量流程，检查没有重新生成文件或重新读取原始气象场。
    def test_standard_cache_skips_wrfout_read_after_source_mount_is_relocated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manager = ProcessingManager(root)
            source = root / "mounted" / "wrfout_d03_2023-09-01_020000"
            source.parent.mkdir(parents=True)
            source.touch()
            output = manager.path_manager.variable_path(
                member_name="WRF_0_sea",
                domain=3,
                variable="ua",
                time_hour=2,
                dimension=VariableDimension.THREE_D,
                height_m=100,
            )
            output.parent.mkdir(parents=True)
            np.save(output, np.ones((2, 2), dtype=np.float32))
            manager.path_manager.write_metadata(
                output,
                {
                    "source_wrfout": "/online1/archive/WRF_0_sea/run/wrfout_d03_2023-09-01_02:00:00",
                    "source_time_index": 0,
                    "motion_corrected": True,
                },
            )
            request = ProcessingRequest(
                processed_root=root,
                member_name="WRF_0_sea",
                domain=3,
                variables=["ua"],
                times=SelectionSpec(values=[2]),
                heights=SelectionSpec(values=[100]),
            )
            manager._find_wrfout_files = lambda _request: [source]  # type: ignore[method-assign]
            manager._source_for_time = lambda *_args: (source, 0)  # type: ignore[method-assign]

            self.assertEqual(manager._run_standard_variables(request, ["ua"]), [])

    # 功能说明：检查普通试验和标准海洋试验各自推导到正确的默认原始目录，并能在试验run子目录找到WRF文件。
    def test_default_wrfout_root_comes_from_member_run(self):
        self.assertEqual(resolve_wrfout_root("0_land"), Path("/Volumes/WRF/0_land/run"))
        self.assertEqual(
            resolve_wrfout_root("WRF_0_sea"),
            Path("/Volumes/sea_1/WRF_0_sea/run"),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            member = Path(tmpdir) / "member"
            run_root = member / "run"
            run_root.mkdir(parents=True)
            wrfout = run_root / "wrfout_d03_000"
            wrfout.write_text("", encoding="utf-8")
            request = ProcessingRequest(
                processed_root=tmpdir,
                member_name=str(member),
                domain=3,
                variables=["slp"],
                times=SelectionSpec(values=[0]),
            )

            self.assertEqual(ProcessingManager(tmpdir)._find_wrfout_files(request), [wrfout])

    # 功能说明：请求d03却显式给出d01文件，检查文件查找结果为空，防止读错模拟区域。
    def test_explicit_wrfout_from_another_domain_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d01 = Path(tmpdir) / "wrfout_d01_2023-09-06_000000"
            d01.touch()
            request = ProcessingRequest(
                processed_root=tmpdir,
                wrfout_file=d01,
                member_name="member",
                domain=3,
                variables=["RAINNCV"],
            )

            self.assertEqual(ProcessingManager(tmpdir)._find_wrfout_files(request), [])

    # 功能说明：按不同顺序查询三份单时次文件，检查每份文件的时次数量只读取一次，后续查询复用索引。
    def test_wrfout_source_index_is_cached(self):
        manager = ProcessingManager("processed")
        files = [Path("wrfout_d03_000"), Path("wrfout_d03_001"), Path("wrfout_d03_002")]
        calls: list[Path] = []

        # 功能说明：记录哪个文件被询问，并假定它只含一个时次，用来统计建立时间索引的读取次数。
        def fake_time_count(path: Path) -> int:
            calls.append(path)
            return 1

        manager._wrfout_time_count = fake_time_count  # type: ignore[method-assign]

        self.assertEqual(manager._source_for_time(files, 0), (files[0], 0))
        self.assertEqual(manager._source_for_time(files, 2), (files[2], 0))
        self.assertEqual(manager._source_for_time(files, 1), (files[1], 0))
        self.assertEqual(calls, files)

    # 功能说明：检查逐文件时间模式能选每小时文件的首条或末条记录，也能按5分钟间隔定位文件内部的细分时刻。
    def test_per_file_time_mode_reads_one_record_from_each_hourly_file(self):
        manager = ProcessingManager("processed")
        files = [Path("wrfout_d03_000"), Path("wrfout_d03_001"), Path("wrfout_d03_002")]
        manager._wrfout_time_count = lambda _path: 12  # type: ignore[method-assign]
        request = ProcessingRequest(
            processed_root="processed",
            member_name="member",
            domain=3,
            variables=["slp"],
            options={"wrfout_time_mode": "per_file", "wrfout_time_index": 0},
        )

        self.assertEqual(manager._source_for_time(files, 0, request), (files[0], 0))
        self.assertEqual(manager._source_for_time(files, 1, request), (files[1], 0))
        self.assertEqual(manager._source_for_time(files, 2, request), (files[2], 0))

        request.options["wrfout_time_index"] = -1
        self.assertEqual(manager._source_for_time(files, 1, request), (files[1], 11))

        request.options["wrfout_interval_minutes"] = 5
        self.assertEqual(manager._source_for_time(files, 5 / 60, request), (files[0], 1))
        self.assertEqual(manager._source_for_time(files, 1 + 10 / 60, request), (files[1], 2))

    # 功能说明：请求整小时文件的第0条记录，并让统计时次操作一旦调用就失败，确认此快捷定位不需要额外打开文件。
    def test_per_file_hourly_zero_index_does_not_open_files_only_to_count_times(self):
        manager = ProcessingManager("processed")
        files = [Path("wrfout_d03_000"), Path("wrfout_d03_001"), Path("wrfout_d03_002")]
        manager._wrfout_time_count = lambda _path: self.fail("exact hourly index zero must not read Times")  # type: ignore[method-assign]
        request = ProcessingRequest(
            processed_root="processed",
            member_name="member",
            domain=3,
            variables=["slp"],
            options={"wrfout_time_mode": "per_file", "wrfout_time_index": 0},
        )

        self.assertEqual(manager._source_for_time(files, 2, request), (files[2], 0))

    # 功能说明：为不同模拟区域设置不同文件间隔和内部时间间隔，检查3、6、9、120小时都映射到正确文件和内部记录。
    def test_per_file_time_mode_uses_domain_specific_file_intervals(self):
        manager = ProcessingManager("processed")
        files = [Path(f"wrfout_d01_{index:03d}") for index in range(14)]
        manager._wrfout_time_count = lambda _path: 3  # type: ignore[method-assign]
        request = ProcessingRequest(
            processed_root="processed",
            member_name="member",
            domain=1,
            variables=["RAINNC"],
            options={
                "wrfout_time_mode": "per_file",
                "wrfout_file_interval_hours_by_domain": {1: 9, 2: 1, 3: 1},
                "wrfout_interval_minutes_by_domain": {1: 180, 2: 5, 3: 5},
                "wrfout_time_index": 0,
            },
        )

        self.assertEqual(manager._source_for_time(files, 3, request), (files[0], 1))
        self.assertEqual(manager._source_for_time(files, 6, request), (files[0], 2))
        self.assertEqual(manager._source_for_time(files, 9, request), (files[1], 0))
        self.assertEqual(manager._source_for_time(files, 120, request), (files[13], 1))

        request.domain = 2
        hourly_files = [Path(f"wrfout_d02_{index:03d}") for index in range(121)]
        self.assertEqual(manager._source_for_time(hourly_files, 120, request), (hourly_files[120], 0))

        request.domain = 3
        hourly_files = [Path(f"wrfout_d03_{index:03d}") for index in range(121)]
        self.assertEqual(manager._source_for_time(hourly_files, 120, request), (hourly_files[120], 0))

    # 功能说明：给文件设置只有一条记录，却请求按间隔应落在第12号记录的时刻，检查系统明确报告索引越界。
    def test_per_file_time_mode_rejects_unavailable_internal_domain_time(self):
        manager = ProcessingManager("processed")
        files = [Path(f"wrfout_d01_{index:03d}") for index in range(41)]
        manager._wrfout_time_count = lambda _path: 1  # type: ignore[method-assign]
        request = ProcessingRequest(
            processed_root="processed",
            member_name="member",
            domain=1,
            variables=["RAINNC"],
            options={
                "wrfout_time_mode": "per_file",
                "wrfout_file_interval_hours_by_domain": {"1": 3},
                "wrfout_interval_minutes": 5,
            },
        )

        with self.assertRaisesRegex(IndexError, "source_time_index=12"):
            manager._source_for_time(files, 1, request)

    # 功能说明：提供原始文件的真实时间表与不一致的默认间隔，检查定位优先采用真实时间表，表中没有的4小时会报错。
    def test_per_file_time_mode_prefers_wrf_times_metadata(self):
        manager = ProcessingManager("processed")
        files = [Path("wrfout_d01_000"), Path("wrfout_d01_009")]
        manager._wrfout_timestamp_index = lambda _files: [  # type: ignore[method-assign]
            (0.0, files[0], 0),
            (3.0, files[0], 1),
            (6.0, files[0], 2),
            (9.0, files[1], 0),
        ]
        request = ProcessingRequest(
            processed_root="processed",
            member_name="member",
            domain=1,
            variables=["RAINNC"],
            options={
                "wrfout_time_mode": "per_file",
                "wrfout_file_interval_hours_by_domain": {1: 3},
            },
        )

        self.assertEqual(manager._source_for_time(files, 3, request), (files[0], 1))
        self.assertEqual(manager._source_for_time(files, 9, request), (files[1], 0))
        with self.assertRaisesRegex(IndexError, "not present in wrfout Times"):
            manager._source_for_time(files, 4, request)

    # 功能说明：创建含多个实际时间字符串的小型NetCDF文件，检查读取后得到0、3、6、9、12小时及其正确文件内位置。
    def test_wrf_times_metadata_index_reads_internal_records(self):
        try:
            from netCDF4 import Dataset
        except ImportError:
            self.skipTest("netCDF4 is not installed in the local test environment")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            files = [root / "wrfout_d01_000", root / "wrfout_d01_009"]
            file_times = [
                ["2023-09-01_00:00:00", "2023-09-01_03:00:00", "2023-09-01_06:00:00"],
                ["2023-09-01_09:00:00", "2023-09-01_12:00:00"],
            ]
            for path, timestamps in zip(files, file_times):
                with Dataset(path, "w") as dataset:
                    dataset.createDimension("Time", len(timestamps))
                    dataset.createDimension("DateStrLen", 19)
                    times_var = dataset.createVariable("Times", "S1", ("Time", "DateStrLen"))
                    times_var[:] = np.asarray(
                        [[character.encode("ascii") for character in timestamp] for timestamp in timestamps],
                        dtype="S1",
                    )

            records = ProcessingManager(root)._wrfout_timestamp_index(files)

            self.assertEqual(
                records,
                [
                    (0.0, files[0], 0),
                    (3.0, files[0], 1),
                    (6.0, files[0], 2),
                    (9.0, files[1], 0),
                    (12.0, files[1], 1),
                ],
            )

    # 功能说明：先创建没有移动订正说明的风缓存，确认判为需处理；补上已经订正标记后，检查允许复用。
    def test_ua_cache_requires_motion_corrected_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = PathManager(tmpdir)
            ua_path = paths.variable_path(
                member_name="member",
                domain=3,
                variable="ua",
                time_hour=0,
                dimension=VariableDimension.THREE_D,
                height_m=1000,
            )
            ua_path.parent.mkdir(parents=True)
            np.save(ua_path, np.zeros((2, 2), dtype=np.float32))

            request = ProcessingRequest(
                processed_root=tmpdir,
                member_name="member",
                domain=3,
                variables=["ua"],
                times=SelectionSpec(values=[0]),
                heights=SelectionSpec(values=[1000]),
            )
            plan = DataManager(tmpdir).plan_for_processing(request)
            self.assertEqual(len(plan.missing_files), 1)

            metadata_path(ua_path).write_text(json.dumps({"motion_corrected": True}), encoding="utf-8")
            plan = DataManager(tmpdir).plan_for_processing(request)
            self.assertEqual(plan.missing_files, [])

    # 功能说明：准备1000米有效、2000米缺订正说明、3000米不存在的三种情况，检查待处理高度只包含2000和3000米。
    def test_pending_standard_heights_only_returns_missing_or_stale_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = PathManager(tmpdir)
            for height_m, metadata in [(1000, {"motion_corrected": True}), (2000, {})]:
                ua_path = paths.variable_path(
                    member_name="member",
                    domain=3,
                    variable="ua",
                    time_hour=0,
                    dimension=VariableDimension.THREE_D,
                    height_m=height_m,
                )
                ua_path.parent.mkdir(parents=True, exist_ok=True)
                np.save(ua_path, np.zeros((2, 2), dtype=np.float32))
                metadata_path(ua_path).write_text(json.dumps(metadata), encoding="utf-8")

            request = ProcessingRequest(
                processed_root=tmpdir,
                member_name="member",
                domain=3,
                variables=["ua"],
                times=SelectionSpec(values=[0]),
                heights=SelectionSpec(values=[1000, 2000, 3000]),
            )
            pending = ProcessingManager(tmpdir)._pending_standard_heights(request, "ua", 0, [1000, 2000, 3000])

            self.assertEqual(pending, [2000, 3000])

    # 功能说明：重复填写基础风变量和层中心极坐标变量，检查分类后的两个名单各自只保留一份。
    def test_split_variables_deduplicates_processing_request_variables(self):
        manager = ProcessingManager("processed")
        standard, operations = manager._split_variables(["ua", "ua", "ua1_polar", "ua1_polar"])

        self.assertEqual(standard, ["ua"])
        self.assertEqual(operations, ["ua1_polar"])

    # 功能说明：把风暴移动作为请求变量并指定两个时刻，检查计划正确列出两条移动数据需求和缺失记录。
    def test_storm_motion_can_be_planned_as_processing_variable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            request = ProcessingRequest(
                processed_root=tmpdir,
                member_name="member",
                domain=3,
                variables=["storm_motion"],
                times=SelectionSpec(values=[0, 1]),
            )
            manager = ProcessingManager(tmpdir)
            plan = manager.check(request)

            self.assertEqual([item.variable for item in plan.required_files], ["storm_motion", "storm_motion"])
            self.assertEqual([item.dimension for item in plan.required_files], ["motion", "motion"])
            self.assertEqual(len(plan.missing_files), 2)

    # 功能说明：预先保存所需时刻的移动速度，并让中心计算一旦调用就失败，检查系统直接复用移动文件而不继续查中心依赖。
    def test_existing_storm_motion_short_circuits_slp_dependency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = PathManager(tmpdir)
            motion_root = paths.storm_motion_root("member", 3)
            motion_root.mkdir(parents=True)
            motion_path = motion_root / "storm_motion.npy"
            np.save(motion_path, np.asarray([[0, 1.0, 2.0, 3.0, 4.0]], dtype=float))

            manager = ProcessingManager(tmpdir)

            # 功能说明：一旦程序尝试准备气压中心就抛出测试错误，用来证明已有移动数据足够时不会走多余的依赖流程。
            def fail_if_called(*args, **kwargs):
                raise AssertionError("slp_center dependency should not be checked when storm_motion exists")

            manager._ensure_slp_center = fail_if_called  # type: ignore[method-assign]
            request = ProcessingRequest(
                processed_root=tmpdir,
                member_name="member",
                domain=3,
                variables=["ua"],
                times=SelectionSpec(values=[0]),
                heights=SelectionSpec(values=[1000]),
            )

            self.assertEqual(manager._ensure_storm_motion(request, [0], None), motion_path)


if __name__ == "__main__":
    unittest.main()
