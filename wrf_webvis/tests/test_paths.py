from pathlib import Path
import tempfile
import unittest

import numpy as np

from wrf_webvis.app.core.paths import PathManager, WrfoutDomainReader, time_dir_name
from wrf_webvis.app.core.registry import DEFAULT_DOMAIN_CONFIG
from wrf_webvis.app.core.schemas import VariableDimension


class PathManagerTests(unittest.TestCase):
    # 功能说明：只准备一号域数据却请求三号域，确认路径读取不会错误回退到一号域。
    def test_domain_reader_never_uses_d01_as_d03_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            d01 = root / "wrfout_d01_2023-09-06_000000"
            d01.touch()

            self.assertEqual(WrfoutDomainReader.find_wrfout_files(root, 1), [d01])
            self.assertEqual(WrfoutDomainReader.find_wrfout_files(root, 3), [])
            self.assertEqual(WrfoutDomainReader.find_wrfout_files(d01, 3), [])

            self.assertEqual(
                {domain: DEFAULT_DOMAIN_CONFIG[domain]["dx_km"] for domain in (1, 2, 3)},
                {1: 25.0, 2: 5.0, 3: 1.0},
            )

            config = WrfoutDomainReader.read_domain_config(
                domain=3,
                wrfout_root=root,
                fallback=DEFAULT_DOMAIN_CONFIG,
            )
            self.assertEqual(config.source, "fallback")
            self.assertEqual(config.dx_km, 1.0)
            self.assertEqual(config.dy_km, 1.0)

    # 功能说明：请求带分钟的时次路径，确认文件名同时写出小时和分钟。
    def test_subhour_time_path_uses_hour_minute_label(self):
        self.assertEqual(time_dir_name(5 / 60), "0h5min")
        self.assertEqual(time_dir_name(1 + 10 / 60), "1h10min")

    # 功能说明：生成三维变量路径，确认高度作为独立目录或文件层级出现。
    def test_3d_per_height_path(self):
        manager = PathManager("processed")
        path = manager.variable_path(
            member_name="m1",
            domain=3,
            variable="dbz",
            time_hour=0,
            dimension=VariableDimension.THREE_D,
            height_m=1000,
        )
        self.assertEqual(path, Path(__file__).resolve().parents[2] / "processed/m1/d03/dbz/0h/1000m.npy")

    # 功能说明：生成二维变量路径，确认它不包含无意义的高度层级。
    def test_2d_path(self):
        manager = PathManager("processed")
        path = manager.variable_path(
            member_name="m1",
            domain=3,
            variable="slp",
            time_hour=0,
            dimension=VariableDimension.TWO_D,
        )
        self.assertEqual(path, Path(__file__).resolve().parents[2] / "processed/m1/d03/slp/0h.npy")

    # 功能说明：同时存在旧路径和按高度路径时，确认优先选用按高度保存的文件。
    def test_existing_prefers_per_height(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = PathManager(tmpdir)
            path = manager.variable_path(
                member_name="m1",
                domain=3,
                variable="dbz",
                time_hour=0,
                dimension=VariableDimension.THREE_D,
                height_m=1000,
            )
            path.parent.mkdir(parents=True)
            np.save(path, np.zeros((2, 2)))
            existing = manager.existing_variable(
                member_name="m1",
                domain=3,
                variable="dbz",
                time_hour=0,
                dimension=VariableDimension.THREE_D,
                height_m=1000,
            )
            self.assertIsNotNone(existing)
            self.assertEqual(existing.layout, "per_height")


if __name__ == "__main__":
    unittest.main()
