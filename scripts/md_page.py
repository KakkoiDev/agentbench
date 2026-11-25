"""Render docs/21-local-ai-playbook.md as the site page site/local-ai.html, in the site's style.

A small Markdown reader for what the document uses: headings, paragraphs, lists, tables, fenced
code, block quotes, **bold**, *italic*, `code` and links. Links to repository files become GitHub
links; links to the data files become site-relative.
"""
import html, re

REPO = "https://github.com/KakkoiDev/agentbench/blob/main/"


def slug(text):
    t = re.sub(r"<[^>]+>", "", text).lower()
    t = re.sub(r"[^\w\- ]", "", t)
    return t.strip().replace(" ", "-")


def link(url):
    if url.startswith(("http", "#", "mailto:")):
        return url
    if url.startswith("../site/"):
        return url[len("../site/"):]
    return REPO + url.removeprefix("../").removeprefix("./")


def inline(t):
    codes = []
    t = re.sub(r"`([^`]+)`", lambda m: codes.append(m.group(1)) or f"\x00{len(codes) - 1}\x00", t)
    t = html.escape(t, quote=False)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", lambda m: f'<a href="{html.escape(link(m.group(2)))}">{m.group(1)}</a>', t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", t)
    t = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"<em>\1</em>", t)
    return re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{html.escape(codes[int(m.group(1))])}</code>", t)


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def num(c):
    return bool(re.fullmatch(r"[-—]?\s*[\d.,/% ]+(of \d+)?\s*%?", c)) and c not in ("", "—")


def render_table(lines, n):
    head, body = cells(lines[0]), [cells(l) for l in lines[2:]]
    h = [f'<figure class="fig"><div class="tblbox"><table class="mdt"><thead><tr>']
    h += [f'<th scope="col">{inline(c)}</th>' for c in head] + ["</tr></thead><tbody>"]
    for r in body:
        h.append("<tr>" + "".join((f'<th scope="row">{inline(c)}</th>' if i == 0 else f'<td class="{"n" if num(c) else ""}">{inline(c)}</td>') for i, c in enumerate(r)) + "</tr>")
    h.append("</tbody></table></div></figure>")
    return "".join(h)


