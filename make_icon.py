# -*- coding: utf-8 -*-
"""
生成应用图标：icon.ico（多尺寸）+ icon.png（256，给 README / GitHub 用）。

为什么用脚本生成而不是塞一个来路不明的二进制进仓库：
  · 谁都能看清它是什么、也能自己改一行重新生成；
  · 图标里不含任何第三方素材（不碰 Cherry Studio 的 logo —— 本项目是非官方的，
    用它的商标既侵权又容易让人误以为有关联）。

Windows 图标的要求（这里都满足了）：
  · **.ico** 格式（PyInstaller 的 --icon、资源管理器、任务栏都认它）
  · 一个 .ico 里装**多个尺寸**：16/24/32/48/64/128/256（系统按场合自己挑，
    没有 256 的话高 DPI 下会糊，没有 16 的任务栏小图标会糊）
  · **32 位带 alpha**（透明背景，圆角才不会留白边）
  · 图形要**在 16×16 下还认得出来**：所以只用大色块，不画细线、不写字
  · 四周留出约 10% 的安全边距（Windows 11 会自己裁圆角/加投影，别自己画）

用法：
    python make_icon.py            # 生成 icon.ico / icon.png 到本目录
"""
import io
import os
import struct
import sys

SIZES = [16, 24, 32, 48, 64, 128, 256]

# 界面上用的主色（和 main_window.py 里的 CHERRY 一致）
CHERRY_TOP = "#FF7D6B"
CHERRY_BOTTOM = "#F0523F"
SHEET = "#FFFFFF"


def draw(size):
    """按给定边长画一张图标，返回 QImage（32 位带 alpha）。"""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath

    u = size / 32.0                      # 以 32×32 为设计基准，等比缩放
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)

    def rounded(x, y, w, h, r, color):
        path = QPainterPath()
        path.addRoundedRect(QRectF(x * u, y * u, w * u, h * u), r * u, r * u)
        p.fillPath(path, QColor(color))

    # 底：圆角方块 + 竖向渐变
    grad = QLinearGradient(0, 1.5 * u, 0, 30.5 * u)
    grad.setColorAt(0.0, QColor(CHERRY_TOP))
    grad.setColorAt(1.0, QColor(CHERRY_BOTTOM))
    path = QPainterPath()
    path.addRoundedRect(QRectF(1.5 * u, 1.5 * u, 29 * u, 29 * u), 7.5 * u, 7.5 * u)
    p.fillPath(path, grad)

    # 后面那张「纸」（半透明白，露出底色 = 层次感）
    rounded(13.0, 6.5, 12.0, 14.5, 2.2, "#FFFFFFA8")
    # 前面那张「纸」
    rounded(7.0, 11.0, 13.5, 15.0, 2.4, SHEET)
    # 纸上的两行「字」。
    # 16px 下只有 1px 高，两行加中间的缝会糊成一片脏色，所以小尺寸干脆不画 ——
    # 只留「两张纸叠在一起」这个形状，反而更干净、更好认。
    if size > 16:
        rounded(9.5, 14.5, 8.5, 2.0, 1.0, CHERRY_BOTTOM)
        rounded(9.5, 18.5, 8.5, 2.0, 1.0, CHERRY_BOTTOM)

    p.end()
    return img


def png_bytes(img):
    from PySide6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    if not img.save(buf, "PNG"):
        raise RuntimeError("QImage 存 PNG 失败")
    return bytes(buf.data())


def build_ico(images):
    """把一组 PNG 拼成 .ico（Vista+ 支持内嵌 PNG，一个文件装多个尺寸）。"""
    entries, payloads, offset = [], [], 6 + 16 * len(images)
    for size, png in images:
        entries.append(struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,        # 宽（256 记作 0）
            size if size < 256 else 0,        # 高
            0, 0,                             # 调色板数、保留位
            1, 32,                            # 色彩平面、每像素位数
            len(png), offset,
        ))
        payloads.append(png)
        offset += len(png)
    return struct.pack("<HHH", 0, 1, len(images)) + b"".join(entries) + b"".join(payloads)


def main():
    out_dir = os.path.dirname(os.path.abspath(__file__))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # 不弹窗
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([sys.argv[0]])

    images = []
    for s in SIZES:
        img = draw(s)
        images.append((s, png_bytes(img)))
    ico = os.path.join(out_dir, "icon.ico")
    with open(ico, "wb") as f:
        f.write(build_ico(images))
    big = draw(256)
    png = os.path.join(out_dir, "icon.png")
    big.save(png, "PNG")
    print(f"已生成 {ico}  ({os.path.getsize(ico)} 字节，{len(SIZES)} 个尺寸: {SIZES})")
    print(f"已生成 {png}  (256×256)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
