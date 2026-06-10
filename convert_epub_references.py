from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote
import xml.etree.ElementTree as ET

from lxml import html


ROOT = Path(__file__).resolve().parent
SOURCE_DIR = ROOT / "references"
OUTPUT_DIR = ROOT / "references_md"

OPF_NS = "{http://www.idpf.org/2007/opf}"
DC_NS = "{http://purl.org/dc/elements/1.1/}"


def normalize_text(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def safe_filename(name: str) -> str:
    name = normalize_text(name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"_+", "_", name).strip(" ._")
    return name or "untitled"


def read_opf_path(epub: zipfile.ZipFile) -> str:
    try:
        root = ET.fromstring(epub.read("META-INF/container.xml"))
        ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
        rootfile = root.find(".//c:rootfile", ns)
        if rootfile is not None:
            return rootfile.attrib["full-path"]
    except Exception:
        pass
    return "content.opf"


def spine_paths(epub: zipfile.ZipFile) -> list[str]:
    opf_path = read_opf_path(epub)
    root = ET.fromstring(epub.read(opf_path))
    base = posixpath.dirname(opf_path)
    manifest: dict[str, tuple[str, str]] = {}

    for item in root.findall(f".//{OPF_NS}manifest/{OPF_NS}item"):
        item_id = item.attrib.get("id")
        href = item.attrib.get("href")
        media_type = item.attrib.get("media-type", "")
        if item_id and href:
            manifest[item_id] = (href, media_type)

    paths: list[str] = []
    for itemref in root.findall(f".//{OPF_NS}spine/{OPF_NS}itemref"):
        idref = itemref.attrib.get("idref")
        if not idref or idref not in manifest:
            continue
        href, media_type = manifest[idref]
        href = unquote(href)
        path = posixpath.normpath(posixpath.join(base, href)) if base else href
        if path.lower().endswith((".html", ".xhtml", ".htm")) or media_type.endswith("xhtml+xml"):
            paths.append(path)
    return paths


def text_from_element(element) -> str:
    return normalize_text(element.text_content())


def convert_html_to_markdown(epub: zipfile.ZipFile, path: str) -> str:
    doc = html.fromstring(epub.read(path))
    blocks = doc.xpath("//body//*[self::h1 or self::h2 or self::h3 or self::h4 or self::p]")
    markdown: list[str] = []

    for block in blocks:
        tag = block.tag.lower()
        # Avoid duplicated nested text from malformed or deeply styled EPUB blocks.
        if block.xpath("ancestor::*[self::h1 or self::h2 or self::h3 or self::h4 or self::p]"):
            continue

        text = text_from_element(block)
        if not text:
            continue

        class_names = set((block.get("class") or "").split())
        if tag in {"h1", "h2", "h3", "h4"}:
            level = {"h1": "#", "h2": "##", "h3": "#", "h4": "##"}[tag]
            markdown.append(f"{level} {text}")
        elif class_names & {"CN", "calibre_8"} and re.fullmatch(r"[0-9A-Za-z一二三四五六七八九十百零〇]+", text):
            markdown.append(f"## {text}")
        elif class_names & {"CT", "calibre_16"}:
            markdown.append(f"# {text}")
        else:
            markdown.append(text)

    return "\n\n".join(markdown).strip() + "\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def find_epubs() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in SOURCE_DIR.glob("*.epub"):
        with zipfile.ZipFile(path) as epub:
            names = set(epub.namelist())
        if "text/part0002.html" in names:
            found["witness_en"] = path
        elif "index_split_019.html" in names:
            found["witness_zh"] = path
        elif "OEBPS/Text/1.xhtml" in names:
            found["roger_zh"] = path
    missing = {"witness_en", "witness_zh", "roger_zh"} - set(found)
    if missing:
        raise RuntimeError(f"Missing expected EPUB sources: {', '.join(sorted(missing))}")
    return found


def convert_witness_english(source: Path) -> list[Path]:
    out_file = OUTPUT_DIR / "控方证人_英文" / "01_the_witness_for_the_prosecution.md"
    with zipfile.ZipFile(source) as epub:
        text = convert_html_to_markdown(epub, "text/part0002.html")
    write_text(out_file, text)
    return [out_file]


def convert_witness_chinese(source: Path) -> list[Path]:
    out_file = OUTPUT_DIR / "控方证人_中文" / "01_控方证人.md"
    with zipfile.ZipFile(source) as epub:
        title = convert_html_to_markdown(epub, "index_split_018.html")
        body = convert_html_to_markdown(epub, "index_split_019.html")
    write_text(out_file, f"{title.rstrip()}\n\n{body}")
    return [out_file]


def convert_roger_chinese(source: Path) -> list[Path]:
    out_dir = OUTPUT_DIR / "罗杰疑案"
    written: list[Path] = []
    with zipfile.ZipFile(source) as epub:
        ordered = spine_paths(epub)

        title_page = "OEBPS/Text/Section0001.xhtml"
        if title_page in ordered:
            out_file = out_dir / "00_书名页.md"
            write_text(out_file, convert_html_to_markdown(epub, title_page))
            written.append(out_file)

        chapter_paths = [
            path
            for path in ordered
            if re.fullmatch(r"OEBPS/Text/[0-9]+\.xhtml", path)
        ]
        chapter_paths.sort(key=lambda p: int(posixpath.basename(p).split(".")[0]))

        for index, chapter_path in enumerate(chapter_paths, start=1):
            md = convert_html_to_markdown(epub, chapter_path)
            first_line = next((line[2:].strip() for line in md.splitlines() if line.startswith("# ")), "")
            title = safe_filename(first_line)
            out_file = out_dir / f"{index:02d}_{title}.md"
            write_text(out_file, md)
            written.append(out_file)
    return written


def relative_list(paths: list[Path]) -> list[str]:
    return [path.relative_to(OUTPUT_DIR).as_posix() for path in paths]


def write_index(groups: dict[str, list[Path]], sources: dict[str, Path]) -> None:
    lines = [
        "# EPUB 转 Markdown 索引",
        "",
        "这些文件由 `convert_epub_references.py` 从 `references` 下的 EPUB 生成。正文文件只做 HTML 到 Markdown 的结构转换，按原段落保留文本；短篇合集只抽取《控方证人 / The Witness for the Prosecution》，未导出其他短篇。",
        "",
        "## 源文件",
        "",
        f"- 控方证人（英文）：`{sources['witness_en'].relative_to(ROOT).as_posix()}`",
        f"- 控方证人（中文）：`{sources['witness_zh'].relative_to(ROOT).as_posix()}`",
        f"- 罗杰疑案（中文）：`{sources['roger_zh'].relative_to(ROOT).as_posix()}`",
        "",
    ]

    labels = {
        "witness_zh": "控方证人（中文）",
        "witness_en": "The Witness for the Prosecution（英文）",
        "roger_zh": "罗杰疑案（中文）",
    }
    for key in ["witness_zh", "witness_en", "roger_zh"]:
        lines.extend([f"## {labels[key]}", ""])
        for rel in relative_list(groups[key]):
            lines.append(f"- [{rel}]({rel})")
        lines.append("")

    write_text(OUTPUT_DIR / "00_index.md", "\n".join(lines).rstrip() + "\n")


def main() -> None:
    sources = find_epubs()
    groups = {
        "witness_zh": convert_witness_chinese(sources["witness_zh"]),
        "witness_en": convert_witness_english(sources["witness_en"]),
        "roger_zh": convert_roger_chinese(sources["roger_zh"]),
    }
    write_index(groups, sources)
    total = sum(len(paths) for paths in groups.values()) + 1
    print(f"Generated {total} Markdown files under {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
