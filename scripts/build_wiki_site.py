"""Static wiki site generator for sempropos (renders openwiki/*.md into site/).

Produces a modern, responsive, self-contained static documentation site with:
  - Light/dark theme toggle with crisp indigo/cyan/emerald palette
  - Collapsible grouped sidebar + sticky scroll-spy Table of Contents
  - Instant client-side Cmd+K / '/' search across all pages and headings
  - Pygments syntax highlighting + one-click Copy buttons
  - Theme-aware Mermaid diagram rendering
  - CNAME for sempropos.dearanayji.rocks
"""

from __future__ import annotations

import html
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

ROOT = Path(__file__).resolve().parent.parent
WIKI_DIR = ROOT / "openwiki"
DEFAULT_OUT = ROOT / "site"
CUSTOM_DOMAIN = "sempropos.dearanayji.rocks"

# Grouped navigation order for the sidebar
NAV_GROUPS: list[tuple[str, list[str]]] = [
    ("Getting Started", ["index.md", "quickstart.md"]),
    (
        "Core Architecture",
        ["architecture.md", "indexing.md", "index-schema.md", "retrieval.md"],
    ),
    ("Intelligence & Runtime", ["synthesis.md", "daemon.md", "configuration.md"]),
    (
        "Usage & Operations",
        ["cli.md", "shell-integration.md", "operations.md", "development.md"],
    ),
]

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_HEADING_RE = re.compile(r"<h([23])>(.*?)</h\1>", re.DOTALL)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_MD_LINK_RE = re.compile(r'href="([a-zA-Z0-9_-]+)\.md(#[^"]*)?"')


@dataclass
class WikiPage:
    filename: str
    slug: str
    title: str
    description: str
    page_type: str
    tags: list[str]
    body_md: str
    body_html: str
    toc: list[tuple[int, str, str]]  # (level, id, text)
    plain_text: str


def _parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw

    fm_text = match.group(1)
    body = raw[match.end() :]
    meta: dict[str, Any] = {}
    for line in fm_text.splitlines():
        if ":" not in line or line.startswith((" ", "\t", "-")):
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip()
        if key in ("title", "description", "type"):
            meta[key] = val.strip('"').strip("'")
        elif key == "tags" and val.startswith("[") and val.endswith("]"):
            meta["tags"] = [t.strip().strip('"').strip("'") for t in val[1:-1].split(",") if t.strip()]
    return meta, body


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9\s-]", "", text).strip().lower()
    return re.sub(r"[\s-]+", "-", cleaned) or "section"


def _highlight_code(code: str, lang: str, _attrs: str) -> str:
    lang_clean = (lang or "").strip().lower()
    if lang_clean == "mermaid":
        return f'<div class="mermaid-wrap"><pre class="mermaid">{html.escape(code)}</pre></div>'

    try:
        lexer = get_lexer_by_name(lang_clean) if lang_clean else TextLexer()
    except ClassNotFound:
        lexer = TextLexer()

    formatter = HtmlFormatter(nowrap=True)
    highlighted = highlight(code, lexer, formatter)
    badge = f'<span class="code-lang">{html.escape(lang_clean or "text")}</span>'
    return (
        f'<div class="code-block">'
        f'<div class="code-header">{badge}'
        f'<button class="copy-btn" type="button" aria-label="Copy code">Copy</button>'
        f"</div>"
        f'<pre class="highlight"><code>{highlighted}</code></pre>'
        f"</div>"
    )


def _add_heading_anchors(rendered_html: str) -> tuple[str, list[tuple[int, str, str]]]:
    toc: list[tuple[int, str, str]] = []
    seen_ids: dict[str, int] = {}

    def repl(match: re.Match[str]) -> str:
        level = int(match.group(1))
        inner = match.group(2)
        plain = html.unescape(_TAG_STRIP_RE.sub("", inner)).strip()
        base_id = _slugify(plain)
        count = seen_ids.get(base_id, 0)
        seen_ids[base_id] = count + 1
        hid = base_id if count == 0 else f"{base_id}-{count}"
        toc.append((level, hid, plain))
        return (
            f'<h{level} id="{hid}">{inner}'
            f'<a class="heading-anchor" href="#{hid}" aria-label="Link to {html.escape(plain)}">#</a>'
            f"</h{level}>"
        )

    updated = _HEADING_RE.sub(repl, rendered_html)
    return updated, toc


