from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

from lxml import html


ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = ROOT / "references_md" / "三体全集"
TARGET_CHARS = 20_000

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


def find_source() -> Path:
    candidates = [p for p in ROOT.glob("*.epub") if "三体" in p.name]
    if not candidates:
        raise FileNotFoundError("No Three Body EPUB found in the workspace root.")
    return candidates[0]


def read_opf_path(epub: zipfile.ZipFile) -> str:
    root = ET.fromstring(epub.read("META-INF/container.xml"))
    ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
    rootfile = root.find(".//c:rootfile", ns)
    if rootfile is None:
        return "content.opf"
    return rootfile.attrib["full-path"]


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
        path = posixpath.normpath(posixpath.join(base, href)) if base else href
        if path.lower().endswith((".html", ".xhtml", ".htm")) or media_type.endswith("xhtml+xml"):
            paths.append(path)
    return paths


def blocks_from_html(epub: zipfile.ZipFile, path: str) -> list[tuple[str, str]]:
    doc = html.fromstring(epub.read(path))
    result: list[tuple[str, str]] = []
    query = "//body//*[self::h1 or self::h2 or self::h3 or self::h4 or self::p]"
    for element in doc.xpath(query):
        if element.xpath("ancestor::*[self::h1 or self::h2 or self::h3 or self::h4 or self::p]"):
            continue
        text = normalize_text(element.text_content())
        if not text:
            continue
        tag = element.tag.lower()
        if tag in {"h1", "h2", "h3", "h4"}:
            result.append(("heading", text))
        else:
            result.append(("paragraph", text))
    return result


def markdown_from_blocks(blocks: list[tuple[str, str]], title_override: str | None = None) -> str:
    lines: list[str] = []
    for index, (kind, text) in enumerate(blocks):
        if index == 0 and title_override:
            lines.append(f"# {title_override}")
            if kind == "heading" and text == title_override:
                continue
        elif kind == "heading":
            lines.append(f"# {text}" if not lines else f"## {text}")
        else:
            lines.append(text)
    return "\n\n".join(lines).strip() + "\n"


def title_from_blocks(blocks: list[tuple[str, str]], fallback: str) -> str:
    for kind, text in blocks:
        if kind == "heading":
            return text
    for _, text in blocks:
        if len(text) <= 80:
            return text
    return fallback


def split_blocks(blocks: list[tuple[str, str]], target_chars: int = TARGET_CHARS) -> list[list[tuple[str, str]]]:
    if not blocks:
        return []

    first_heading: list[tuple[str, str]] = []
    rest = blocks
    if blocks[0][0] == "heading":
        first_heading = [blocks[0]]
        rest = blocks[1:]

    chunks: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    current_len = 0
    for block in rest:
        block_len = len(block[1])
        if current and current_len + block_len > target_chars:
            chunks.append(first_heading + current)
            current = []
            current_len = 0
        current.append(block)
        current_len += block_len
    if current:
        chunks.append(first_heading + current)
    return chunks or [blocks]


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_single(epub: zipfile.ZipFile, source_path: str, out_path: Path, title: str | None = None) -> Path:
    blocks = blocks_from_html(epub, source_path)
    write_text(out_path, markdown_from_blocks(blocks, title))
    return out_path


def write_split(epub: zipfile.ZipFile, source_path: str, out_dir: Path, prefix: str) -> list[Path]:
    blocks = blocks_from_html(epub, source_path)
    base_title = title_from_blocks(blocks, prefix)
    chunks = split_blocks(blocks)
    written: list[Path] = []
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        title = base_title if total == 1 else f"{base_title}（{index:02d}）"
        file_name = f"{prefix}_{index:02d}_{safe_filename(base_title)}.md" if total > 1 else f"{prefix}_{safe_filename(base_title)}.md"
        out_path = out_dir / file_name
        write_text(out_path, markdown_from_blocks(chunk, title))
        written.append(out_path)
    return written


