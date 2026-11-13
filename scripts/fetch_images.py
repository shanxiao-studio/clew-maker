#!/usr/bin/env python3
"""按 images.tsv 下载头像和封面，找脸裁好，放进工作目录的 images/。

用法：
    <python> scripts/fetch_images.py <工作目录>           只补缺的
    <python> scripts/fetch_images.py <工作目录> --force   全部重新下载、重新裁

images.tsv 每行：名字 <tab> 来源 [<tab> x y [边长]]
    名字   people.txt 里的主名；写「封面」（或 cover）表示线团封面
    来源   图片直链（https://…）
           wiki:<站点>/<页面名>   取 MediaWiki 页面的主图，如 wiki:zh.wikipedia.org/林黛玉、
                                  wiki:gameofthrones.fandom.com/Jon_Snow；页面名写「File:…」取那张图
           file:<本地路径>        用户自己给的图片（相对工作目录或绝对路径）
    x y    可选：脸中心的位置（占图宽、图高的比例，0–1），图里有几个人或认不出脸时用
    边长   可选：手动裁的正方形边长（占图高的比例）

原图存在 source/，裁好的头像（512×512）和封面（长边 1600）存在 images/，都是 PNG；
build_clew.py 打包时再转成 HEIC。
"""

import io
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

AVATAR_SIZE = 512
COVER_LONG_EDGE = 1600
COVERS = {"封面", "cover", "Cover"}
COVER_FILE = "_cover.png"
# 不少图床拒绝没有浏览器标识的请求。
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*;q=0.8",
}


