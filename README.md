# WRF WebVis

WRF WebVis 是一个本地运行的台风模拟数据网页，提供看板、绘图、处理数据 v3、后台任务和账号管理。仓库不包含实际模拟数据；运行前需准备可访问的 `wrfout` 和/或已处理数据目录。

## 安装与启动

建议使用 Python 虚拟环境。网页依赖见 `requirements-web.txt`；处理数据 v3 所需依赖见 `requirements-processing.txt`。仅查看已有切片和看板时通常只需网页依赖；执行 v3 处理任务时还需安装处理依赖。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-web.txt
python -m pip install -r requirements-processing.txt
python -m uvicorn wrf_webvis.app.main:app --host 127.0.0.1 --port 8000
```

浏览器访问 <http://127.0.0.1:8000/>。网页数据目录默认为 `wrf_webvis/storage_data/`，绘图默认读取 `/Volumes/land_1/processed_v3`，处理数据 v3 的输入和输出默认值见 `处理数据_v3/project_config.py`。部署到其他机器时，按 `wrf_webvis/app/core/config.py` 支持的 `WRF_WEBVIS_*` 环境变量设置数据路径；具体变量与用途见 [网页使用与维护说明](docs/网页使用与维护说明.md)。不要把账号数据库、看板数据库或本机数据路径提交到公开仓库。

## 功能边界

- **看板**：创建和组织看板、查看绘图、管理图片和文字批注；支持子看板。看板状态存于 `storage_data/`，图片文件位于 `wrf_webvis/app/static/figures/`。
- **绘图**：网页绘图读取已处理的 v3 NetCDF 切片，不会因绘图请求自动处理缺失数据。通过数据管理提交处理后，再到绘图页选择变量和时次。
- **数据管理与任务**：查看输入文件和已处理切片覆盖情况，预览依赖并提交 v3 处理任务；可查看任务状态和日志。
- **账号**：本机直连和公网反向代理的访问策略不同。公网部署应配置 HTTPS、反向代理和管理员账号；首次设置及权限说明见维护文档。

## 处理与绘图

处理数据 v3 的入口是 `处理数据_v3/process_data.py`，其配置在 `处理数据_v3/project_config.py`。命令行默认先预览；实际执行需要显式传入 `--run`。网页处理入口为数据管理页。绘图使用 `processed_v3` 数据，不会回退到旧版 NPY 数据。处理和绘图使用的数据根目录可分别配置，切换前请确认对应目录结构和可用空间。

## 测试

标准测试范围包括 `wrf_webvis/tests/`、`处理数据_v3/tests/`，以及仓库中对应的 Node 前端测试。可以从项目根目录运行：

```bash
python -m pytest wrf_webvis/tests 处理数据_v3/tests
```

Node 前端测试可运行 `node --test wrf_webvis/tests/*.mjs`。Python 测试需让仓库及处理模块可导入，例如设置 `PYTHONPATH=wrf_webvis:处理数据:处理数据_v3` 后运行上面的 pytest 命令。不同机器的依赖和数据挂载情况会影响测试结果；不要据此声称所有环境下测试均通过。

## 目录概览

- `wrf_webvis/`：FastAPI 网页、静态资源、存储和测试
- `处理数据_v3/`：NetCDF 处理管线及测试
- `处理数据/`：旧版处理代码，仅在相关兼容或测试场景中保留
- `server_tools/`：服务启动和维护工具
- `docs/`：使用维护说明、接口和技术记录
