#!/usr/bin/env python3
"""更新已安装的原生主机 manifest 中的 allowed_origins

典型用法:
  python3 update_native_host.py <extension_id>           # 收紧到指定扩展 ID
  python3 update_native_host.py --allow-all              # 恢复通配
  python3 update_native_host.py --browser edge <id>      # 仅更新 Edge
"""
import argparse
import json
import os
import sys

from install_native_host import (
    BROWSERS,
    MANIFEST_FILENAME,
    detect_installed_browsers,
    get_native_host_dir,
)


def update_for_browser(browser_key, allowed_origins):
    host_dir = get_native_host_dir(browser_key)
    if host_dir is None:
        return False
    manifest_path = os.path.join(host_dir, MANIFEST_FILENAME)
    if not os.path.exists(manifest_path):
        print(f"⚠️  [{BROWSERS[browser_key]['name']}] 未找到清单，跳过")
        return False

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    manifest["allowed_origins"] = allowed_origins

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"✅ [{BROWSERS[browser_key]['name']}] 已更新 → {allowed_origins}")
    return True


def main():
    parser = argparse.ArgumentParser(description="更新原生主机 allowed_origins")
    parser.add_argument("extension_id", nargs="?", help="目标扩展 ID")
    parser.add_argument("--allow-all", action="store_true", help="使用通配 allowed_origins")
    parser.add_argument(
        "--browser",
        choices=list(BROWSERS.keys()) + ["all"],
        default="all",
        help="目标浏览器（默认更新所有已安装的）",
    )
    args = parser.parse_args()

    if args.extension_id and args.allow_all:
        parser.error("不能同时指定 extension_id 和 --allow-all")
    if not args.extension_id and not args.allow_all:
        parser.error("请提供 extension_id 或 --allow-all")

    if args.extension_id:
        allowed_origins = [f"chrome-extension://{args.extension_id}/"]
    else:
        allowed_origins = ["chrome-extension://*/"]
        print("⚠️  使用通配 allowed_origins，任何扩展都能调用此原生主机")

    if args.browser == "all":
        targets = detect_installed_browsers()
        if not targets:
            print("❌ 未检测到任何已安装的浏览器")
            sys.exit(1)
    else:
        targets = [args.browser]

    any_updated = False
    for browser_key in targets:
        if update_for_browser(browser_key, allowed_origins):
            any_updated = True

    if not any_updated:
        print("❌ 没有更新到任何 manifest，请先运行 install_native_host.py install")
        sys.exit(1)

    print("\n💡 重启浏览器使更改生效")


if __name__ == "__main__":
    main()
