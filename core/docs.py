import html
import re
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.utils.safestring import mark_safe


DOCS_DIR = settings.BASE_DIR / "docs"
DOC_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
DOC_ORDER = {
    "getting-started": 0,
    "workflows": 10,
    "accounting": 20,
    "inventory": 30,
    "ai-copilot": 35,
    "agent-hub": 37,
    "website-editor": 38,
    "deployment": 40,
}


@dataclass(frozen=True)
class DocPage:
    slug: str
    title: str
    summary: str
    path: Path


def list_doc_pages() -> list[DocPage]:
    pages = []
    for path in sorted(DOCS_DIR.glob("*.md"), key=_sort_key):
        if path.name.startswith("."):
            continue
        pages.append(_page_from_path(path))
    return pages


def get_doc_page(slug: str) -> DocPage | None:
    if not DOC_SLUG_RE.match(slug):
        return None

    path = (DOCS_DIR / f"{slug}.md").resolve()
    docs_root = DOCS_DIR.resolve()
    if docs_root not in path.parents or not path.exists():
        return None

    return _page_from_path(path)


def render_doc_markdown(page: DocPage):
    markdown_text = page.path.read_text(encoding="utf-8")
    try:
        import markdown
    except ImportError:
        return mark_safe(_fallback_markdown(markdown_text))

    return mark_safe(markdown.markdown(
        markdown_text,
        extensions=["fenced_code", "tables", "toc"],
        output_format="html5",
    ))


def _page_from_path(path: Path) -> DocPage:
    text = path.read_text(encoding="utf-8")
    title = _extract_title(text) or path.stem.replace("-", " ").title()
    return DocPage(
        slug=path.stem,
        title=title,
        summary=_extract_summary(text),
        path=path,
    )


def _sort_key(path: Path):
    return (DOC_ORDER.get(path.stem, 999), path.stem)


def _extract_title(markdown_text: str) -> str:
    for line in markdown_text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _extract_summary(markdown_text: str) -> str:
    for line in markdown_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        if line.startswith("- "):
            continue
        return line[:220]
    return ""


def _fallback_markdown(markdown_text: str) -> str:
    """Small safe renderer for local development when python-markdown is absent."""
    lines = markdown_text.splitlines()
    html_lines = []
    in_list = False
    in_code = False
    code_lines = []

    for raw_line in lines:
        line = raw_line.rstrip()

        if line.startswith("```"):
            if in_code:
                html_lines.append("<pre><code>{}</code></pre>".format(
                    html.escape("\n".join(code_lines))
                ))
                code_lines = []
                in_code = False
            else:
                if in_list:
                    html_lines.append("</ul>")
                    in_list = False
                in_code = True
            continue

        if in_code:
            code_lines.append(line)
            continue

        stripped = line.strip()
        if not stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            continue

        if stripped.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{html.escape(stripped[2:])}</li>")
            continue

        if in_list:
            html_lines.append("</ul>")
            in_list = False

        if stripped.startswith("### "):
            html_lines.append(f"<h3>{html.escape(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            html_lines.append(f"<h2>{html.escape(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            html_lines.append(f"<h1>{html.escape(stripped[2:])}</h1>")
        else:
            html_lines.append(f"<p>{html.escape(stripped)}</p>")

    if in_code:
        html_lines.append("<pre><code>{}</code></pre>".format(
            html.escape("\n".join(code_lines))
        ))
    if in_list:
        html_lines.append("</ul>")

    return "\n".join(html_lines)
