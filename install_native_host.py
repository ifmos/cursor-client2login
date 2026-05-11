#!/usr/bin/env python3
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


# 支持的浏览器及其原生消息主机配置
# 添加新浏览器只需在此扩展即可（如 Brave、Vivaldi）
BROWSERS = {
    "chrome": {
        "name": "Google Chrome",
        "macos_parent": "~/Library/Application Support/Google/Chrome",
        "linux_parent": "~/.config/google-chrome",
        "windows_appdata_subpath": ("Google", "Chrome"),
        "windows_registry": r"SOFTWARE\Google\Chrome\NativeMessagingHosts",
    },
    "edge": {
        "name": "Microsoft Edge",
        "macos_parent": "~/Library/Application Support/Microsoft Edge",
        "linux_parent": "~/.config/microsoft-edge",
        "windows_appdata_subpath": ("Microsoft", "Edge"),
        "windows_registry": r"SOFTWARE\Microsoft\Edge\NativeMessagingHosts",
    },
}

HOST_NAME = "com.cursor.client.manage"
MANIFEST_FILENAME = f"{HOST_NAME}.json"


def get_system_key():
    """获取规范化的系统标识"""
    system = platform.system()
    if system == "Darwin":
        return "macos"
    if system == "Windows":
        return "windows"
    if system == "Linux":
        return "linux"
    raise RuntimeError(f"不支持的操作系统: {system}")


def get_browser_parent_dir(browser_key):
    """获取浏览器用户数据目录（用于检测是否安装）"""
    config = BROWSERS[browser_key]
    system = get_system_key()
    if system == "macos":
        return os.path.expanduser(config["macos_parent"])
    if system == "linux":
        return os.path.expanduser(config["linux_parent"])
    if system == "windows":
        appdata = os.getenv("APPDATA")
        if not appdata:
            return None
        return os.path.join(appdata, *config["windows_appdata_subpath"])
    return None


def get_native_host_dir(browser_key):
    """获取浏览器原生消息主机目录"""
    parent = get_browser_parent_dir(browser_key)
    if parent is None:
        return None
    return os.path.join(parent, "NativeMessagingHosts")


def detect_installed_browsers():
    """返回本机已安装的浏览器列表"""
    installed = []
    for key in BROWSERS:
        parent = get_browser_parent_dir(key)
        if parent and os.path.exists(parent):
            installed.append(key)
    return installed


def resolve_target_browsers(requested):
    """解析 --browser 参数为实际安装目标列表"""
    if requested == "all":
        targets = detect_installed_browsers()
        if not targets:
            raise RuntimeError(
                "未检测到任何受支持的浏览器（Chrome / Edge），请先安装其一"
            )
        return targets
    if requested in BROWSERS:
        return [requested]
    raise RuntimeError(f"未知的浏览器: {requested}")