def _rewrite_wiki_links(rendered_html: str) -> str:
    def repl(match: re.Match[str]) -> str:
        stem = match.group(1)
        frag = match.group(2) or ""
        target = "index.html" if stem == "index" else f"{stem}.html"
        return f'href="{target}{frag}"'

    return _MD_LINK_RE.sub(repl, rendered_html)


def _load_pages() -> list[WikiPage]:
    md = MarkdownIt("commonmark", {"html": False, "linkify": True, "typographer": True, "highlight": _highlight_code})
    md.enable(["table", "strikethrough"])

    ordered_files = [fn for _grp, files in NAV_GROUPS for fn in files]
    # Include any extra markdown files not explicitly listed
    for p in sorted(WIKI_DIR.glob("*.md")):
        if p.name not in ordered_files:
            ordered_files.append(p.name)

    pages: list[WikiPage] = []
    for filename in ordered_files:
        path = WIKI_DIR / filename
        if not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw)

        # Extract H1 title if frontmatter omitted it
        title = str(meta.get("title") or "")
        if not title:
            for line in body.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
        title = title or filename.removesuffix(".md").replace("-", " ").title()

        rendered = md.render(body)
        rendered = _rewrite_wiki_links(rendered)
        rendered, toc = _add_heading_anchors(rendered)
        plain_text = " ".join(html.unescape(_TAG_STRIP_RE.sub(" ", rendered)).split())

        slug = "index" if filename == "index.md" else filename.removesuffix(".md")
        pages.append(
            WikiPage(
                filename=filename,
                slug=slug,
                title=title,
                description=str(meta.get("description") or ""),
                page_type=str(meta.get("type") or "Docs"),
                tags=list(meta.get("tags") or []),
                body_md=body,
                body_html=rendered,
                toc=toc,
                plain_text=plain_text,
            )
        )
    return pages


_HOME_HERO_HTML = """
<section class="hero-card">
  <div class="hero-badge">v1.0.0 · Offline-First · Python 3.11+ · MIT</div>
  <h1 class="hero-title">Instant CLI mastery,<br/>without leaving your terminal.</h1>
  <p class="hero-sub">
    <strong>sem</strong> replaces <code>apropos</code>, <code>--help</code>, and <code>man</code> with a canonical
    structured <code>ToolSpec</code> index, Porter-stemmed BM25 + semantic retrieval,
    safe local GGUF / Ollama synthesis, and an auto-managed warm daemon.
  </p>
  <div class="hero-actions">
    <a class="btn-primary" href="quickstart.html">Get Started →</a>
    <a class="btn-secondary" href="architecture.html">Explore Architecture</a>
    <a class="btn-ghost" href="https://pypi.org/project/sempropos/" target="_blank" rel="noopener">PyPI ↗</a>
  </div>
  <div class="hero-terminal">
    <div class="term-bar">
      <span class="dot red"></span><span class="dot yellow"></span><span class="dot green"></span>
      <span class="term-title">zsh — sem v1.0.0</span>
    </div>
    <pre class="term-body"><span class="t-prompt">$</span> sem -e "delete files older than 30 days"
<span class="t-ok">╭─ Command (DESTRUCTIVE) ────────────── local_gguf · 118ms ─╮</span>
<span class="t-cmd">│ find . -type f -mtime +30 -delete                          │</span>
<span class="t-ok">╰────────────────────────────────────────────────────────────╯</span>
<span class="t-warn">⚠ Safety Notice: `find -delete` permanently removes matched files.</span>
<span class="t-flag">  -type  </span> File is of type: f (regular file)
<span class="t-flag">  -mtime </span> File's data was last modified n*24 hours ago
<span class="t-flag">  -delete</span> Delete files; true if removal succeeded

<span class="t-prompt">$</span> sem show du --flags depth
<span class="t-flag">  -d, --max-depth &lt;N&gt;</span>  print total for a directory only if N levels below</pre>
  </div>
  <div class="feature-grid">
    <div class="feature-card">
      <div class="f-tag">Ask / Ctrl+S</div>
      <h3>Grounded Synthesis</h3>
      <p>JSON-schema constrained local GGUF (<code>0.5B–3B</code>), pure-HTTP Ollama, or zero-LLM Tier-0 fallback with safety warnings.</p>
    </div>
    <div class="feature-card">
      <div class="f-tag">Show</div>
      <h3>Instant Reference Cards</h3>
      <p>0ms-LLM structured cheat sheets merging <code>man(7)</code>/<code>mdoc(7)</code>, <code>tldr</code>, completions, and safe <code>--help</code> probes.</p>
    </div>
    <div class="feature-card">
      <div class="f-tag">Search</div>
      <h3>Smart Tool Discovery</h3>
      <p>Weighted multi-column Porter FTS5, extension hints (<code>.7z</code>, <code>.json</code>), and compound intent ranking.</p>
    </div>
  </div>
</section>
"""


