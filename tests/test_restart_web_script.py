from __future__ import annotations

import os
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "restart_web.sh"


def _write_executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _run_script(tmp_path: Path, commands: dict[str, str]) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in commands.items():
        _write_executable(bin_dir / name, body)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(tmp_path / "home"),
            "WRF_WEBVIS_TEST_UID": "501",
            "WRF_WEBVIS_WAIT_SECONDS": "0",
            "WRF_WEBVIS_MAX_ATTEMPTS": "2",
            "COMMAND_LOG": str(tmp_path / "commands.log"),
        }
    )
    plist = tmp_path / "home/Library/LaunchAgents/com.zhuangyifan.wrfwebvis.uvicorn.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("test plist", encoding="utf-8")
    return subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_restart_reloads_launch_agent_and_waits_for_health(tmp_path: Path) -> None:
    result = _run_script(
        tmp_path,
        {
            "launchctl": 'printf "%s\\n" "$*" >> "$COMMAND_LOG"\n',
            "lsof": "exit 1\n",
            "curl": "exit 0\n",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "网页已重启" in result.stdout
    assert (tmp_path / "commands.log").read_text(encoding="utf-8").splitlines() == [
        "bootout gui/501/com.zhuangyifan.wrfwebvis.uvicorn",
        (
            "bootstrap gui/501 "
            f"{tmp_path}/home/Library/LaunchAgents/com.zhuangyifan.wrfwebvis.uvicorn.plist"
        ),
        "kickstart gui/501/com.zhuangyifan.wrfwebvis.uvicorn",
    ]


def test_restart_refuses_to_stop_an_unrelated_port_listener(tmp_path: Path) -> None:
    result = _run_script(
        tmp_path,
        {
            "launchctl": 'printf "%s\\n" "$*" >> "$COMMAND_LOG"\n',
            "lsof": (
                'if [ "$1" = "-tiTCP:8000" ]; then printf "4242\\n"; '
                'else printf "n/tmp/another-project\\n"; fi\n'
            ),
            "ps": 'printf "python -m http.server 8000\\n"\n',
            "curl": "exit 1\n",
        },
    )

    assert result.returncode != 0
    assert "不属于本项目" in result.stderr
    command_log = tmp_path / "commands.log"
    assert not command_log.exists() or "bootstrap" not in command_log.read_text(encoding="utf-8")
