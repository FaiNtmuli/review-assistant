#!/usr/bin/env python3
"""把 markdown 文档渲染成单文件 HTML，方便直接用浏览器看。

用法：
    python scripts/render_doc.py                  # 渲染 docs 下所有 .md
    python scripts/render_doc.py docs/xxx.md      # 只渲染指定文件

生成的 HTML 不依赖任何外部资源，样式和目录都内嵌，双击即可打开。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
HEADING_RE = re.compile(r"<h([23])>(.*?)</h\1>", re.DOTALL)
PRE_RE = re.compile(r"<pre><code(?:\s+class=\"[^\"]*\")?>(.*?)</code></pre>", re.DOTALL)
INLINE_RE = re.compile(r"<code>(.*?)</code>", re.DOTALL)

# 调用链的标记按类型着色，颜色含义与文档开头的标签约定一致
SIDE_EFFECTS = {"网": "net", "印": "log", "进": "proc", "文": "file", "存": "db"}
TAG_RE = re.compile(r"\[(网|印|进|文|存)\]")
ARROW_RE = re.compile("⟶")
RW_RE = re.compile(r"(?<![\w])([RW]):")
KEYWORDS = (
    "def", "return", "if", "elif", "else", "for", "while", "try", "except",
    "finally", "raise", "continue", "break", "import", "lambda", "yield",
    "pass", "self", "None", "True", "False",
)
KEYWORD_RE = re.compile(r"(?<![\w.])(?:" + "|".join(KEYWORDS) + r")(?![\w])")

STYLE = """
:root {
  --bg: #ffffff;
  --fg: #1f2328;
  --muted: #57606a;
  --line: #d8dee4;
  --code-bg: #f6f8fa;
  --accent: #0969da;
  --kw: #57606a;
  --cmt: #8b949e;
  --ret: #8250df;
  --rw-r: #0969da;
  --rw-w: #cf222e;
  --sf-net: #0969da;   --sf-net-bg: #ddf4ff;
  --sf-log: #8250df;   --sf-log-bg: #f3ebff;
  --sf-proc: #bc4c00;  --sf-proc-bg: #fff1e5;
  --sf-file: #1a7f37;  --sf-file-bg: #dafbe1;
  --sf-db: #0d7d8f;    --sf-db-bg: #d6f4f8;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14161a;
    --fg: #e6e8eb;
    --muted: #9aa4b2;
    --line: #2b2f36;
    --code-bg: #1c1f24;
    --accent: #6cb6ff;
    --kw: #a5b0bd;
    --cmt: #7d8590;
    --ret: #d2a8ff;
    --rw-r: #79c0ff;
    --rw-w: #ff7b72;
    --sf-net: #79c0ff;   --sf-net-bg: rgba(56, 139, 253, 0.18);
    --sf-log: #d2a8ff;   --sf-log-bg: rgba(163, 113, 247, 0.18);
    --sf-proc: #ffa657;  --sf-proc-bg: rgba(219, 109, 40, 0.18);
    --sf-file: #7ee787;  --sf-file-bg: rgba(63, 185, 80, 0.18);
    --sf-db: #76e3ea;    --sf-db-bg: rgba(57, 197, 207, 0.18);
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--fg);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei",
               "PingFang SC", "Hiragino Sans GB", sans-serif;
  font-size: 15px;
  line-height: 1.75;
  -webkit-font-smoothing: antialiased;
}
.wrap {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 28px;
  max-width: 1320px;
  margin: 0 auto;
  padding: 40px 28px 120px;
}
@media (min-width: 1120px) {
  .wrap { grid-template-columns: 250px minmax(0, 1fr); gap: 40px; }
  .toc {
    position: sticky;
    top: 40px;
    align-self: start;
    max-height: calc(100vh - 80px);
    overflow-y: auto;
  }
}
main { min-width: 0; }
h1, h2, h3 { line-height: 1.35; font-weight: 600; }
h1 { font-size: 26px; margin: 0 0 8px; }
h2 {
  font-size: 20px;
  margin: 44px 0 14px;
  padding-top: 18px;
  border-top: 1px solid var(--line);
}
h3 { font-size: 16px; margin: 28px 0 10px; }
p { margin: 10px 0; }
ul, ol { margin: 10px 0; padding-left: 26px; }
li { margin: 4px 0; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
code {
  font-family: ui-monospace, "Cascadia Mono", Consolas, "SFMono-Regular",
               "Liberation Mono", Menlo, monospace;
  font-size: 0.9em;
  background: var(--code-bg);
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 1px 5px;
}
pre {
  background: var(--code-bg);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 16px 18px;
  overflow-x: auto;
  margin: 16px 0;
}
pre code {
  background: none;
  border: 0;
  padding: 0;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre;
  tab-size: 4;
}
.kw { color: var(--kw); font-weight: 500; }
.c { color: var(--cmt); }
.ret { color: var(--ret); font-weight: 600; }
.rw-r { color: var(--rw-r); font-weight: 600; }
.rw-w { color: var(--rw-w); font-weight: 600; }
.tag { border-radius: 4px; padding: 0 4px; font-weight: 600; }
.tag.net { color: var(--sf-net); background: var(--sf-net-bg); }
.tag.log { color: var(--sf-log); background: var(--sf-log-bg); }
.tag.proc { color: var(--sf-proc); background: var(--sf-proc-bg); }
.tag.file { color: var(--sf-file); background: var(--sf-file-bg); }
.tag.db { color: var(--sf-db); background: var(--sf-db-bg); }
table { border-collapse: collapse; margin: 16px 0; width: 100%; }
th, td { border: 1px solid var(--line); padding: 7px 12px; text-align: left; }
th { background: var(--code-bg); font-weight: 600; }
blockquote {
  margin: 14px 0;
  padding: 2px 16px;
  border-left: 3px solid var(--line);
  color: var(--muted);
}
.toc {
  font-size: 13px;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px 16px;
}
.toc-title { font-weight: 600; margin: 0 0 8px; font-size: 13px; }
.toc ol { list-style: none; margin: 0; padding: 0; }
.toc li { margin: 2px 0; }
.toc .lvl3 { padding-left: 14px; font-size: 12.5px; }
.toc a { color: var(--muted); display: block; padding: 2px 0; }
.toc a:hover { color: var(--accent); }
.foot {
  margin-top: 60px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 12.5px;
}
"""

TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{style}</style>
</head>
<body>
<div class="wrap">
<aside>
<nav class="toc" aria-label="目录">
<p class="toc-title">目录</p>
<ol>{toc}</ol>
</nav>
</aside>
<main>
{body}
<p class="foot">由 <code>scripts/render_doc.py</code> 从 {source} 生成。改文档后重新运行脚本即可。</p>
</main>
</div>
</body>
</html>
"""


def slugify(text: str, used: dict[str, int]) -> str:
    """把标题转成锚点，同名标题追加序号。"""
    base = re.sub(r"[^\w\u4e00-\u9fff]+", "-", text).strip("-")
    if not base:
        base = "section"
    count = used.get(base, 0)
    used[base] = count + 1
    return base if count == 0 else f"{base}-{count}"


def color_tokens(text: str) -> str:
    """给一行的标记着色。先后顺序固定，避免后一步匹配到前一步插入的标签。"""
    text = KEYWORD_RE.sub(lambda m: f'<span class="kw">{m.group(0)}</span>', text)
    text = RW_RE.sub(lambda m: f'<span class="rw-{m.group(1).lower()}">{m.group(1)}:</span>', text)
    text = TAG_RE.sub(
        lambda m: f'<span class="tag {SIDE_EFFECTS[m.group(1)]}">[{m.group(1)}]</span>', text
    )
    return ARROW_RE.sub('<span class="ret">⟶</span>', text)


def split_comment(line: str) -> tuple[str, str | None]:
    """把行尾注释切开。只有位于行首或空白之后的 # 才算注释起点。"""
    for index, char in enumerate(line):
        if char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index], line[index:]
    return line, None