_STYLES_CSS = """
:root {
  --bg: #090d16;
  --bg-elev: #0f1624;
  --bg-card: #131c2e;
  --bg-code: #0c1220;
  --border: rgba(148, 163, 184, 0.14);
  --border-strong: rgba(148, 163, 184, 0.28);
  --text: #e2e8f0;
  --text-strong: #f8fafc;
  --text-muted: #94a3b8;
  --accent: #38bdf8;
  --accent-2: #818cf8;
  --accent-bg: rgba(56, 189, 248, 0.1);
  --emerald: #34d399;
  --amber: #fbbf24;
  --rose: #fb7185;
  --shadow: 0 20px 50px rgba(0, 0, 0, 0.45);
  --header-h: 64px;
  --sidebar-w: 272px;
  --toc-w: 230px;
}

html[data-theme="light"] {
  --bg: #f8fafc;
  --bg-elev: #ffffff;
  --bg-card: #ffffff;
  --bg-code: #0f172a;
  --border: rgba(15, 23, 42, 0.1);
  --border-strong: rgba(15, 23, 42, 0.22);
  --text: #334155;
  --text-strong: #0f172a;
  --text-muted: #64748b;
  --accent: #0284c7;
  --accent-2: #4f46e5;
  --accent-bg: rgba(2, 132, 199, 0.08);
  --shadow: 0 12px 35px rgba(15, 23, 42, 0.08);
}

* { box-sizing: border-box; }
html { scroll-behavior: smooth; scroll-padding-top: calc(var(--header-h) + 24px); }
body {
  margin: 0;
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.68;
  -webkit-font-smoothing: antialiased;
}
code, pre {
  font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

/* Header */
.topbar {
  position: sticky; top: 0; z-index: 40;
  height: var(--header-h);
  backdrop-filter: blur(14px);
  background: color-mix(in srgb, var(--bg) 82%, transparent);
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 1.25rem; gap: 1rem;
}
.brand { display: flex; align-items: center; gap: 0.7rem; color: var(--text-strong); font-weight: 700; font-size: 1.05rem; }
.brand:hover { text-decoration: none; }
.brand-logo {
  width: 32px; height: 32px; border-radius: 8px;
  background: linear-gradient(135deg, var(--accent-2), var(--accent));
  display: grid; place-items: center; color: #fff; font-weight: 800; font-size: 0.9rem;
  box-shadow: 0 4px 16px rgba(56, 189, 248, 0.3);
}
.ver-pill {
  font-size: 0.72rem; font-weight: 600; padding: 0.15rem 0.5rem;
  border-radius: 999px; background: var(--accent-bg); color: var(--accent);
  border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
}
.top-right { display: flex; align-items: center; gap: 0.65rem; }
.search-trigger {
  display: flex; align-items: center; gap: 0.65rem;
  background: var(--bg-elev); border: 1px solid var(--border);
  color: var(--text-muted); border-radius: 9px; padding: 0.4rem 0.75rem;
  cursor: pointer; font-size: 0.86rem; min-width: 210px; justify-content: space-between;
  transition: border-color 0.15s;
}
.search-trigger:hover { border-color: var(--accent); color: var(--text-strong); }
.kbd {
  font-size: 0.72rem; padding: 0.1rem 0.4rem; border-radius: 5px;
  border: 1px solid var(--border-strong); color: var(--text-muted);
}
.icon-btn {
  background: var(--bg-elev); border: 1px solid var(--border);
  color: var(--text); border-radius: 8px; padding: 0.42rem 0.65rem;
  cursor: pointer; font-size: 0.85rem; display: inline-flex; align-items: center; gap: 0.35rem;
}
.icon-btn:hover { border-color: var(--accent); color: var(--accent); text-decoration: none; }
.menu-btn { display: none; }

/* Layout */
.layout {
  max-width: 1440px; margin: 0 auto;
  display: grid; grid-template-columns: var(--sidebar-w) minmax(0, 1fr) var(--toc-w);
  gap: 2rem; padding: 1.5rem 1.25rem 4rem;
}
.sidebar {
  position: sticky; top: calc(var(--header-h) + 1.25rem);
  height: calc(100vh - var(--header-h) - 2.5rem);
  overflow-y: auto; padding-right: 0.5rem;
}
.nav-group { margin-bottom: 1.35rem; }
.nav-group-title {
  font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--text-muted); font-weight: 700; margin: 0 0 0.45rem 0.65rem;
}
.nav-link {
  display: block; padding: 0.38rem 0.65rem; border-radius: 7px;
  font-size: 0.89rem; color: var(--text); margin-bottom: 2px;
  transition: background 0.12s, color 0.12s;
}
.nav-link:hover { background: var(--accent-bg); color: var(--text-strong); text-decoration: none; }
.nav-link.active {
  background: var(--accent-bg); color: var(--accent); font-weight: 600;
  border-left: 2px solid var(--accent);
}

/* Main Content */
.content { min-width: 0; max-width: 820px; }
.page-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; margin-bottom: 0.75rem; }
.type-pill {
  font-size: 0.73rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em;
  padding: 0.16rem 0.55rem; border-radius: 6px;
  background: color-mix(in srgb, var(--accent-2) 15%, transparent); color: var(--accent-2);
}
.tag-pill {
  font-size: 0.74rem; padding: 0.12rem 0.48rem; border-radius: 999px;
  border: 1px solid var(--border); color: var(--text-muted);
}
.prose h1 { font-size: 2.05rem; line-height: 1.2; color: var(--text-strong); margin: 0.25rem 0 1rem; letter-spacing: -0.02em; }
.prose h2 {
  font-size: 1.38rem; color: var(--text-strong); margin: 2.1rem 0 0.75rem;
  padding-bottom: 0.4rem; border-bottom: 1px solid var(--border); letter-spacing: -0.01em;
}
.prose h3 { font-size: 1.1rem; color: var(--text-strong); margin: 1.5rem 0 0.5rem; }
.heading-anchor { opacity: 0; margin-left: 0.45rem; color: var(--accent); font-weight: 500; transition: opacity 0.12s; }
.prose h2:hover .heading-anchor, .prose h3:hover .heading-anchor { opacity: 1; text-decoration: none; }
.prose p { margin: 0.85rem 0; }
.prose ul, .prose ol { padding-left: 1.35rem; margin: 0.75rem 0; }
.prose li { margin: 0.35rem 0; }
.prose strong { color: var(--text-strong); }
.prose :not(pre) > code {
  background: var(--accent-bg); color: var(--accent);
  padding: 0.12rem 0.38rem; border-radius: 5px; font-size: 0.86em;
}
.prose blockquote {
  margin: 1rem 0; padding: 0.75rem 1rem; border-left: 3px solid var(--accent);
  background: var(--bg-elev); border-radius: 0 8px 8px 0; color: var(--text-muted);
}
.prose table {
  width: 100%; border-collapse: collapse; margin: 1.25rem 0;
  font-size: 0.9rem; border: 1px solid var(--border); border-radius: 10px; overflow: hidden;
}
.prose th {
  text-align: left; background: var(--bg-elev); color: var(--text-strong);
  padding: 0.65rem 0.85rem; border-bottom: 1px solid var(--border-strong); font-weight: 600;
}
.prose td { padding: 0.6rem 0.85rem; border-bottom: 1px solid var(--border); vertical-align: top; }
.prose tr:last-child td { border-bottom: none; }

/* Code Blocks */
.code-block {
  margin: 1.1rem 0; border-radius: 10px; overflow: hidden;
  border: 1px solid var(--border); background: var(--bg-code); box-shadow: 0 6px 20px rgba(0,0,0,0.18);
}
.code-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 0.38rem 0.85rem; background: rgba(255,255,255,0.03);
  border-bottom: 1px solid rgba(255,255,255,0.08); font-size: 0.75rem; color: #94a3b8;
}
.copy-btn {
  background: rgba(255,255,255,0.07); color: #cbd5e1; border: 1px solid rgba(255,255,255,0.12);
  border-radius: 5px; padding: 0.15rem 0.55rem; font-size: 0.72rem; cursor: pointer;
}
.copy-btn:hover { background: var(--accent); color: #090d16; border-color: var(--accent); }
.code-block pre { margin: 0; padding: 0.95rem 1rem; overflow-x: auto; font-size: 0.86rem; line-height: 1.55; color: #e2e8f0; }

/* Mermaid */
.mermaid-wrap {
  margin: 1.25rem 0; padding: 1rem; border-radius: 10px;
  background: var(--bg-elev); border: 1px solid var(--border); overflow-x: auto; text-align: center;
}

/* Hero Landing */
.hero-card {
  margin-bottom: 2rem; padding: 2rem; border-radius: 16px;
  background: radial-gradient(circle at top right, color-mix(in srgb, var(--accent-2) 16%, transparent), transparent 55%),
              linear-gradient(180deg, var(--bg-elev), var(--bg-card));
  border: 1px solid var(--border); box-shadow: var(--shadow);
}
.hero-badge {
  display: inline-block; font-size: 0.76rem; font-weight: 600; color: var(--accent);
  background: var(--accent-bg); padding: 0.25rem 0.7rem; border-radius: 999px; margin-bottom: 0.85rem;
}
.hero-title { font-size: 2.35rem; line-height: 1.15; margin: 0 0 0.85rem; color: var(--text-strong); letter-spacing: -0.03em; }
.hero-sub { font-size: 1.03rem; color: var(--text); max-width: 64ch; margin: 0 0 1.3rem; }
.hero-actions { display: flex; flex-wrap: wrap; gap: 0.75rem; margin-bottom: 1.5rem; }
.btn-primary {
  background: linear-gradient(135deg, var(--accent-2), var(--accent));
  color: #fff; font-weight: 600; padding: 0.58rem 1.15rem; border-radius: 9px;
}
.btn-primary:hover { opacity: 0.92; text-decoration: none; }
.btn-secondary, .btn-ghost {
  padding: 0.58rem 1.05rem; border-radius: 9px; font-weight: 600;
  border: 1px solid var(--border-strong); color: var(--text-strong); background: var(--bg-elev);
}
.btn-secondary:hover, .btn-ghost:hover { border-color: var(--accent); text-decoration: none; }
.hero-terminal {
  border-radius: 12px; overflow: hidden; border: 1px solid rgba(255,255,255,0.12);
  background: #0b101b; margin-bottom: 1.5rem;
}
.term-bar {
  display: flex; align-items: center; gap: 0.45rem; padding: 0.5rem 0.85rem;
  background: rgba(255,255,255,0.04); border-bottom: 1px solid rgba(255,255,255,0.08);
}
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.dot.red { background: #f87171; } .dot.yellow { background: #fbbf24; } .dot.green { background: #34d399; }
.term-title { margin-left: 0.4rem; font-size: 0.75rem; color: #94a3b8; }
.term-body { margin: 0; padding: 1rem; font-size: 0.82rem; line-height: 1.5; color: #cbd5e1; overflow-x: auto; }
.t-prompt { color: #38bdf8; font-weight: 700; }
.t-ok { color: #fb7185; }
.t-cmd { color: #f8fafc; font-weight: 700; }
.t-warn { color: #fbbf24; }
.t-flag { color: #34d399; font-weight: 600; }
.feature-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0.9rem; }
.feature-card {
  padding: 1rem; border-radius: 10px; background: var(--bg-elev); border: 1px solid var(--border);
}
.feature-card h3 { margin: 0.4rem 0 0.35rem; font-size: 0.98rem; color: var(--text-strong); }
.feature-card p { margin: 0; font-size: 0.85rem; color: var(--text-muted); }
.f-tag { font-size: 0.72rem; font-weight: 700; color: var(--accent); text-transform: uppercase; letter-spacing: 0.05em; }

/* TOC */
.toc {
  position: sticky; top: calc(var(--header-h) + 1.25rem);
  height: calc(100vh - var(--header-h) - 2.5rem); overflow-y: auto;
  font-size: 0.83rem;
}
.toc-title { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); font-weight: 700; margin-bottom: 0.5rem; }
.toc a { display: block; padding: 0.25rem 0 0.25rem 0.65rem; color: var(--text-muted); border-left: 2px solid var(--border); }
.toc a.lvl-3 { padding-left: 1.25rem; font-size: 0.79rem; }
.toc a:hover, .toc a.active { color: var(--accent); border-left-color: var(--accent); text-decoration: none; }

/* Prev / Next */
.pager {
  display: grid; grid-template-columns: 1fr 1fr; gap: 1rem;
  margin-top: 3rem; padding-top: 1.5rem; border-top: 1px solid var(--border);
}
.pager-card {
  padding: 0.85rem 1rem; border-radius: 10px; border: 1px solid var(--border);
  background: var(--bg-elev); display: flex; flex-direction: column;
}
.pager-card:hover { border-color: var(--accent); text-decoration: none; }
.pager-card.next { text-align: right; }
.pager-dir { font-size: 0.75rem; color: var(--text-muted); }
.pager-title { font-weight: 600; color: var(--text-strong); }

/* Search Modal */
.modal-backdrop {
  position: fixed; inset: 0; z-index: 80; background: rgba(2, 6, 23, 0.72);
  backdrop-filter: blur(4px); display: none; align-items: flex-start; justify-content: center; padding-top: 10vh;
}
.modal-backdrop.open { display: flex; }
.modal {
  width: min(640px, 92vw); background: var(--bg-elev); border: 1px solid var(--border-strong);
  border-radius: 14px; box-shadow: var(--shadow); overflow: hidden;
}
.modal-input-row { display: flex; align-items: center; padding: 0.85rem 1rem; border-bottom: 1px solid var(--border); gap: 0.65rem; }
.modal-input {
  flex: 1; background: transparent; border: none; outline: none;
  color: var(--text-strong); font-size: 1rem;
}
.modal-results { max-height: 55vh; overflow-y: auto; padding: 0.5rem; }
.search-item {
  display: block; padding: 0.65rem 0.85rem; border-radius: 8px; color: var(--text);
}
.search-item:hover, .search-item.selected { background: var(--accent-bg); text-decoration: none; }
.search-item-title { font-weight: 600; color: var(--text-strong); }
.search-item-desc { font-size: 0.82rem; color: var(--text-muted); }

/* Pygments dark/light friendly overrides */
.highlight .c, .highlight .c1, .highlight .ch, .highlight .cm { color: #64748b; font-style: italic; }
.highlight .k, .highlight .kd, .highlight .kn, .highlight .kp, .highlight .kr, .highlight .kt { color: #c084fc; }
.highlight .s, .highlight .s1, .highlight .s2, .highlight .sb, .highlight .sc, .highlight .sd, .highlight .si { color: #34d399; }
.highlight .na, .highlight .nb, .highlight .nc, .highlight .nf { color: #38bdf8; }
.highlight .mi, .highlight .mf, .highlight .mh { color: #fbbf24; }
.highlight .o, .highlight .p { color: #94a3b8; }

@media (max-width: 1120px) {
  .layout { grid-template-columns: var(--sidebar-w) minmax(0, 1fr); }
  .toc { display: none; }
}
@media (max-width: 820px) {
  .layout { grid-template-columns: 1fr; }
  .menu-btn { display: inline-flex; }
  .search-trigger { min-width: 120px; }
  .feature-grid { grid-template-columns: 1fr; }
  .sidebar {
    position: fixed; left: 0; top: var(--header-h); bottom: 0; width: 280px;
    background: var(--bg-elev); border-right: 1px solid var(--border);
    padding: 1rem; z-index: 60; transform: translateX(-105%); transition: transform 0.2s;
  }
  .sidebar.open { transform: translateX(0); }
}
"""

