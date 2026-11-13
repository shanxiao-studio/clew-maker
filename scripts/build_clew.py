#!/usr/bin/env python3
"""检查工作目录里的人物和关系，打包成线团能导入的 .clew 文件。

用法：
    <python> scripts/build_clew.py <工作目录>
    <python> scripts/build_clew.py <工作目录> --check    只检查，不生成文件

工作目录：
    work.json       {"name": 名称, "summary": 简介, "tags": [标签]}；id 和 revision 由本脚本写回
    people.txt      名字 | 别名 | 简介
    relations.txt   A > B | 大类 | A 是 B 的… / B 是 A 的…，或 A = B | 大类 | 称呼
    images/         fetch_images.py 生成的头像（<名字>.png）和封面（_cover.png），可选

输出 <工作目录>/<名称>.clew。每次生成 revision 加 1，线团导入时会认出这是同一份关系图的新版本。
格式见 https://shanxiao.app/clew/skill.md 里的说明，或本 skill 的 references/format.md。
"""

import io
import json
import re
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

CATEGORIES = {
    "亲属": "kin", "親屬": "kin", "kin": "kin", "family": "kin",
    "情感": "romance", "romance": "romance", "love": "romance",
    "敌对": "hostile", "敵對": "hostile", "hostile": "hostile", "enemy": "hostile",
    "从属": "subordinate", "從屬": "subordinate", "subordinate": "subordinate",
    "社交": "social", "social": "social",
    "其他": "other", "other": "other",
}
AVATAR_SIZE = 512
COVER_LONG_EDGE = 1600
COVER_FILE = "_cover.png"
COMFORTABLE_PEOPLE = 500
ALIAS_SEPARATORS = re.compile(r"[,，、]")

problems = []


def problem(message):
    problems.append(message)


def lines(path):
    if not path.exists():
        sys.exit(f"✗ 没有 {path.name}")
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if line and not line.startswith("#"):
            yield number, line


def read_people(path):
    people = []
    for number, line in lines(path):
        parts = [part.strip() for part in line.split("|")]
        parts += [""] * (3 - len(parts))
        if len(parts) != 3 or not parts[0]:
            problem(f"{path.name}:{number} 应该是「名字 | 别名 | 简介」（简介里不能有「|」）")
            continue
        aliases = [alias.strip() for alias in ALIAS_SEPARATORS.split(parts[1]) if alias.strip()]
        people.append({"name": parts[0], "aliases": aliases, "bio": parts[2]})
    names = [person["name"] for person in people]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        problem(f"人物重名（同名的两个人要用不同的主名区分，比如加上身份）：{'、'.join(duplicates)}")
    return people


def read_relations(path, names):
    relations, pairs = [], {}
    for number, line in lines(path):
        parts = [part.strip() for part in line.split("|")]
        if len(parts) == 2:
            parts.append("")
        if len(parts) != 3:
            problem(f"{path.name}:{number} 应该是「A > B | 大类 | 称呼」")
            continue
        ends, category, labels = parts
        symmetric = "=" in ends
        a, _, b = (end.strip() for end in ends.partition("=" if symmetric else ">"))
        if not symmetric and ">" not in ends:
            problem(f"{path.name}:{number} 两端之间要用「>」或「=」")
            continue
        missing = [name for name in (a, b) if name not in names]
        if missing:
            problem(f"{path.name}:{number} people.txt 里没有这个人物：{'、'.join(missing)}")
            continue
        if a == b:
            problem(f"{path.name}:{number} 两端是同一个人")
            continue
        if category not in CATEGORIES:
            problem(f"{path.name}:{number} 不认识的大类「{category}」，只能是 亲属 / 情感 / 敌对 / 从属 / 社交 / 其他")
            continue
        pair = frozenset((a, b))
        if pair in pairs:
            problem(f"{path.name}:{number} {a} 和 {b} 之间已经有一根线了（第 {pairs[pair]} 行），多层关系用「 · 」写在同一行")
            continue
        pairs[pair] = number
        label, _, reverse = (text.strip() for text in labels.partition("/"))
        relations.append({"a": a, "b": b, "category": CATEGORIES[category], "symmetric": symmetric,
                          "label": label or None, "reverse": None if symmetric else (reverse or None)})
    return relations


def heic(path, size, square):
    """PNG/JPEG → HEIC 字节。头像正方形 512，封面长边 1600。"""
    from PIL import Image
    import pillow_heif

    pillow_heif.register_heif_opener()
    image = Image.open(path).convert("RGB")
    if square:
        side = min(image.size)
        left, top = (image.width - side) // 2, (image.height - side) // 2
        image = image.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
    else:
        image.thumbnail((size, size), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="HEIF", quality=80)
    return buffer.getvalue()


def file_name(name):
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "未命名"


