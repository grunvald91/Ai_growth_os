"""Small, safe presentation renderer for AI-written Markdown articles.

No arbitrary HTML, inline JavaScript, remote images or dependency on a browser
Markdown runtime. Raw source stays available for copy/edit.
"""
import html
import re
from urllib.parse import urlsplit

LINK_RE = re.compile(r"!?(\[([^\]\n]{1,180})\]\(([^\)\s]{1,2048})\))")
HEADER_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$")
UL_RE = re.compile(r"^[-*+]\s+(.+)$")
OL_RE = re.compile(r"^\d{1,3}[.)]\s+(.+)$")


def inline_markdown(text: str) -> str:
    """Escape first; restore only vetted HTTP(S) anchors and small text styles."""
    placeholders = []
    pieces = []
    pos = 0
    for match in LINK_RE.finditer(text):
        pieces.append(html.escape(text[pos:match.start()], quote=True))
        whole, label, url = match.group(1), match.group(2), match.group(3)
        try:
            parts = urlsplit(url)
            valid = parts.scheme in {"https", "http"} and bool(parts.netloc)
        except ValueError:
            valid = False
        if valid:
            element = (
                f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer" '
                'target="_blank">' + html.escape(
                    ("Изображение: " if match.group(0).startswith("!") else "") + label
                ) + '</a>'
            )
        else:
            element = html.escape(label, quote=True)
        token = f"@@LINK_TOKEN_{len(placeholders)}@@"
        placeholders.append((token, element))
        pieces.append(token)
        pos = match.end()
    pieces.append(html.escape(text[pos:], quote=True))
    result = "".join(pieces)
    result = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", result)
    result = re.sub(r"\*\*([^*\n]{1,1000})\*\*", r"<strong>\1</strong>", result)
    result = re.sub(r"__(?!\s)([^_\n]{1,1000})__", r"<strong>\1</strong>", result)
    result = re.sub(r"(?<!\*)\*([^*\n]{1,1000})\*(?!\*)", r"<em>\1</em>", result)
    result = re.sub(r"~~([^~\n]{1,1000})~~", r"<del>\1</del>", result)
    for token, anchor in placeholders:
        result = result.replace(token, anchor)
    return result


def render_article(source: str) -> str:
    """Render Markdown as semantic HTML, escaping untrusted content."""
    output = []
    block = []
    mode = None
    in_code = False
    code_lines = []

    def flush():
        nonlocal block, mode
        if block:
            if mode == "ul":
                output.append("<ul>" + "".join(
                    "<li>" + inline_markdown(x) + "</li>" for x in block
                ) + "</ul>")
            elif mode == "ol":
                output.append("<ol>" + "".join(
                    "<li>" + inline_markdown(x) + "</li>" for x in block
                ) + "</ol>")
            else:
                output.append("<p>" + " ".join(inline_markdown(x) for x in block) + "</p>")
        block, mode = [], None

    for raw in (source or "").replace("\r\n", "\n").split("\n")[:8000]:
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            if not in_code:
                in_code = True
                code_lines = []
            else:
                output.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
                in_code = False
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not stripped:
            flush()
            continue
        heading = HEADER_RE.match(stripped)
        if heading:
            flush()
            level = min(4, len(heading.group(1)) + 1)
            output.append(f"<h{level}>{inline_markdown(heading.group(2))}</h{level}>")
            continue
        if stripped in {"---", "***", "___"}:
            flush()
            output.append("<hr>")
            continue
        ul = UL_RE.match(stripped)
        ol = OL_RE.match(stripped)
        if ul or ol:
            new_mode = "ul" if ul else "ol"
            if mode and mode != new_mode:
                flush()
            mode = new_mode
            block.append((ul or ol).group(1))
            continue
        if stripped.startswith("> "):
            flush()
            output.append("<blockquote>" + inline_markdown(stripped[2:]) + "</blockquote>")
            continue
        if mode in {"ul", "ol"}:
            flush()
        mode = "paragraph"
        block.append(stripped)
    flush()
    if in_code:
        output.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
    return "\n".join(output) or "<p>Текст ещё не подготовлен.</p>"