_APP_JS = """
(() => {
  const root = document.documentElement;
  const themeBtn = document.getElementById('theme-toggle');
  const saved = localStorage.getItem('sem-wiki-theme');
  const initial = saved || (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
  root.setAttribute('data-theme', initial);

  function updateThemeLabel(t) {
    if (themeBtn) themeBtn.textContent = t === 'dark' ? '☀ Light' : '🌙 Dark';
  }
  updateThemeLabel(initial);

  if (themeBtn) {
    themeBtn.addEventListener('click', () => {
      const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      localStorage.setItem('sem-wiki-theme', next);
      updateThemeLabel(next);
    });
  }

  // Mobile sidebar drawer
  const menuBtn = document.getElementById('menu-toggle');
  const sidebar = document.getElementById('sidebar');
  if (menuBtn && sidebar) {
    menuBtn.addEventListener('click', () => sidebar.classList.toggle('open'));
  }

  // Copy code buttons
  document.querySelectorAll('.copy-btn').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const pre = btn.closest('.code-block')?.querySelector('pre');
      if (!pre) return;
      try {
        await navigator.clipboard.writeText(pre.innerText);
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = 'Copy'; }, 1400);
      } catch {}
    });
  });

  // Search modal (Cmd+K / Ctrl+K / '/')
  const backdrop = document.getElementById('search-modal');
  const input = document.getElementById('search-input');
  const resultsEl = document.getElementById('search-results');
  const openBtn = document.getElementById('search-open');
  let searchData = null;

  async function ensureIndex() {
    if (searchData) return searchData;
    const res = await fetch('search-index.json');
    searchData = await res.json();
    return searchData;
  }

  function renderSearch(q) {
    if (!searchData || !resultsEl) return;
    const terms = q.trim().toLowerCase().split(/\\s+/).filter(Boolean);
    const matches = !terms.length ? searchData.slice(0, 8) : searchData.filter((item) => {
      const hay = (item.title + ' ' + item.description + ' ' + item.tags.join(' ') + ' ' + item.text).toLowerCase();
      return terms.every((t) => hay.includes(t));
    }).slice(0, 10);

    if (!matches.length) {
      resultsEl.innerHTML = '<div style="padding:1rem;color:var(--text-muted)">No matching documentation found.</div>';
      return;
    }
    resultsEl.innerHTML = matches.map((m) => (
      `<a class="search-item" href="${m.href}">
        <div class="search-item-title">${m.title}</div>
        <div class="search-item-desc">${m.description || m.tags.join(' · ')}</div>
      </a>`
    )).join('');
  }

  async function openSearch() {
    if (!backdrop || !input) return;
    backdrop.classList.add('open');
    input.focus();
    await ensureIndex();
    renderSearch(input.value);
  }
  function closeSearch() {
    if (backdrop) backdrop.classList.remove('open');
  }
  if (openBtn) openBtn.addEventListener('click', openSearch);
  if (backdrop) backdrop.addEventListener('click', (e) => { if (e.target === backdrop) closeSearch(); });
  if (input) input.addEventListener('input', () => renderSearch(input.value));

  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      openSearch();
    } else if (e.key === '/' && document.activeElement?.tagName !== 'INPUT') {
      e.preventDefault();
      openSearch();
    } else if (e.key === 'Escape') {
      closeSearch();
    }
  });

  // Scroll-spy for TOC
  const tocLinks = Array.from(document.querySelectorAll('.toc a'));
  if (tocLinks.length) {
    const headings = tocLinks.map((a) => document.getElementById(a.getAttribute('href').slice(1))).filter(Boolean);
    window.addEventListener('scroll', () => {
      let current = headings[0]?.id;
      for (const h of headings) {
        if (h.getBoundingClientRect().top <= 110) current = h.id;
      }
      tocLinks.forEach((a) => a.classList.toggle('active', a.getAttribute('href') === '#' + current));
    }, { passive: true });
  }
})();
"""


