from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HTML = (PROJECT_ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
JAVASCRIPT = (PROJECT_ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")


class DashboardDeletionUiTests(unittest.TestCase):
    # 功能说明：读取仪表盘页面结构，确认标题栏提供初始禁用的删除按钮。
    def test_dashboard_header_exposes_a_disabled_delete_control(self):
        self.assertIn('id="deleteDashboard"', HTML)
        self.assertIn('id="deleteDashboard" disabled', HTML)
        self.assertIn('/static/app.js?v=20261107-core', HTML)

    # 功能说明：检查顶部日志按钮的脚本，确认点击会打开并自动刷新最近日志的对话框。
    def test_topbar_log_button_opens_an_auto_refreshing_recent_log_dialog(self):
        self.assertIn('id="openLogs"', HTML)
        self.assertIn('id="logDialog"', HTML)
        self.assertIn('id="logContents"', HTML)
        self.assertIn('openLogsButton.addEventListener("click", openRecentLogs)', JAVASCRIPT)
        self.assertIn('api("/api/logs/recent?lines=200")', JAVASCRIPT)
        self.assertIn('setInterval(refreshRecentLogs, 2000)', JAVASCRIPT)

    # 功能说明：检查停止按钮绑定，确认它会向当前任务发送取消请求。
    def test_topbar_stop_button_cancels_the_active_job(self):
        self.assertIn('id="stopJob" disabled', HTML)
        self.assertIn('stopJobButton?.addEventListener("click", cancelCurrentJob)', JAVASCRIPT)
        self.assertIn('stopJobButton.disabled = false;', JAVASCRIPT)
        self.assertIn('stopJobButton.disabled = true;', JAVASCRIPT)

    # 功能说明：检查页面加载的脚本版本，确认停止功能使用更新后的前端文件。
    def test_stop_button_uses_a_new_script_version(self):
        self.assertIn('/static/app.js?v=20261107-core', HTML)

    # 功能说明：模拟确认删除当前仪表盘，确认处理器先询问用户再调用删除动作。
    def test_delete_handler_confirms_then_deletes_the_current_dashboard(self):
        self.assertIn('deleteDashboardButton.addEventListener("click", deleteCurrentDashboard)', JAVASCRIPT)
        self.assertIn('confirm(`Delete dashboard "${currentDashboard.title}"?`)', JAVASCRIPT)
        self.assertIn('api(`/api/dashboards/${dashboardId}`, { method: "DELETE" })', JAVASCRIPT)
        self.assertIn('currentDashboard = null;', JAVASCRIPT)


if __name__ == "__main__":
    unittest.main()
