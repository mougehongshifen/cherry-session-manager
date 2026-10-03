# -*- coding: utf-8 -*-
"""
把 Cherry 会话管理器打包成 Windows 可执行文件。

用法：
    python build_exe.py                      # 打包，界面找系统里已有的 bun
    python build_exe.py --bun "C:\\path\\bun.exe"
                                             # 连 bun.exe 一起打包（推荐，用户就不用自己装 Bun）
    python build_exe.py --onefile            # 打成单个 exe（启动慢、体积大，不推荐）

产物在 dist/CherrySessionManager/ 里，整个文件夹压缩后即可发布。

说明：
  · 默认用 onedir 模式。onefile 每次启动都要把上百 MB 解压到临时目录，很慢，
    而且内嵌的 bun.exe 也会被反复解压。
  · 打包后程序会优先使用「跟 exe 放在一起的」session-tools/ 和 bun.exe，
    所以把 exe、session-tools/、bun.exe 放在同一个文件夹里就是绿色便携版。
"""
import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "CherrySessionManager"
ENTRY = os.path.join(HERE, "main_window.py")

QUICKSTART = """Cherry 会话管理器 {version}
============================

双击 CherrySessionManager.exe 就能用。不用装 Python，也不用装 Bun。

  · 右键一个会话 →「复制此会话」/「删除此会话」
  · 右键一个 Agent（或列表空白处）→「清理副本」「清理旧备份」「清理删除留底」

整个文件夹可以随便放（桌面、U 盘都行），但别只把 exe 拖出来 ——
旁边的 _internal 是它的一部分，缺了就起不来。

非官方作品，与 Cherry Studio 官方无关。它直接读写 Cherry Studio 的本地数据库，
第一次用之前建议先备份。删除是永久的（删之前会留一份很小的「留底」，可以还原）；
删完看不到变化就重启一下 Cherry Studio。

项目主页：https://github.com/mougehongshifen/cherry-session-manager

Cherry Session Manager {version} — unofficial. Run CherrySessionManager.exe.
Keep this folder together: the _internal folder next to it is required.
"""


def write_quickstart(out_dir, version):
    """在产物目录里放一份中文"先看这里"，免得下载的人对着 exe 发愣。"""
    p = os.path.join(out_dir, "先看这里.txt")
    try:
        # 带 BOM：老版记事本打开中文才不会乱码
        with open(p, "w", encoding="utf-8-sig") as f:
            f.write(QUICKSTART.format(version=version))
        print(f"  已写入 {p}")
    except OSError as e:
        print(f"  ⚠ 写不了 {p}：{e}")



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bun", help="bun.exe 的路径；给了就一起打包进产物")
    ap.add_argument("--onefile", action="store_true", help="打成单个 exe（不推荐）")
    ap.add_argument("--clean", action="store_true", help="先删掉 build/ 与 dist/")
    args = ap.parse_args()

    if not os.path.exists(ENTRY):
        sys.exit(f"找不到入口文件：{ENTRY}")

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit(
            "没有安装 PyInstaller。请先运行：\n"
            "    pip install pyinstaller\n"
            "或者：\n"
            f'    "{sys.executable}" -m pip install pyinstaller'
        )

    if args.clean:
        for d in ("build", "dist"):
            p = os.path.join(HERE, d)
            if os.path.isdir(p):
                shutil.rmtree(p)
                print(f"已删除 {d}/")

    sep = ";" if os.name == "nt" else ":"
    add_data = [
        f"{os.path.join(HERE, 'session-tools')}{sep}session-tools",
        f"{os.path.join(HERE, 'Background.png')}{sep}.",
        f"{os.path.join(HERE, 'icon.ico')}{sep}.",
    ]
    for p in add_data:
        src = p.split(sep)[0]
        if not os.path.exists(src):
            print(f"⚠ 警告：{src} 不存在，跳过")

    icon_path = os.path.join(HERE, "icon.ico")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--windowed",                       # 不弹控制台黑窗
        "--name", APP_NAME,
        "--onedir" if not args.onefile else "--onefile",
    ]
    if os.path.exists(icon_path):
        # exe 文件本身的图标（资源管理器里显示的就是它）
        cmd += ["--icon", icon_path]
    else:
        print("⚠ 没找到 icon.ico，exe 会用 PyInstaller 的默认图标"
              "（可以运行 python make_icon.py 生成）")
    for d in add_data:
        if os.path.exists(d.split(sep)[0]):
            cmd += ["--add-data", d]

    if args.bun:
        if not os.path.exists(args.bun):
            sys.exit(f"找不到 bun：{args.bun}")
        # bun 放到产物根目录，程序启动时会自动找到
        cmd += ["--add-binary", f"{args.bun}{sep}."]
        print(f"会把 bun 一起打包：{args.bun}  ({os.path.getsize(args.bun)/1048576:.0f} MB)")

    cmd.append(ENTRY)

    print("执行：")
    print("  " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    print()
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode != 0:
        sys.exit(f"打包失败，退出码 {r.returncode}")

    out_dir = os.path.join(HERE, "dist", APP_NAME)
    try:
        sys.path.insert(0, HERE)
        from main_window import APP_VERSION
    except Exception:
        APP_VERSION = "v?"
    write_quickstart(out_dir, APP_VERSION)
    print()
    print("=" * 60)
    print("打包完成")
    print(f"  产物：{out_dir}")
    if args.bun:
        print("  说明：bun 已内嵌，用户不需要自己安装 Bun")
    else:
        print("  说明：没有内嵌 bun，用户需要自己安装 Bun（https://bun.sh）")
        print("        或者在运行程序后，于「设置」里指定 bun.exe 的路径")
    print("  发布：把整个文件夹压缩成 zip 上传即可")
    print("        （里面会自动放一份「先看这里.txt」，给下载的人看）")


if __name__ == "__main__":
    main()