def append_index_section(index_path: Path, generated: list[Path], source: Path) -> None:
    existing = index_path.read_text(encoding="utf-8") if index_path.exists() else "# EPUB 转 Markdown 索引\n"
    marker = "## 三体全集（共3册）"
    if marker in existing:
        existing = existing.split(marker)[0].rstrip() + "\n"

    lines = [
        "",
        marker,
        "",
        f"- 源文件：`{source.relative_to(ROOT).as_posix()}`",
        f"- 输出目录：`references_md/三体全集`",
        "- 拆分说明：第一册按原章节拆分；第二册和第三册保留原有部/章标题，并将超长正文按段落边界切成约 2 万字符的小卷；广告页未导出。",
        "",
    ]
    for path in generated:
        rel = path.relative_to(index_path.parent).as_posix()
        lines.append(f"- [{rel}]({rel})")
    write_text(index_path, existing.rstrip() + "\n" + "\n".join(lines).rstrip() + "\n")


def main() -> None:
    source = find_source()
    generated: list[Path] = []
    with zipfile.ZipFile(source) as epub:
        paths = set(spine_paths(epub))

        book1 = OUTPUT_ROOT / "01_三体I"
        book2 = OUTPUT_ROOT / "02_三体II_黑暗森林"
        book3 = OUTPUT_ROOT / "03_三体III_死神永生"

        generated.append(write_single(epub, "OEBPS/Text/part0002.xhtml", book1 / "00_刘慈欣2018克拉克奖获奖感言.md"))
        generated.append(write_single(epub, "OEBPS/Text/part0003.xhtml", book1 / "00_目录.md"))
        for number in range(4, 40):
            source_path = f"OEBPS/Text/part{number:04d}.xhtml"
            if source_path not in paths:
                continue
            blocks = blocks_from_html(epub, source_path)
            title = title_from_blocks(blocks, f"part{number:04d}")
            out_name = f"{number - 3:02d}_{safe_filename(title)}.md"
            generated.append(write_single(epub, source_path, book1 / out_name))
        generated.append(write_single(epub, "OEBPS/Text/part0040.xhtml", book1 / "37_后记.md"))
        generated.append(write_single(epub, "OEBPS/Text/part0041.xhtml", book1 / "38_脚注.md"))

        generated.append(write_single(epub, "OEBPS/Text/part0043.xhtml", book2 / "00_目录.md"))
        generated += write_split(epub, "OEBPS/Text/part0044.xhtml", book2, "01")
        generated += write_split(epub, "OEBPS/Text/part0045.xhtml", book2, "02")
        generated += write_split(epub, "OEBPS/Text/part0046.xhtml", book2, "03")
        generated += write_split(epub, "OEBPS/Text/part0047.xhtml", book2, "04")
        generated.append(write_single(epub, "OEBPS/Text/part0048.xhtml", book2 / "05_脚注.md"))

        generated.append(write_single(epub, "OEBPS/Text/part0050.xhtml", book3 / "00_目录.md"))
        generated.append(write_single(epub, "OEBPS/Text/part0051.xhtml", book3 / "01_纪年对照表.md"))
        generated += write_split(epub, "OEBPS/Text/part0052.xhtml", book3, "02")
        generated += write_split(epub, "OEBPS/Text/part0053.xhtml", book3, "03")
        generated += write_split(epub, "OEBPS/Text/part0054.xhtml", book3, "04")
        generated += write_split(epub, "OEBPS/Text/part0055.xhtml", book3, "05")
        generated += write_split(epub, "OEBPS/Text/part0056.xhtml", book3, "06")
        generated += write_split(epub, "OEBPS/Text/part0057.xhtml", book3, "07")
        generated.append(write_single(epub, "OEBPS/Text/part0060.xhtml", book3 / "08_脚注.md"))

    append_index_section(ROOT / "references_md" / "00_index.md", generated, source)
    print(f"Generated {len(generated)} Markdown files under {OUTPUT_ROOT}")


if __name__ == "__main__":
    main()
