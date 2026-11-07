import unittest

from wrf_webvis.app.core.schemas import SelectionSpec
from wrf_webvis.app.core.selection import expand_selection


class SelectionTests(unittest.TestCase):
    # 功能说明：确认显式给出的三个选择值原样返回。
    def test_values(self):
        self.assertEqual(expand_selection(SelectionSpec(values=[1, 2, 3])), [1, 2, 3])

    # 功能说明：确认起点、终点和步长组成的范围包含终点。
    def test_range(self):
        self.assertEqual(expand_selection(SelectionSpec(start=0, end=4, step=2)), [0.0, 2.0, 4.0])

    # 功能说明：选择规则为空时确认返回调用方给定的默认时次。
    def test_default(self):
        self.assertEqual(expand_selection(None, default=[0]), [0])


if __name__ == "__main__":
    unittest.main()