def color_line(line: str) -> str:
    head, comment = split_comment(line)
    head = color_tokens(head)
    if comment is None:
        return head
    return head + '<span class="c">' + color_tokens(comment) + "</span>"


def color_code_blocks(html: str) -> str:
    """先给代码块上色，再给行内代码上色。代码块要先摘出来，防止被行内规则重复处理。"""
    blocks: list[str] = []

    def stash(match: re.Match[str]) -> str:
        blocks.append(match.group(1))
        return f"\x00BLOCK{len(blocks) - 1}\x00"

    html = PRE_RE.sub(stash, html)
    html = INLINE_RE.sub(lambda m: f"<code>{color_tokens(m.group(1))}</code>", html)

    def restore(match: re.Match[str]) -> str:
        inner = blocks[int(match.group(1))]
        body = "\n".join(color_line(line) for line in inner.split("\n"))
        return f"<pre><code>{body}</code></pre>"

    return re.sub(r"\x00BLOCK(\d+)\x00", restore, html)


def render_markdown(text: str) -> tuple[str, str]:
    """返回 (正文 HTML, 目录 HTML)。"""
    parser = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")
    html = color_code_blocks(parser.render(text))

    used: dict[str, int] = {}
    entries: list[tuple[int, str, str]] = []

    def annotate(match: re.Match[str]) -> str:
        level = int(match.group(1))
        inner = match.group(2)
        plain = re.sub(r"<[^>]+>", "", inner).strip()
        anchor = slugify(plain, used)
        entries.append((level, anchor, plain))
        return f'<h{level} id="{anchor}">{inner}</h{level}>'

    body = HEADING_RE.sub(annotate, html)
    items = "".join(
        f'<li class="lvl{level}"><a href="#{anchor}">{plain}</a></li>'
        for level, anchor, plain in entries
    )
    return body, items


def title_of(text: str, fallback: str) -> str:
    match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else fallback


def convert(source: Path) -> Path:
    text = source.read_text(encoding="utf-8")
    body, toc = render_markdown(text)
    document = TEMPLATE.format(
        title=title_of(text, source.stem),
        style=STYLE,
        toc=toc or "<li>（无小节）</li>",
        body=body,
        source=source.name,
    )
    target = source.with_suffix(".html")
    target.write_text(document, encoding="utf-8")
    return target


def main(argv: list[str]) -> int:
    if argv:
        sources = [Path(item) for item in argv]
    else:
        sources = sorted((ROOT / "docs").glob("*.md"))
    if not sources:
        print("没有找到要渲染的 markdown")
        return 1
    for source in sources:
        if not source.is_file():
            print(f"跳过，找不到文件：{source}")
            continue
        target = convert(source)
        size = target.stat().st_size
        print(f"{source} → {target}（{size // 1024} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