def _render_page_html(
    page: WikiPage,
    all_pages: list[WikiPage],
    prev_page: WikiPage | None,
    next_page: WikiPage | None,
) -> str:
    page_by_file = {p.filename: p for p in all_pages}
    nav_html_parts: list[str] = []
    for group_title, files in NAV_GROUPS:
        links = []
        for fn in files:
            p = page_by_file.get(fn)
            if p is None:
                continue
            href = "index.html" if p.slug == "index" else f"{p.slug}.html"
            active = " active" if p.filename == page.filename else ""
            links.append(f'<a class="nav-link{active}" href="{href}">{html.escape(p.title)}</a>')
        if links:
            nav_html_parts.append(
                f'<div class="nav-group"><div class="nav-group-title">{html.escape(group_title)}</div>'
                + "".join(links)
                + "</div>"
            )

    toc_html = "".join(
        f'<a class="lvl-{lvl}" href="#{hid}">{html.escape(text)}</a>'
        for lvl, hid, text in page.toc
    )
    toc_block = (
        f'<aside class="toc"><div class="toc-title">On this page</div>{toc_html}</aside>'
        if toc_html
        else '<aside class="toc"></aside>'
    )

    tags_html = "".join(f'<span class="tag-pill">{html.escape(t)}</span>' for t in page.tags[:6])
    meta_html = (
        f'<div class="page-meta"><span class="type-pill">{html.escape(page.page_type)}</span>{tags_html}</div>'
        if page.slug != "index"
        else ""
    )

    hero_block = _HOME_HERO_HTML if page.slug == "index" else ""

    prev_html = (
        f'<a class="pager-card" href="{"index.html" if prev_page.slug == "index" else f"{prev_page.slug}.html"}">'
        f'<span class="pager-dir">← Previous</span>'
        f'<span class="pager-title">{html.escape(prev_page.title)}</span></a>'
        if prev_page
        else "<div></div>"
    )
    next_html = (
        f'<a class="pager-card next" href="{"index.html" if next_page.slug == "index" else f"{next_page.slug}.html"}">'
        f'<span class="pager-dir">Next →</span>'
        f'<span class="pager-title">{html.escape(next_page.title)}</span></a>'
        if next_page
        else "<div></div>"
    )

    has_mermaid = 'class="mermaid"' in page.body_html
    mermaid_script = (
        '<script type="module">'
        "import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';"
        "const theme = document.documentElement.getAttribute('data-theme') === 'light' ? 'default' : 'dark';"
        "mermaid.initialize({ startOnLoad: true, theme });"
        "</script>"
        if has_mermaid
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(page.title)} — sempropos Wiki</title>
  <meta name="description" content="{html.escape(page.description or 'Offline CLI replacement for apropos, --help, and man pages.')}" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet" />
  <link rel="stylesheet" href="assets/style.css" />
