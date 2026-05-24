"""Convert Markdown → Medium paragraph delta objects.

Medium's internal editor protocol (used by /p/{id}/deltas) represents each
paragraph as a dict:

    {
        "type": 1,            # operation: 1 = insert paragraph
        "index": <int>,       # position in doc (0-based)
        "paragraph": {
            "type": <int>,    # paragraph type (see constants below)
            "text": <str>,    # plain-text content of the paragraph
            "markups": [      # inline formatting ranges
                {
                    "type": <int>,  # markup type
                    "start": <int>, # char offset (inclusive)
                    "end": <int>,   # char offset (exclusive)
                    "href": <str>,  # only for link (type 3)
                    "anchorType": 0,
                },
            ],
        },
    }

Paragraph types (para.type):
    1  = P          (normal paragraph)
    2  = H1         (large title)
    3  = H2         (section header)
    8  = H3         (sub-section)
    9  = BLOCKQUOTE
   10  = PRE        (code block, verbatim)
   13  = ULI        (unordered list item)
   15  = OLI        (ordered list item)

Markup types (markup.type):
    1  = STRONG  (bold)
    2  = EM      (italic)
    3  = A       (hyperlink)
   10  = CODE    (inline code)
   11  = STRIKETHROUGH
"""

from __future__ import annotations

import re
from typing import Any

import markdown as _md
from selectolax.parser import HTMLParser, Node

# ── paragraph type constants ──────────────────────────────────────────────────
P_PARA       = 1
P_H1         = 2
P_H2         = 3
P_H3         = 8
P_BLOCKQUOTE = 9
P_PRE        = 10
P_ULI        = 13
P_OLI        = 15

# ── markup type constants ─────────────────────────────────────────────────────
M_BOLD   = 1
M_ITALIC = 2
M_LINK   = 3
M_CODE   = 10
M_STRIKE = 11

_TAG_TO_PARA: dict[str, int] = {
    "p":  P_PARA,
    "h1": P_H1,
    "h2": P_H2,
    "h3": P_H3,
    "h4": P_H3,
    "h5": P_H3,
    "h6": P_H3,
}

_TEXT_NODE = "-text"  # selectolax's sentinel tag for text nodes


def _is_text(node: Node) -> bool:
    return (node.tag or "") == _TEXT_NODE


# ── markdown pre-processing ───────────────────────────────────────────────────

def _strip_leading_h1(md: str) -> str:
    """Remove the first ``# Title`` line from a markdown document.

    When the caller passes the title separately via ``--title``, the H1
    heading in the body would create a duplicate title block in Medium's
    editor.  This strips it so the heading only appears in the Title field.
    """
    lines = md.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            # Remove this line and any immediately following blank line
            del lines[i]
            if i < len(lines) and not lines[i].strip():
                del lines[i]
            return "\n".join(lines)
        if stripped:  # stop after first non-blank line if it's not an H1
            break
    return md


def _preprocess(md: str) -> str:
    """Fix edge cases that confuse the markdown parser.

    1. Ensure a blank line exists before list markers when the preceding
       line is non-empty.  Without this, ``python-markdown`` collapses the
       preceding paragraph and the list into a single <p> element.

    2. Preserve ``---`` section dividers as explicit ``<hr>`` (they already
       parse correctly but we make sure triple-dash lines are not confused
       with setext-style heading underlines by normalising spacing).
    """
    # Insert blank line before a list item that immediately follows text.
    md = re.sub(r"(?m)(\S[ \t]*)\n([ \t]*[-*+] )", r"\1\n\n\2", md)
    # Same for ordered list items (digit + dot or paren).
    md = re.sub(r"(?m)(\S[ \t]*)\n([ \t]*\d+[.)]\s)", r"\1\n\n\2", md)
    return md


# ── inline markup extraction ──────────────────────────────────────────────────

def _collect_inline(node: Node) -> tuple[str, list[dict[str, Any]]]:
    """Walk all children of *node*, return (plain_text, markups)."""
    buf: list[str] = []
    markups: list[dict[str, Any]] = []

    def _walk(n: Node) -> None:
        tag = (n.tag or "").lower()

        if _is_text(n):
            buf.append(n.text() or "")
            return

        if tag == "br":
            buf.append("\n")
            return

        markup_type: int | None = None
        href: str | None = None

        if tag in ("strong", "b"):
            markup_type = M_BOLD
        elif tag in ("em", "i"):
            markup_type = M_ITALIC
        elif tag == "a":
            markup_type = M_LINK
            href = (n.attrs or {}).get("href") or ""
        elif tag == "code":
            markup_type = M_CODE
        elif tag in ("del", "s", "strike"):
            markup_type = M_STRIKE

        start = len("".join(buf))

        child = n.child
        while child:
            _walk(child)
            child = child.next

        if markup_type is not None:
            end = len("".join(buf))
            if end > start:
                m: dict[str, Any] = {"type": markup_type, "start": start, "end": end}
                if href is not None:
                    m["href"] = href
                    m["anchorType"] = 0
                markups.append(m)

    child = node.child
    while child:
        _walk(child)
        child = child.next

    return "".join(buf), markups