def get(url, referer=None):
    headers = dict(HEADERS)
    parts = urllib.parse.urlsplit(url)
    headers["Referer"] = referer or f"{parts.scheme}://{parts.netloc}/"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def wiki_image(spec):
    """wiki:<站点>/<页面名> → 原图地址。"""
    host, _, title = spec.partition("/")
    title = urllib.parse.unquote(title).replace("_", " ")
    if title.lower().startswith("file:"):
        params = {"action": "query", "titles": title, "prop": "imageinfo", "iiprop": "url",
                  "redirects": 1, "format": "json"}
    else:
        params = {"action": "query", "titles": title, "prop": "pageimages", "piprop": "original",
                  "redirects": 1, "format": "json"}
    query = urllib.parse.urlencode(params)
    last_error = None
    for path in ("/w/api.php", "/api.php"):
        try:
            data = json.loads(get(f"https://{host}{path}?{query}"))
        except Exception as error:  # 站点的 API 路径不同，换一个再试。
            last_error = error
            continue
        for page in data.get("query", {}).get("pages", {}).values():
            if "original" in page:
                return page["original"]["source"]
            if page.get("imageinfo"):
                return page["imageinfo"][0]["url"]
        # 维基百科不把非自由版权的剧照当作页面主图，列出页面里的图片，挑一张写成 File:…
        listing = urllib.parse.urlencode({"action": "query", "titles": title, "prop": "images",
                                          "imlimit": 20, "redirects": 1, "format": "json"})
        files = [image["title"] for page in json.loads(get(f"https://{host}{path}?{listing}"))
                 .get("query", {}).get("pages", {}).values() for image in page.get("images", [])
                 if image["title"].lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
        hint = f"页面里的图片：{'；'.join(files[:10])}" if files else "页面里没有图片"
        raise LookupError(f"页面「{title}」没有主图，写成 wiki:{host}/File:… 指定一张。{hint}")
    raise LookupError(f"读不到 {host} 的 MediaWiki API：{last_error}")


def load(source, work):
    if source.startswith("file:"):
        path = Path(source[5:]).expanduser()
        if not path.is_absolute():
            path = work / path
        return path.read_bytes()
    if source.startswith("wiki:"):
        return get(wiki_image(source[5:]))
    if source.startswith(("http://", "https://")):
        return get(source)
    raise ValueError("来源要以 https://、wiki: 或 file: 开头")


def faces(image):
    """返回 [(cx, cy, w)]，按脸从大到小。没装 OpenCV 时返回空。"""
    try:
        import cv2
        import numpy
    except ImportError:
        return []
    if not hasattr(cv2, "CascadeClassifier"):
        return []
    gray = cv2.cvtColor(numpy.array(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    found = []
    for name in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + name)
        minimum = max(24, int(min(gray.shape) * 0.06))
        for (x, y, w, h) in cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(minimum, minimum)):
            found.append((x + w / 2, y + h / 2, w))
        if found:
            break
    return sorted(found, key=lambda face: -face[2])


def square(image, cx, cy, side):
    """以 (cx, cy) 为中心取边长 side 的正方形，超出图片时往里挪、必要时缩小。"""
    width, height = image.size
    side = min(side, width, height)
    left = min(max(cx - side / 2, 0), width - side)
    top = min(max(cy - side / 2, 0), height - side)
    return image.crop((int(left), int(top), int(left + side), int(top + side)))


def crop_avatar(image, hint):
    width, height = image.size
    if hint and len(hint) == 3:
        x, y, side = hint
        return square(image, x * width, y * height, side * height), "手动"
    if hint:
        x, y = hint
        found = [face for face in faces(image) if abs(face[0] - x * width) < face[2] and abs(face[1] - y * height) < face[2]]
        if found:
            cx, cy, w = found[0]
            return square(image, cx, cy + 0.12 * w, 2.4 * w), "找到脸"
        # 指定的位置附近没认出脸：按图高 22% 取头肩。
        return square(image, x * width, y * height + 0.03 * height, 0.22 * height), "按位置"
    found = faces(image)
    if found:
        cx, cy, w = found[0]
        return square(image, cx, cy + 0.12 * w, 2.4 * w), "找到脸"
    # 没认出脸（侧脸、插画、动物）：竖图取上部，横图取中间。
    side = min(width, height)
    top = 0.04 * height if height > width else (height - side) / 2
    return square(image, width / 2, top + side / 2, side), "居中"


def read_list(path):
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [part.strip() for part in line.split("\t")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            sys.exit(f"✗ images.tsv:{number} 应该是「名字<tab>来源」，注意用 Tab 分隔")
        hint = None
        if len(parts) > 2 and parts[2]:
            try:
                hint = tuple(float(value) for value in parts[2].split())
            except ValueError:
                sys.exit(f"✗ images.tsv:{number} 位置应该是两个或三个 0–1 的数")
            if len(hint) not in (2, 3):
                sys.exit(f"✗ images.tsv:{number} 位置应该是两个或三个 0–1 的数")
        entries.append((parts[0], parts[1], hint))
    return entries


def main():
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    force = "--force" in sys.argv
    if len(args) != 1:
        sys.exit("用法：fetch_images.py <工作目录> [--force]")
    work = Path(args[0]).expanduser().resolve()
    listing = work / "images.tsv"
    if not listing.exists():
        sys.exit(f"✗ 没有 {listing}")
    source_dir, image_dir = work / "source", work / "images"
    source_dir.mkdir(exist_ok=True)
    image_dir.mkdir(exist_ok=True)

    done, failed, methods = 0, [], {}
    for name, source, hint in read_list(listing):
        is_cover = name in COVERS
        out = image_dir / (COVER_FILE if is_cover else f"{name}.png")
        if out.exists() and not force:
            continue
        try:
            data = load(source, work)
            (source_dir / f"{name}.bin").write_bytes(data)
            image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        except Exception as error:
            failed.append(f"{name}：{error}")
            continue
        if is_cover:
            image.thumbnail((COVER_LONG_EDGE, COVER_LONG_EDGE), Image.LANCZOS)
            method = "封面"
        else:
            image, method = crop_avatar(image, hint)
            image = image.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
        image.save(out)
        methods.setdefault(method, []).append(name)
        done += 1

    print(f"✓ 新处理 {done} 张图片")
    for method, names in methods.items():
        if method in ("居中", "按位置"):
            print(f"! {method}裁的（没认出脸，最好看一眼）：{'、'.join(names)}")
    if failed:
        print(f"✗ {len(failed)} 张没拿到，换个来源再运行一次：")
        for line in failed:
            print(f"  {line}")


if __name__ == "__main__":
    main()
