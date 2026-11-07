from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from wrf_webvis.app.core.schemas import DashboardImage
from wrf_webvis.app.storage.dashboard_store import DashboardStore


def _image(index: int) -> DashboardImage:
    return DashboardImage(
        id=f"image-{index}",
        title=f"Image {index}",
        image_path=f"/static/figures/{index}.png",
        request={},
    )


# 功能说明：让八个线程同时向同一仪表盘添加图像，确认记录没有丢失。
def test_concurrent_image_writes_keep_every_image(tmp_path) -> None:
    store = DashboardStore(tmp_path)
    dashboard = store.create("parallel plots")
    images = [_image(index) for index in range(8)]

    with ThreadPoolExecutor(max_workers=8) as executor:
        for future in [executor.submit(store.add_image, dashboard.id, image) for image in images]:
            future.result()

    saved = DashboardStore(tmp_path).get(dashboard.id)
    assert len(DashboardStore(tmp_path).list()) == 1
    assert {image.id for image in saved.images} == {image.id for image in images}


# 功能说明：多个独立存储对象（相当于多个进程）同时写入，依赖数据库事务而不是进程内锁。
def test_independent_store_instances_do_not_lose_writes(tmp_path) -> None:
    dashboard = DashboardStore(tmp_path).create("shared")

    def add(index: int) -> None:
        DashboardStore(tmp_path).add_image(dashboard.id, _image(index))

    with ThreadPoolExecutor(max_workers=8) as executor:
        for future in [executor.submit(add, index) for index in range(16)]:
            future.result()

    saved = DashboardStore(tmp_path).get(dashboard.id)
    assert sorted(image.id for image in saved.images) == sorted(f"image-{i}" for i in range(16))