# ── block extraction ──────────────────────────────────────────────────────────

def _process_block(
    node: Node,
    results: list[tuple[int, str, list[dict[str, Any]]]],
) -> None:
    tag = (node.tag or "").lower()

    # skip bare whitespace text nodes between blocks
    if _is_text(node):
        text = (node.text() or "").strip()
        if text:
            results.append((P_PARA, text, []))
        return

    if tag == "pre":
        code_node = node.css_first("code")
        raw = (code_node or node).text(deep=True) or ""
        raw = raw.strip("\n")
        if raw.strip():
            results.append((P_PRE, raw, []))
        return

    if tag == "blockquote":
        text, markups = _collect_inline(node)
        text = text.strip()
        if text:
            results.append((P_BLOCKQUOTE, text, markups))
        return

    if tag in ("ul", "ol"):
        item_type = P_ULI if tag == "ul" else P_OLI
        item = node.child
        while item:
            item_tag = (item.tag or "").lower()
            if item_tag == "li":
                inline_buf: list[str] = []
                inline_markups: list[dict[str, Any]] = []
                c = item.child
                while c:
                    c_tag = (c.tag or "").lower()
                    if c_tag in ("ul", "ol"):
                        # nested list — flush current text first, then recurse
                        text = "".join(inline_buf).strip()
                        if text:
                            results.append((item_type, text, inline_markups))
                            inline_buf = []
                            inline_markups = []
                        _process_block(c, results)
                    elif c_tag == "p":
                        # sane_lists wraps li content in <p>; unwrap it.
                        # IMPORTANT: reset the buffer first so that any
                        # preceding whitespace text nodes (\n) from inside
                        # <li> don't shift the markup offsets.
                        inline_buf = []
                        inline_markups = []
                        t, m = _collect_inline(c)
                        inline_buf.append(t)
                        inline_markups.extend(m)
                    elif _is_text(c):
                        # Skip bare whitespace text nodes inside <li> that
                        # appear before the <p> wrapper (sane_lists artefact)
                        chunk = (c.text() or "").strip()
                        if chunk:
                            off = len("".join(inline_buf))
                            inline_buf.append(chunk)
                    else:
                        t, m = _collect_inline(c)
                        off = len("".join(inline_buf))
                        inline_buf.append(t)
                        for mk in m:
                            mk2 = dict(mk); mk2["start"] += off; mk2["end"] += off
                            inline_markups.append(mk2)
                    c = c.next
                text = "".join(inline_buf).strip()
                if text:
                    results.append((item_type, text, inline_markups))
            item = item.next
        return

    if tag == "hr":
        # Skip --- dividers (Medium uses its own section separators)
        return

    para_type = _TAG_TO_PARA.get(tag)
    if para_type is not None:
        text, markups = _collect_inline(node)
        text = text.strip()
        if text:
            results.append((para_type, text, markups))
        return

    # unknown block tag — recurse into direct children
    child = node.child
    while child:
        _process_block(child, results)
        child = child.next


def _blocks_from_html(html: str) -> list[tuple[int, str, list[dict[str, Any]]]]:
    parser = HTMLParser(html)
    body = parser.css_first("body")
    if body is None:
        return []
    results: list[tuple[int, str, list[dict[str, Any]]]] = []
    node = body.child
    while node:
        _process_block(node, results)
        node = node.next
    return results


# ── public API ────────────────────────────────────────────────────────────────

def markdown_to_deltas(
    md_text: str,
    *,
    start_index: int = 0,
    strip_h1: bool = True,
) -> list[dict[str, Any]]:
    """Convert a Markdown string to Medium paragraph delta objects.

    Handles:
    - Headings (H1→H2→H3 mapped to Medium types 2/3/8)
    - Bold, italic, inline code, strikethrough
    - Hyperlinks (both ``[text](url)`` and bare URLs auto-linked)
    - Fenced and indented code blocks
    - Blockquotes
    - Bullet and ordered lists (including bold/italic in list items)
    - Horizontal rules (silently dropped — Medium has its own separator)
    - Text immediately followed by a list (pre-processed to avoid parser
      collapsing them into one ``<p>``)

    Args:
        md_text: Raw Markdown source.
        start_index: Index offset for the first delta.  Pass ``1`` when a
                     title delta already occupies index 0.
        strip_h1: If True (default), removes a leading ``# Title`` from the
                  body.  Set to False only when you are NOT passing a title
                  separately and want the H1 rendered in the body.

    Returns:
        List of delta dicts ready for ``POST /p/{id}/deltas``.
    """
    if strip_h1:
        md_text = _strip_leading_h1(md_text)
    md_text = _preprocess(md_text)
    html = _md.markdown(
        md_text,
        extensions=["fenced_code", "tables", "sane_lists", "mdx_linkify"],
    )
    blocks = _blocks_from_html(html)
    return [
        {
            "type": 1,
            "index": start_index + i,
            "paragraph": {"type": para_type, "text": text, "markups": markups},
        }
        for i, (para_type, text, markups) in enumerate(blocks)
    ]
