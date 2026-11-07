import unittest
import tempfile
from pathlib import Path

import numpy as np

from wrf_processing.storm_motion import load_motion_for_time, local_displacement_m, motion_components, motion_file_has_times


class StormMotionTests(unittest.TestCase):
    # 功能说明：给出纬度不变、经度递增的中心轨迹，确认速度东向为正、北向接近零。
    def test_eastward_motion_positive_u(self):
        lon = np.array([120.0, 121.0, 122.0])
        lat = np.array([20.0, 20.0, 20.0])
        u, v = motion_components(lon, lat, 3600.0)
        self.assertTrue(np.all(u > 0))
        self.assertTrue(np.all(np.abs(v) < 1e-6))

    # 功能说明：比较同纬度向东一度的位置，确认局地东向位移为正、北向位移为零。
    def test_local_displacement(self):
        east, north = local_displacement_m(120.0, 20.0, 121.0, 20.0)
        self.assertGreater(east, 0)
        self.assertAlmostEqual(north, 0.0, places=6)

    # 功能说明：写入两时次移动数组，确认时次覆盖检查和按 3 小时读取 u、v 的结果。
    def test_load_motion_for_time(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "storm_motion.npy"
            np.save(path, np.asarray([[0, 1.5, -2.5, 3.0, 120.0], [3, 4.5, 5.5, 7.0, 30.0]], dtype=float))

            self.assertTrue(motion_file_has_times(path, [0, 3]))
            self.assertFalse(motion_file_has_times(path, [1]))
            self.assertEqual(load_motion_for_time(path, 3), (4.5, 5.5))


if __name__ == "__main__":
    unittest.main()