def main():
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    check_only = "--check" in sys.argv
    if len(args) != 1:
        sys.exit("用法：build_clew.py <工作目录> [--check]")
    work = Path(args[0]).expanduser().resolve()
    work_file = work / "work.json"
    if not work_file.exists():
        sys.exit(f"✗ 没有 {work_file}")
    meta = json.loads(work_file.read_text(encoding="utf-8"))
    if not meta.get("name"):
        problem("work.json 缺少 name")

    people = read_people(work / "people.txt")
    names = {person["name"] for person in people}
    relations = read_relations(work / "relations.txt", names)

    images = work / "images"
    known_images = {path.stem for path in images.glob("*.png")} if images.exists() else set()
    strays = sorted(known_images - names - {Path(COVER_FILE).stem})
    if strays:
        problem(f"images/ 里这些图片对不上人物（改名后记得改图片名或 images.tsv）：{'、'.join(strays)}")

    if problems:
        print(f"✗ 有 {len(problems)} 处要改：")
        for line in problems:
            print(f"  {line}")
        sys.exit(1)

    connected = {name for relation in relations for name in (relation["a"], relation["b"])}
    isolated = [person["name"] for person in people if person["name"] not in connected]
    without_avatar = [person["name"] for person in people if person["name"] not in known_images]

    if check_only:
        print(f"✓ 检查通过：{len(people)} 个人物，{len(relations)} 根线，{len(people) - len(without_avatar)} 个有头像")
        if isolated:
            print(f"! 没有任何关系的人物：{'、'.join(isolated)}")
        return

    if not meta.get("id"):
        meta["id"] = str(uuid.uuid4()).upper()
    meta["revision"] = int(meta.get("revision", 0)) + 1
    namespace = uuid.UUID(meta["id"])
    ids = {person["name"]: str(uuid.uuid5(namespace, "person:" + person["name"])).upper() for person in people}

    files = {}
    people_json = []
    for person in people:
        entry = {"id": ids[person["name"]], "name": person["name"]}
        if person["aliases"]:
            entry["aliases"] = person["aliases"]
        if person["name"] in known_images:
            entry["avatar"] = f"images/{entry['id']}.heic"
            files[entry["avatar"]] = heic(images / f"{person['name']}.png", AVATAR_SIZE, square=True)
        if person["bio"]:
            entry["bio"] = [{"text": person["bio"]}]
        people_json.append(entry)

    relations_json = []
    for relation in relations:
        a, b = ids[relation["a"]], ids[relation["b"]]
        key = "relation:" + "|".join(sorted((relation["a"], relation["b"])))
        segment = {"category": relation["category"]}
        if relation["symmetric"]:
            source, target = a, b
            if relation["label"]:
                segment["label"] = relation["label"]
        else:
            # 「A 是 B 的 X / B 是 A 的 Y」存成 from B → to A：B 看 A 是 X，A 看 B 是 Y。
            source, target = b, a
            if relation["label"]:
                segment["label"] = relation["label"]
            if relation["reverse"]:
                segment["reverseLabel"] = relation["reverse"]
        relations_json.append({"id": str(uuid.uuid5(namespace, key)).upper(), "from": source, "to": target,
                               "symmetric": relation["symmetric"], "segments": [segment]})

    clew = {"id": str(uuid.uuid5(namespace, "clew")).upper(), "name": meta["name"]}
    if meta.get("summary"):
        clew["summary"] = meta["summary"]
    if meta.get("tags"):
        clew["tags"] = meta["tags"]
    if (images / COVER_FILE).exists():
        clew["cover"] = "images/cover.heic"
        files["images/cover.heic"] = heic(images / COVER_FILE, COVER_LONG_EDGE, square=False)
    clew["people"] = people_json
    clew["relations"] = relations_json

    manifest = {"format": "clew", "version": 1,
                "exportedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "origin": {"clewId": meta["id"], "revision": meta["revision"], "author": ""}}

    out = work / f"{file_name(meta['name'])}.clew"
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2), zipfile.ZIP_DEFLATED)
        archive.writestr("clew.json", json.dumps(clew, ensure_ascii=False, indent=2), zipfile.ZIP_DEFLATED)
        for path, data in sorted(files.items()):
            archive.writestr(path, data, zipfile.ZIP_STORED)
    work_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"✓ {out}")
    print(f"  {len(people_json)} 个人物，{len(relations_json)} 根线，"
          f"{len(people_json) - len(without_avatar)} 个有头像，第 {meta['revision']} 版，{out.stat().st_size // 1024} KB")
    if without_avatar:
        print(f"! 没有头像（显示名字首字）：{'、'.join(without_avatar)}")
    if isolated:
        print(f"! 没有任何关系的人物：{'、'.join(isolated)}")
    if len(people_json) > COMFORTABLE_PEOPLE:
        print(f"! 人物超过 {COMFORTABLE_PEOPLE} 个，在较旧的手机上全景可能会卡")


if __name__ == "__main__":
    main()