def write_native_host_script(source_path, target_path):
    """将 native_host.py 复制到目标位置，并把 shebang 重写为当前 Python 解释器路径

    这样确保原生主机使用安装时的 Python（带有 requests 依赖），
    而源仓库内的 shebang 始终保持可移植的 #!/usr/bin/env python3。
    """
    with open(source_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if lines and lines[0].startswith("#!"):
        lines[0] = f"#!{sys.executable}\n"
    else:
        lines.insert(0, f"#!{sys.executable}\n")

    with open(target_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def build_allowed_origins(extension_id, allow_all):
    """构造 manifest 的 allowed_origins 列表"""
    if extension_id:
        return [f"chrome-extension://{extension_id}/"]
    if allow_all:
        print(
            "⚠️  使用通配 allowed_origins (chrome-extension://*/)，"
            "任何已安装扩展都能调用此原生主机；"
            "建议安装完成后用 update_native_host.py <extension_id> 收紧"
        )
        return ["chrome-extension://*/"]
    raise RuntimeError("必须提供 --extension-id 或 --allow-all 之一")


def create_native_host_manifest(host_dir, script_path, allowed_origins):
    """创建原生主机清单文件"""
    manifest = {
        "name": HOST_NAME,
        "description": "Cursor Client2Login Native Host",
        "path": str(script_path),
        "type": "stdio",
        "allowed_origins": allowed_origins,
    }
    manifest_path = os.path.join(host_dir, MANIFEST_FILENAME)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return manifest_path


def install_for_browser(browser_key, source_script, allowed_origins):
    """为单个浏览器安装原生主机"""
    config = BROWSERS[browser_key]
    host_dir = get_native_host_dir(browser_key)
    if host_dir is None:
        print(f"⚠️  无法解析 {config['name']} 的安装路径，已跳过")
        return False

    os.makedirs(host_dir, exist_ok=True)
    print(f"📁 [{config['name']}] 主机目录: {host_dir}")

    system = get_system_key()
    target_script = os.path.join(host_dir, "native_host.py")
    write_native_host_script(source_script, target_script)

    if system in ("macos", "linux"):
        os.chmod(target_script, 0o755)
        script_path_for_manifest = target_script
    else:
        # Windows 上 native messaging 期望 path 指向单一可执行文件；
        # 用 .bat 包装一层调用 python，避免 manifest 中拼接命令行
        bat_path = os.path.join(host_dir, "native_host.bat")
        with open(bat_path, "w", encoding="utf-8") as f:
            f.write(f'@echo off\r\n"{sys.executable}" "{target_script}" %*\r\n')
        script_path_for_manifest = bat_path

    manifest_path = create_native_host_manifest(
        host_dir, script_path_for_manifest, allowed_origins
    )
    print(f"📄 [{config['name']}] 清单: {manifest_path}")

    if system == "windows":
        install_windows_registry(browser_key, manifest_path)

    return True


def install_windows_registry(browser_key, manifest_path):
    """在 Windows 注册表中登记原生主机（按浏览器选择不同分支）"""
    try:
        import winreg
    except ImportError:
        print(f"⚠️  无法导入 winreg，请手动在以下位置添加注册表项:")
        print(f"   HKEY_CURRENT_USER\\{BROWSERS[browser_key]['windows_registry']}\\{HOST_NAME}")
        print(f"   值: {manifest_path}")
        return

    key_path = f"{BROWSERS[browser_key]['windows_registry']}\\{HOST_NAME}"
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, manifest_path)
        print(f"📝 [{BROWSERS[browser_key]['name']}] 已写入注册表: HKCU\\{key_path}")
    except Exception as e:
        print(f"⚠️  写注册表失败 ({browser_key}): {e}")


def install_native_host(targets, extension_id, allow_all):
    """安装原生消息主机到指定浏览器列表"""
    print("🔧 开始安装 Cursor Client2Login 原生主机...")
    current_dir = Path(__file__).parent.absolute()
    source_script = current_dir / "native_host.py"
    if not source_script.exists():
        print(f"❌ 找不到 native_host.py: {source_script}")
        return False

    try:
        allowed_origins = build_allowed_origins(extension_id, allow_all)
    except RuntimeError as e:
        print(f"❌ {e}")
        return False

    print(f"🎯 安装目标: {', '.join(BROWSERS[k]['name'] for k in targets)}")
    print(f"🔐 allowed_origins: {allowed_origins}")

    success = False
    for browser_key in targets:
        if install_for_browser(browser_key, source_script, allowed_origins):
            success = True

    if success:
        print("\n✅ 安装完成")
        print("📝 接下来:")
        print("  1. 重启浏览器")
        print("  2. 在扩展弹窗中点击「自动读取 Cursor 数据」")
    else:
        print("\n❌ 所有目标浏览器安装均失败")
    return success


def uninstall_native_host(targets):
    """卸载指定浏览器列表中的原生主机"""
    print("🗑️  开始卸载...")
    system = get_system_key()
    any_removed = False

    for browser_key in targets:
        host_dir = get_native_host_dir(browser_key)
        if host_dir is None or not os.path.exists(host_dir):
            continue
        for filename in ("native_host.py", "native_host.bat", MANIFEST_FILENAME):
            file_path = os.path.join(host_dir, filename)
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"🗑️  已删除: {file_path}")
                any_removed = True

        if system == "windows":
            try:
                import winreg
                key_path = f"{BROWSERS[browser_key]['windows_registry']}\\{HOST_NAME}"
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
                print(f"🗑️  已删除注册表项: HKCU\\{key_path}")
                any_removed = True
            except FileNotFoundError:
                pass
            except Exception as e:
                print(f"⚠️  删除注册表失败 ({browser_key}): {e}")

    print("✅ 卸载完成" if any_removed else "ℹ️  没有发现已安装的副本")
    return True