def body(md):
    lines, out, i, tables, toc = md.splitlines(), [], 0, 0, []
    title = ""
    para = []
    opened = [False]

    def flush():
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para.clear()

    while i < len(lines):
        l = lines[i]
        if l.startswith("<!--"):
            i += 1
            continue
        if l.startswith("```"):
            flush()
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append("<pre class=\"code\">" + html.escape("\n".join(lines[i + 1:j])) + "</pre>")
            i = j + 1
            continue
        m = re.match(r"(#{1,4}) (.*)", l)
        if m:
            flush()
            level, text = len(m.group(1)), m.group(2)
            if level == 1:
                title = text
            else:
                sid = slug(text)
                if level == 2:
                    toc.append((sid, text, 2))
                    if opened[0]:
                        out.append("</section>")
                    out.append(f'<section class="sec" aria-labelledby="{sid}">')
                    opened[0] = True
                if level == 3 and re.match(r"F\d+\.", text):
                    toc.append((sid, text, 3))
                out.append(f'<h{level} id="{sid}">{inline(text)}</h{level}>')
            i += 1
            continue
        if l.startswith("|"):
            flush()
            j = i
            while j < len(lines) and lines[j].startswith("|"):
                j += 1
            tables += 1
            out.append(render_table(lines[i:j], tables))
            i = j
            continue
        if re.match(r"\s*(-|\d+\.) ", l):
            flush()
            tag = "ol" if re.match(r"\s*\d+\.", l) else "ul"
            items = []
            while i < len(lines) and (re.match(r"\s*(-|\d+\.) ", lines[i]) or (lines[i].startswith("  ") and lines[i].strip())):
                if re.match(r"\s*(-|\d+\.) ", lines[i]):
                    items.append(re.sub(r"^\s*(-|\d+\.) ", "", lines[i]))
                else:
                    items[-1] += " " + lines[i].strip()
                i += 1
            out.append(f"<{tag} class=\"list\">" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        if l.startswith(">"):
            flush()
            q = []
            while i < len(lines) and lines[i].startswith(">"):
                q.append(lines[i][1:].strip())
                i += 1
            out.append('<aside class="cond">' + inline(" ".join(q)) + "</aside>")
            continue
        if not l.strip():
            flush()
        else:
            para.append(l.strip())
        i += 1
    flush()
    if opened[0]:
        out.append("</section>")
    return title, toc, "\n".join(out)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Local AI findings · Mac mini M6 lab</title>
<meta name="description" content="{desc}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;0,8..60,700;1,8..60,400&amp;family=IBM+Plex+Mono:wght@400;500&amp;display=swap">
<link rel="stylesheet" href="site.css">
<link rel="stylesheet" href="v2.css">
<script src="site.js"></script>
<style>
.ra .sec{{display:flex;flex-direction:column;gap:14px}}
.ra h2{{font-size:26px;line-height:32px;margin-top:8px}}
.ra h3{{font-size:20px;line-height:28px;margin-top:18px}}
.ra .list{{padding-left:22px;display:flex;flex-direction:column;gap:6px}}
.ra .code{{white-space:pre;overflow:auto;font-family:'IBM Plex Mono',ui-monospace,Menlo,monospace;font-size:13px;line-height:21px;background:var(--tint);padding:12px 14px}}
.ra .fig{{margin:4px 0}}
.ra .tblbox{{overflow-x:auto;-webkit-overflow-scrolling:touch}}
.mdt{{border-collapse:collapse;width:100%;font-size:14.5px;line-height:21px;border-top:2px solid var(--fg);border-bottom:2px solid var(--fg)}}
.mdt th,.mdt td{{text-align:left;padding:6px 12px 6px 0;vertical-align:top}}
.mdt thead th{{border-bottom:1px solid var(--fg);font-weight:600}}
.mdt tbody tr+tr th,.mdt tbody tr+tr td{{border-top:1px solid var(--hair)}}
.mdt tbody th{{font-weight:400}}
.mdt td.n{{font-variant-numeric:tabular-nums;white-space:nowrap}}
.ra nav.toc ol.sub{{display:block;padding-left:20px;font-size:14px;line-height:22px;margin:2px 0 6px}}
.ra nav.toc > ol{{padding-left:22px;display:grid;grid-template-columns:repeat(auto-fit,minmax(min(260px,100%),1fr));gap:4px 28px;font-size:15px;line-height:24px}}
</style>
</head>
<body class="pp" data-theme="auto">
<script>try{{var t=localStorage.getItem('theme');if(t==='light'||t==='dark')document.body.setAttribute('data-theme',t)}}catch(e){{}}</script>
<header class="sh">
<div class="col-wide sh-in">
<a class="sh-name" href="index.html"><strong>Mac mini M6 lab</strong><span>Local AI on one 32 GB Mac: what works, measured</span></a>
<nav class="sh-nav" aria-label="Site">
<a href="index.html">Home</a><a href="models.html">Models</a><a href="speed.html">Speed</a><a href="intelligence.html">Intelligence</a><a href="learnings.html">Learnings</a><a href="local-ai.html" aria-current="page">Research note</a><a href="glm.html">GLM report</a><a href="bench.html">Benchmarks</a><a href="findings.html">GLM findings</a><a href="glm-speed.html">GLM speed runs</a><a href="https://github.com/KakkoiDev/agentbench">Repository<span class="sh-ext" aria-hidden="true"> ↗</span></a>
</nav>
<div class="theme" role="group" aria-label="Colour theme"><button type="button" data-t="auto">Auto</button><button type="button" data-t="light">Light</button><button type="button" data-t="dark">Dark</button></div>
</div>
</header>
<main class="col-text ra" style="display:flex;flex-direction:column;gap:32px;padding-bottom:24px">
<div class="ptitle">
<p class="sc note">Research note · generated {date}</p>
<h1>{title}</h1>
</div>
<nav class="toc" aria-label="On this page" style="border-top:2px solid var(--rule);border-bottom:1px solid var(--hair);padding:12px 0">
<ol>{toc}</ol>
</nav>
{body}
<footer class="foot">
<span>Data: <a href="data/local-ai/summary.json">summary.json</a> · <a href="data/local-ai/runs.csv">runs.csv</a> · <a href="data/local-ai/runs.json">runs.json</a> · <a href="data/local-ai/schema.json">schema.json</a> · <a href="llms.txt">llms.txt</a></span>
<span>Generated from the bench results by scripts/collect_local_ai.py; the same text is <a href="{repo}docs/21-local-ai-playbook.md">docs/21-local-ai-playbook.md</a>.</span>
</footer>
</main>
</body>
</html>
"""


def page(md, date):
    title, toc, b = body(md)
    first = re.search(r"<p>(.*?)</p>", b)
    desc = html.escape(re.sub(r"<[^>]+>", "", first.group(1)) if first else title)[:300]
    toc_html, sub = "", []
    for s, t, lv in toc + [(None, None, 2)]:
        if lv == 3:
            sub.append(f'<li><a href="#{s}">{inline(t)}</a></li>')
            continue
        if sub:
            toc_html = toc_html[:-5] + '<ol class="sub">' + "".join(sub) + "</ol></li>"
            sub = []
        if s:
            toc_html += f'<li><a href="#{s}">{inline(t)}</a></li>'

    return PAGE.format(title=inline(title), toc=toc_html, body=b, date=date, desc=desc, repo=REPO)
