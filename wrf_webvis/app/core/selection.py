from __future__ import annotations

from typing import Iterable

from .schemas import SelectionSpec


# 功能说明：把时间、高度等选择展开成具体列表，例如从0到6、每隔3取一次得到0、3、6；未指定时使用默认列表。
def expand_selection(spec: SelectionSpec | None, *, default: Iterable[float] | None = None) -> list[float]:
    if isinstance(spec, dict):
        spec = SelectionSpec(**spec)
    if spec is None:
        return list(default or [])
    count = spec.checked_count()
    if spec.values is not None:
        if spec.values or default is None:
            return list(spec.values)
        return list(default)
    if spec.start is not None and spec.end is not None:
        step = 1.0 if spec.step is None else spec.step
        return [float(spec.start) + index * step for index in range(count)]
    if default is not None:
        return list(default)
    return []


# 功能说明：把时间或高度选择展开成数值列表，供旧绘图接口使用；实际展开工作交给 expand_selection。
def selection_to_legacy_value(spec: SelectionSpec | None, default: Iterable[float] | None = None) -> list[float]:
    return expand_selection(spec, default=default)
