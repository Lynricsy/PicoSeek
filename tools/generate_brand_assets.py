"""从交付的品牌插画生成各客户端品牌资源。依赖 Pillow、NumPy、SciPy 与 potrace。

链路：`docs/assets/logo-source.png`（白底交付稿）
  → `docs/assets/logo.png`（抠成透明底的方形母版）
  → 各端 PNG / ICO；`docs/assets/logo-mono.svg` 由母版描摹，供 Android 主题图标使用。
"""

import io
import re
import shutil
import struct
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import cast

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)
# potrace 的路径把指令与数字紧挨着输出，需要按词法切开。
PATH_TOKEN = re.compile(r"[A-Za-z]|-?\d+(?:\.\d+)?")

# 母版尺寸取 iOS 应用图标所需的 1024，其余尺寸一律由它降采样得到。
MASTER = 1024
# 母版四周留白比例：标识本身已是完整构图，留白只为避免贴边。
MASTER_MARGIN = 0.03
# 应用图标里标识占画布的比例；自适应图标要落在 66% 安全区内，比例更小。
ICON_RATIO = 0.78
ADAPTIVE_RATIO = 0.48
# 桌面图标的圆角底板：内缩与圆角半径均相对画布边长。
PLATE_INSET = 0.031
PLATE_RADIUS = 0.203

LIGHT_BACKGROUND = "#FFFFFF"
DARK_BACKGROUND = "#0D1B2E"
TINTED_BACKGROUND = "#202020"

# 抠图阈值：最小通道 ≥ WHITE_FLOOR 视为纯白背景；≥ FADE_FLOOR 视为向白淡出的高光，
# 按白度解算 alpha。实色浅蓝构件最小通道在 165 以下，不会被误判。
WHITE_FLOOR = 246.0
FADE_FLOOR = 200.0
SMOOTH = 1.6
# 单色版阈值：白底上的亮度低于它才算墨色，书页留白因此成为镂空。
MONO_INK = 210.0


def flood_from_border(mask: np.ndarray) -> np.ndarray:
    """掩码中与画布边缘连通的区域——即真正的背景，画面内部的同色区域不受影响。"""
    labels = cast("np.ndarray", ndimage.label(mask)[0])
    edges = np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
    return np.isin(labels, list(set(np.unique(edges)) - {0}))


def transparent_art(source: Image.Image) -> Image.Image:
    """把白底交付稿解算成透明底：外部白色抠除，淡出高光转为 alpha 渐变。"""
    rgb = np.asarray(source.convert("RGB")).astype(np.float32)
    mn = rgb.min(axis=2)
    background = flood_from_border(mn >= WHITE_FLOOR)
    fade = flood_from_border(mn >= FADE_FLOOR)
    # 紧贴背景的 2px 反锯齿带按邻域实色反解覆盖率，避免深色底上出现白边。
    band = (
        ndimage.binary_dilation(
            background, ndimage.generate_binary_structure(2, 2), 2
        )
        & ~background
    )
    core = ndimage.minimum_filter(mn, size=7)

    alpha = np.ones_like(mn)
    alpha[fade] = np.clip((255.0 - mn[fade]) / (255.0 - FADE_FLOOR), 0.0, 1.0)
    edge = np.clip((255.0 - mn) / np.maximum(255.0 - core, 8.0), 0.0, 1.0)
    alpha[band] = np.minimum(alpha[band], edge[band])
    alpha[background] = 0.0
    # 近白区域的 JPEG 噪点会让 alpha 抖动成斑块，只在淡出区做一次平滑。
    soft = fade & ~band & ~background
    alpha = np.where(soft, ndimage.gaussian_filter(alpha, SMOOTH), alpha)

    # 半透明像素直接反解颜色会放大噪点，改用最近的不透明像素取色。
    solid = alpha >= 0.9
    nearest = cast(
        "np.ndarray",
        ndimage.distance_transform_edt(
            ~solid, return_distances=False, return_indices=True
        ),
    )
    color = rgb[nearest[0], nearest[1]]
    color[solid] = rgb[solid]
    color = np.where(
        solid[..., None], color, ndimage.gaussian_filter(color, (SMOOTH, SMOOTH, 0))
    )

    art = Image.fromarray(
        np.dstack([np.clip(color, 0, 255), np.round(alpha * 255)]).astype(np.uint8),
        "RGBA",
    )
    return art.crop(art.getchannel("A").getbbox())