</head>
<body>
  <header class="topbar">
    <div style="display:flex;align-items:center;gap:0.6rem;">
      <button id="menu-toggle" class="icon-btn menu-btn" type="button" aria-label="Toggle menu">☰</button>
      <a class="brand" href="index.html">
        <span class="brand-logo">&gt;_</span>
        <span>sempropos</span>
      </a>
      <span class="ver-pill">v1.0.0</span>
    </div>
    <div class="top-right">
      <button id="search-open" class="search-trigger" type="button">
        <span>Search wiki…</span>
        <span class="kbd">⌘K</span>
      </button>
      <button id="theme-toggle" class="icon-btn" type="button">☀ Light</button>
      <a class="icon-btn" href="https://github.com/ADPer0705/sempropos" target="_blank" rel="noopener">GitHub</a>
    </div>
  </header>

  <div class="layout">
    <aside id="sidebar" class="sidebar">{''.join(nav_html_parts)}</aside>
    <main class="content">
      {hero_block}
      <article class="prose">
        {meta_html}
        {page.body_html}
      </article>
      <nav class="pager">{prev_html}{next_html}</nav>
    </main>
    {toc_block}
  </div>

  <div id="search-modal" class="modal-backdrop" role="dialog" aria-modal="true" aria-label="Search wiki">
    <div class="modal">
      <div class="modal-input-row">
        <span style="color:var(--accent)">⌕</span>
        <input id="search-input" class="modal-input" type="search" placeholder="Search commands, architecture, config, retrieval…" autocomplete="off" />
        <span class="kbd">Esc</span>
      </div>
      <div id="search-results" class="modal-results"></div>
    </div>
  </div>

  <script src="assets/app.js"></script>
  {mermaid_script}
</body>
</html>
"""


def build_site(out_dir: Path = DEFAULT_OUT) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    (assets_dir / "style.css").write_text(_STYLES_CSS.strip() + "\n", encoding="utf-8")
    (assets_dir / "app.js").write_text(_APP_JS.strip() + "\n", encoding="utf-8")
    (out_dir / "CNAME").write_text(f"{CUSTOM_DOMAIN}\n", encoding="utf-8")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    pages = _load_pages()
    search_index: list[dict[str, Any]] = []

    for idx, page in enumerate(pages):
        prev_page = pages[idx - 1] if idx > 0 else None
        next_page = pages[idx + 1] if idx + 1 < len(pages) else None
        out_name = "index.html" if page.slug == "index" else f"{page.slug}.html"
        (out_dir / out_name).write_text(
            _render_page_html(page, pages, prev_page, next_page),
            encoding="utf-8",
        )
        search_index.append(
            {
                "title": page.title,
                "href": out_name,
                "description": page.description,
                "tags": page.tags,
                "text": page.plain_text[:4000],
            }
        )

    (out_dir / "search-index.json").write_text(
        json.dumps(search_index, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Built {len(pages)} wiki pages into {out_dir}")


if __name__ == "__main__":
    build_site()
