from __future__ import annotations

from .core.config import absolute_path

import argparse
import json
from pathlib import Path



# 功能说明：命令行入口：绘图（plot）、清理未引用图片（cleanup-figures）、账号（users，2026-11-06）。
def main() -> None:
    parser = argparse.ArgumentParser(description="WRF webvis command line tools")
    sub = parser.add_subparsers(dest="command", required=True)

    plot_parser = sub.add_parser("plot")
    plot_parser.add_argument("--config", required=True)
    plot_parser.add_argument("--dry-run", action="store_true")

    cleanup_parser = sub.add_parser(
        "cleanup-figures",
        help="列出（或用 --apply 移走）没有被任何看板引用的图片文件",
    )
    cleanup_parser.add_argument("--apply", action="store_true", help="把未引用图片移到 storage_data/figures_trash/<时间>/，不直接删除")
    cleanup_parser.add_argument("--min-age-hours", type=float, default=24.0, help="只处理修改时间早于该小时数的文件，默认 24")

    users_parser = sub.add_parser("users", help="网页登录账号：list / set-password / create")
    users_sub = users_parser.add_subparsers(dest="users_command", required=True)
    users_sub.add_parser("list", help="列出账号")
    password_parser = users_sub.add_parser("set-password", help="设置密码；账号不存在时新建（默认 root 角色）")
    password_parser.add_argument("username")
    password_parser.add_argument("--role", choices=["root", "user"], default=None,
                                 help="新建时的角色；不写时 root 这个名字建成 root，其他建成 user")
    create_parser = users_sub.add_parser("create", help="新建普通账号（交互输入密码）")
    create_parser.add_argument("username")
    create_parser.add_argument("--dashboard", action="append", default=[], help="可见看板编号，可写多次；写 * 表示全部")
    create_parser.add_argument("--page", action="append", default=[], help="可见页面：data（数据管理）")
    create_parser.add_argument("--can-annotate", action="store_true")

    args = parser.parse_args()
    if args.command == "users":
        _users_command(args)
        return
    if args.command == "cleanup-figures":
        from .services.figure_cleanup import cleanup_figures

        report = cleanup_figures(apply=args.apply, min_age_hours=args.min_age_hours)
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        if not args.apply and report.orphans:
            print("这是预览；确认后加 --apply 把这些文件移到回收目录。")
        return
    if args.command == "plot":
        from .core.schemas import PlotRequest
        from .services.plot_service import PlotService

        payload = json.loads(absolute_path(args.config).read_text(encoding="utf-8"))
        request = PlotRequest(**payload)
        service = PlotService()
        result = service.dry_run(request) if args.dry_run else service.create_plot(request)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return


def _ask_password() -> str:
    import getpass

    first = getpass.getpass("新密码（至少 6 位，输入时不显示）：")
    second = getpass.getpass("再输一遍：")
    if first != second:
        raise SystemExit("两次输入不一致，没有修改。")
    return first


def _users_command(args) -> None:
    from .storage.user_store import UserError, store

    try:
        if args.users_command == "list":
            for user in store.list_users():
                dashboards = "全部" if user["dashboards"] == "*" else (", ".join(user["dashboards"]) or "无")
                suffix = "\t(已停用)" if user["disabled"] else ""
                annotate = "是" if user["can_annotate"] else "否"
                pages = ",".join(user["pages"]) or "无"
                print(f"{user['username']}\t{user['role']}\t可批注={annotate}\t页面={pages}\t看板={dashboards}{suffix}")
            if not store.list_users():
                print("还没有账号。")
            return
        if args.users_command == "set-password":
            password = _ask_password()
            try:
                store.get_user(args.username)
            except KeyError:
                role = args.role or ("root" if args.username == "root" else "user")
                store.create_user(args.username, password, role)
                print(f"已新建账号 {args.username}（{role}）。")
                return
            store.set_password(args.username, password)
            print(f"已修改 {args.username} 的密码，原来的登录已失效。")
            return
        if args.users_command == "create":
            password = _ask_password()
            dashboards = "*" if "*" in args.dashboard else args.dashboard
            store.create_user(args.username, password, "user", can_annotate=args.can_annotate,
                              pages=args.page, dashboards=dashboards)
            print(f"已新建账号 {args.username}。")
    except UserError as exc:
        raise SystemExit(f"没有修改：{exc}") from exc


if __name__ == "__main__":
    main()