def placed(art: Image.Image, ratio: float, size: int = MASTER) -> Image.Image:
    """把标识按比例居中放到透明方形画布上。"""
    scale = size * ratio / max(art.size)
    scaled = art.resize(
        (max(1, round(art.width * scale)), max(1, round(art.height * scale))),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(scaled, ((size - scaled.width) // 2, (size - scaled.height) // 2))
    return canvas


def icon(logo: Image.Image, background: str, rounded: bool = False) -> Image.Image:
    """应用图标：标识叠在品牌底板上；圆角底板用于 macOS / Windows / Linux。"""
    plate = Image.new("RGBA", logo.size, background)
    if rounded:
        mask = Image.new("L", logo.size, 0)
        inset = round(logo.width * PLATE_INSET)
        ImageDraw.Draw(mask).rounded_rectangle(
            (inset, inset, logo.width - 1 - inset, logo.height - 1 - inset),
            radius=round(logo.width * PLATE_RADIUS),
            fill=255,
        )
        plate.putalpha(mask)
    return Image.alpha_composite(plate, logo)


def grayscale(image: Image.Image) -> Image.Image:
    """iOS 着色图标只取明度，系统再按用户选色重新上色。"""
    gray = image.convert("L").convert("RGB")
    gray.putalpha(image.getchannel("A"))
    return gray


def encode(image: Image.Image, size: int, opaque: bool = False) -> bytes:
    resized = image.resize((size, size), Image.Resampling.LANCZOS, reducing_gap=3.0)
    if opaque:
        resized = resized.convert("RGB")
    buffer = io.BytesIO()
    resized.save(buffer, "PNG", optimize=True)
    data = buffer.getvalue()
    # PNG 的 IHDR 色彩类型 2 为无 alpha 的 RGB，满足 iOS 图标要求。
    if opaque and data[25] != 2:
        raise ValueError("应用图标不是不透明 RGB")
    return data


def save_bytes(relative: str, content: bytes) -> None:
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(content)


def save_png(relative: str, image: Image.Image, size: int, opaque: bool = False) -> None:
    save_bytes(relative, encode(image, size, opaque))


def save_ico(relative: str, image: Image.Image, sizes: list[int]) -> None:
    # ICO 目录指向各尺寸的 PNG，现代 Windows 与浏览器均原生支持。
    images = [encode(image, size) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = len(header) + 16 * len(sizes)
    entries = []
    for size, data in zip(sizes, images, strict=True):
        entries.append(
            struct.pack(
                "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset
            )
        )
        offset += len(data)
    save_bytes(relative, header + b"".join(entries) + b"".join(images))


def mono_bitmap(art: Image.Image) -> Image.Image:
    """单色版掩码：墨色取自彩色图形，书页留白成为镂空，细小构件整体填实。"""
    sample = art.resize((art.width * 3, art.height * 3), Image.Resampling.LANCZOS)
    pixels = np.asarray(sample).astype(np.float32)
    alpha = pixels[..., 3] / 255.0
    luma = pixels[..., :3] @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    plate = alpha * luma + (1 - alpha) * 255.0

    ink = (alpha >= 0.5) & (plate < MONO_INK)
    ink = ndimage.binary_opening(
        ndimage.binary_closing(ink, np.ones((5, 5))), np.ones((5, 5))
    )
    # 速度方块与短线本身是浅蓝渐变，按阈值切会留毛边，整块填实更干净。
    silhouette = alpha >= 0.5
    labels, count = cast("tuple[np.ndarray, int]", ndimage.label(silhouette))
    limit = silhouette.sum() * 0.01
    for index in range(1, count + 1):
        part = labels == index
        if part.sum() < limit:
            ink |= part
    return Image.fromarray(np.where(ink, 0, 255).astype(np.uint8)).convert("1")


def trace_mono(art: Image.Image) -> bytes:
    """potrace 描摹单色掩码，并把坐标归一到与母版一致的 256 画布。"""
    bitmap = mono_bitmap(art)
    buffer = io.BytesIO()
    bitmap.save(buffer, "PPM")
    traced = subprocess.run(
        ["potrace", "-s", "-o", "-", "--turdsize", "12", "--alphamax", "1.0", "-"],
        input=buffer.getvalue(),
        capture_output=True,
        check=True,
    ).stdout.decode()

    root = ET.fromstring(traced)
    group = root.find(f"{{{NS}}}g")
    if group is None:
        raise ValueError("potrace 输出缺少路径分组")
    numbers = [
        float(value)
        for value in group.attrib["transform"]
        .replace("translate(", " ")
        .replace("scale(", " ")
        .replace(")", " ")
        .replace(",", " ")
        .split()
    ]
    shift_x, shift_y, scale_x, scale_y = numbers
    # potrace 的坐标系以位图像素为单位；再映射到 256 画布上母版所占的区域。
    fit = 256 * (1 - 2 * MASTER_MARGIN) / max(bitmap.size)
    offset_x = (256 - bitmap.width * fit) / 2
    offset_y = (256 - bitmap.height * fit) / 2

    def point(x: float, y: float) -> tuple[float, float]:
        return (
            (shift_x + scale_x * x) * fit + offset_x,
            (shift_y + scale_y * y) * fit + offset_y,
        )

    commands: list[str] = []
    for path in group.findall(f"{{{NS}}}path"):
        commands.append(transform_path(path.attrib["d"], point, scale_x * fit, scale_y * fit))

    svg = ET.Element(
        f"{{{NS}}}svg",
        {
            "width": "256",
            "height": "256",
            "viewBox": "0 0 256 256",
            "role": "img",
            "aria-labelledby": "logo-title logo-desc",
        },
    )
    title = ET.SubElement(svg, f"{{{NS}}}title", {"id": "logo-title"})
    title.text = "PicoSeek 单色标志"
    desc = ET.SubElement(svg, f"{{{NS}}}desc", {"id": "logo-desc"})
    desc.text = "字母 P 与放大镜内的展开书页，单色描摹版，内联时可用 CSS color 换色。"
    ET.SubElement(
        svg,
        f"{{{NS}}}path",
        {"fill": "currentColor", "fill-rule": "evenodd", "d": " ".join(commands)},
    )
    return ET.tostring(svg, encoding="utf-8", xml_declaration=True)


def transform_path(
    data: str,
    point: Callable[[float, float], tuple[float, float]],
    delta_x: float,
    delta_y: float,
) -> str:
    """把 potrace 的路径（M/m + 相对 c/l + z）换算到目标坐标系，起点一律输出绝对值。"""
    tokens = PATH_TOKEN.findall(data)
    output: list[str] = []
    index = 0
    command = ""
    x = y = start_x = start_y = 0.0
    while index < len(tokens):
        if tokens[index][-1].isalpha():
            command = tokens[index]
            index += 1
            if command == "z":
                output.append("z")
                x, y = start_x, start_y
                continue
        if command in {"M", "m"}:
            if command == "M":
                x, y = float(tokens[index]), float(tokens[index + 1])
            else:
                x, y = x + float(tokens[index]), y + float(tokens[index + 1])
            start_x, start_y = x, y
            output.append("M{:.2f} {:.2f}".format(*point(x, y)))
            index += 2
        elif command in {"c", "l"}:
            count = 6 if command == "c" else 2
            values = [float(value) for value in tokens[index : index + count]]
            pairs = [
                f"{values[step] * delta_x:.2f} {values[step + 1] * delta_y:.2f}"
                for step in range(0, count, 2)
            ]
            output.append(command + " ".join(pairs))
            x, y = x + values[count - 2], y + values[count - 1]
            index += count
        else:
            raise ValueError(f"potrace 路径出现未支持的指令：{command}")
    return "".join(output)


def android_monochrome(mono: bytes) -> bytes:
    """Android 主题图标：把单色矢量搬进 VectorDrawable，并按安全区缩放。"""
    android = "http://schemas.android.com/apk/res/android"
    ET.register_namespace("android", android)
    root = ET.Element(
        "vector",
        {
            f"{{{android}}}width": "108dp",
            f"{{{android}}}height": "108dp",
            f"{{{android}}}viewportWidth": "384",
            f"{{{android}}}viewportHeight": "384",
        },
    )
    # 单色矢量的 256 画布对应自适应前景里标识应占的宽度，居中落在 384 视口中。
    span = 384 * ADAPTIVE_RATIO / (1 - 2 * MASTER_MARGIN)
    scale = span / 256
    group = ET.SubElement(
        root,
        "group",
        {
            f"{{{android}}}scaleX": f"{scale:.4f}",
            f"{{{android}}}scaleY": f"{scale:.4f}",
            f"{{{android}}}translateX": f"{(384 - span) / 2:.2f}",
            f"{{{android}}}translateY": f"{(384 - span) / 2:.2f}",
        },
    )
    for source in ET.fromstring(mono).findall(f"{{{NS}}}path"):
        path = ET.SubElement(
            group,
            "path",
            {
                f"{{{android}}}pathData": source.attrib["d"],
                f"{{{android}}}fillColor": "#FFFFFFFF",
            },
        )
        if source.get("fill-rule") == "evenodd":
            path.set(f"{{{android}}}fillType", "evenOdd")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def main() -> None:
    if not shutil.which("potrace"):
        raise SystemExit("缺少 potrace，请先安装。")
    art = transparent_art(Image.open(ROOT / "docs/assets/logo-source.png"))
    logo = placed(art, 1 - 2 * MASTER_MARGIN)
    mono = trace_mono(art)
    save_bytes("docs/assets/logo.png", encode(logo, MASTER))
    save_bytes("docs/assets/logo-mono.svg", mono)

    inner = placed(art, ICON_RATIO)
    light_icon = icon(inner, LIGHT_BACKGROUND)
    dark_icon = icon(inner, DARK_BACKGROUND)
    tinted_icon = icon(grayscale(inner), TINTED_BACKGROUND)
    desktop_icon = icon(inner, LIGHT_BACKGROUND, rounded=True)
    adaptive = placed(art, ADAPTIVE_RATIO)

    # PDF 导出页眉内联这张图，尺寸够印刷即可。
    save_png("backend/app/export/logo.png", logo, 192)

    web = "frontend/public/brand"
    save_png(f"{web}/logo.png", logo, 256)
    save_ico(f"{web}/favicon.ico", logo, [16, 32, 48])
    for name, size in [("apple-touch-icon", 180), ("icon-192", 192), ("icon-512", 512)]:
        save_png(f"{web}/{name}.png", light_icon, size, opaque=True)

    apple = "apple/PicoSeek/Resources/Assets.xcassets"
    for suffix, size in [("", 256), ("@2x", 512), ("@3x", 768)]:
        save_png(f"{apple}/BrandLogo.imageset/logo{suffix}.png", logo, size)
    for name, image in [
        ("light", light_icon),
        ("dark", dark_icon),
        ("tinted", tinted_icon),
    ]:
        save_png(f"{apple}/AppIcon.appiconset/ios-{name}.png", image, 1024, opaque=True)
    for size in [16, 32, 64, 128, 256, 512, 1024]:
        save_png(f"{apple}/AppIcon.appiconset/mac-{size}.png", desktop_icon, size)

    flutter = "flutter/assets/brand"
    for directory, size in [("", 256), ("2.0x/", 512), ("3.0x/", 768)]:
        save_png(f"{flutter}/{directory}logo.png", logo, size)
    save_png(f"{flutter}/app-icon.png", desktop_icon, 512)
    save_ico(
        "flutter/windows/runner/resources/app_icon.ico",
        desktop_icon,
        [16, 24, 32, 48, 64, 128, 256],
    )

    android = "flutter/android/app/src/main/res"
    save_bytes(
        f"{android}/drawable/ic_launcher_monochrome.xml", android_monochrome(mono)
    )
    for density, launcher_size, adaptive_size in [
        ("mdpi", 48, 108),
        ("hdpi", 72, 162),
        ("xhdpi", 96, 216),
        ("xxhdpi", 144, 324),
        ("xxxhdpi", 192, 432),
    ]:
        save_png(
            f"{android}/mipmap-{density}/ic_launcher.png",
            light_icon,
            launcher_size,
            opaque=True,
        )
        for name in ["ic_launcher_foreground", "brand_splash"]:
            save_png(f"{android}/drawable-{density}/{name}.png", adaptive, adaptive_size)
    print("已从 docs/assets/logo-source.png 生成 Web、Apple、Flutter/Android/Windows 品牌资源。")


if __name__ == "__main__":
    main()