def test_native_host(targets):
    """对源脚本和已安装副本各执行一次测试"""
    print("🧪 测试原生主机...")
    current_dir = Path(__file__).parent.absolute()
    source_script = current_dir / "native_host.py"
    if not source_script.exists():
        print(f"❌ 找不到 native_host.py: {source_script}")
        return False

    print("📍 测试源脚本...")
    result = subprocess.run(
        [sys.executable, str(source_script), "test"],
        capture_output=True, timeout=30, text=True,
    )
    if result.returncode != 0:
        print(f"❌ 源脚本测试失败 (rc={result.returncode})")
        if result.stderr:
            print(result.stderr)
        return False
    print("✅ 源脚本测试通过")
    print(result.stdout)

    for browser_key in targets:
        host_dir = get_native_host_dir(browser_key)
        if host_dir is None:
            continue
        installed = os.path.join(host_dir, "native_host.py")
        if not os.path.exists(installed):
            print(f"⚠️  [{BROWSERS[browser_key]['name']}] 未安装，跳过")
            continue

        print(f"📍 测试已安装副本 [{BROWSERS[browser_key]['name']}]...")
        result = subprocess.run(
            [sys.executable, installed, "test"],
            capture_output=True, timeout=30, text=True,
        )
        if result.returncode != 0:
            print(f"❌ 已安装副本测试失败 (rc={result.returncode})")
            if result.stderr:
                print(result.stderr)
            return False
        print("✅ 已安装副本测试通过")

    return True


def build_parser():
    parser = argparse.ArgumentParser(description="Cursor Client2Login 原生主机安装工具")
    sub = parser.add_subparsers(dest="action", required=True)

    common_browser = lambda p: p.add_argument(
        "--browser",
        choices=list(BROWSERS.keys()) + ["all"],
        default="all",
        help="目标浏览器（默认安装到所有已检测到的浏览器）",
    )

    p_install = sub.add_parser("install", help="安装原生主机")
    common_browser(p_install)
    group = p_install.add_mutually_exclusive_group()
    group.add_argument("--extension-id", help="限制 allowed_origins 到指定扩展 ID（推荐）")
    group.add_argument(
        "--allow-all",
        action="store_true",
        help="允许任何扩展调用（仅在调试时使用）",
    )

    p_uninstall = sub.add_parser("uninstall", help="卸载原生主机")
    common_browser(p_uninstall)

    p_test = sub.add_parser("test", help="测试原生主机")
    common_browser(p_test)

    sub.add_parser("detect", help="列出本机已安装的受支持浏览器")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.action == "detect":
        installed = detect_installed_browsers()
        if not installed:
            print("ℹ️  未检测到 Chrome / Edge")
        else:
            for key in installed:
                print(f"✓ {BROWSERS[key]['name']} → {get_native_host_dir(key)}")
        return

    try:
        targets = resolve_target_browsers(args.browser)
    except RuntimeError as e:
        print(f"❌ {e}")
        sys.exit(1)

    if args.action == "install":
        if not args.extension_id and not args.allow_all:
            print("⚠️  未提供 --extension-id，将默认使用通配 allowed_origins")
            print("    强烈建议先在 chrome://extensions/ 或 edge://extensions/ 取得扩展 ID")
            args.allow_all = True
        ok = install_native_host(targets, args.extension_id, args.allow_all)
        sys.exit(0 if ok else 1)
    elif args.action == "uninstall":
        uninstall_native_host(targets)
    elif args.action == "test":
        ok = test_native_host(targets)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
