"""Minimal Markdown -> HTML for the reports this app writes (no external dependency).

Supports what to_markdown() emits: ATX headings, blockquotes, pipe tables, ordered/unordered lists with
indented continuation lines, paragraphs, and inline **bold**, *italic* and `code`. Input is HTML-escaped.
"""

from __future__ import annotations

import html
import re

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITAL = re.compile(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])")
_CODE = re.compile(r"`([^`]+)`")
_OL = re.compile(r"^(\d+)\.\s+(.*)")
_UL = re.compile(r"^[-*]\s+(.*)")
_HEAD = re.compile(r"^(#{1,6})\s+(.*)")


def inline(text: str) -> str:
    parts = _CODE.split(html.escape(text, quote=False))
    out = []
    for i, part in enumerate(parts):
        if i % 2:
            out.append(f"<code>{part}</code>")
        else:
            out.append(_ITAL.sub(r"<em>\1</em>", _BOLD.sub(r"<strong>\1</strong>", part)))
    return "".join(out)


def _row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def to_html(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if m := _HEAD.match(line):
            n = len(m.group(1))
            out.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
            i += 1
        elif line.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].startswith(">"):
                quote.append(lines[i].lstrip(">").strip())
                i += 1
            out.append(f"<blockquote>{inline(' '.join(quote))}</blockquote>")
        elif line.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]):
            head = _row(line)
            i += 2
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(_row(lines[i]))
                i += 1
            th = "".join(f"<th>{inline(c)}</th>" for c in head)
            tb = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows)
            out.append(f"<div class='tablewrap'><table><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table></div>")
        elif _OL.match(line) or _UL.match(line):
            ordered = bool(_OL.match(line))
            items: list[list[str]] = []
            while i < len(lines):
                cur = lines[i]
                m = _OL.match(cur) if ordered else _UL.match(cur)
                if m:
                    items.append([m.group(2) if ordered else m.group(1)])
                elif cur.startswith(("  ", "\t")) and cur.strip() and items:
                    items[-1].append(cur.strip())  # continuation line of the current item
                else:
                    break
                i += 1
            tag = "ol" if ordered else "ul"
            lis = "".join("<li>" + "<br>".join(inline(x) for x in it) + "</li>" for it in items)
            out.append(f"<{tag}>{lis}</{tag}>")
        else:
            para = []
            while i < len(lines) and lines[i].strip() and not (
                    _HEAD.match(lines[i]) or lines[i].startswith(">") or _OL.match(lines[i])
                    or _UL.match(lines[i]) or lines[i].lstrip().startswith("|")):
                para.append(lines[i].strip())
                i += 1
            out.append(f"<p>{inline(' '.join(para))}</p>")
    return "\n".join(out)
