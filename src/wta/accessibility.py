"""The app's own accessibility check for smoke tests: text colour contrast (WCAG 2.1, 1.4.3).

Claude checks labels, names and alt text from the page snapshot; contrast cannot be judged
from a snapshot, so the app measures it in the page after a smoke run. It runs in an isolated
JavaScript world, so the page's own scripts cannot change the result.
"""

import json

from wta.db.models import PageIssueKind
from wta.db.runs import IssueRecord

MAX_FINDINGS = 20

# Returned as JSON text: PySide6 hands JavaScript objects and arrays back as an empty string.
CONTRAST_JS = r"""
JSON.stringify((() => {
  const parse = (c) => {
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);
    return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1};
  };
  const lum = ({r, g, b}) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const background = (el) => {
    for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.backgroundImage && style.backgroundImage !== 'none') return null;  // cannot tell
      const bg = parse(style.backgroundColor);
      if (bg && bg.a >= 0.99) return bg;
    }
    return {r: 255, g: 255, b: 255, a: 1};
  };
  const describe = (el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '')
      + (el.classList.length ? '.' + Array.from(el.classList).slice(0, 2).join('.') : '');
  const seen = new Set();
  const found = [];
  for (const el of document.body ? document.body.querySelectorAll('*') : []) {
    const text = Array.from(el.childNodes).filter((n) => n.nodeType === 3).map((n) => n.textContent).join(' ')
        .replace(/\s+/g, ' ').trim();
    if (!text) continue;
    const style = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    if (style.visibility === 'hidden' || style.display === 'none' || box.width === 0 || box.height === 0) continue;
    const fg = parse(style.color);
    const bg = background(el);
    if (!fg || !bg || fg.a < 0.99) continue;
    const l1 = lum(fg), l2 = lum(bg);
    const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    const size = parseFloat(style.fontSize);
    const bold = parseInt(style.fontWeight, 10) >= 700;
    const needed = (size >= 24 || (bold && size >= 18.66)) ? 3 : 4.5;
    if (ratio >= needed) continue;
    const key = style.color + '|' + describe(el);
    if (seen.has(key)) continue;
    seen.add(key);
    found.push({text: text.slice(0, 60), ratio: Math.round(ratio * 100) / 100, needed,
                color: style.color, background: `rgb(${bg.r}, ${bg.g}, ${bg.b})`, element: describe(el)});
  }
  found.sort((a, b) => a.ratio - b.ratio);
  return {url: location.href, findings: found.slice(0, 40)};
})())
"""


def contrast_issues(result) -> list[IssueRecord]:
    """IssueRecords from the script's result (JSON text; worst first, at most MAX_FINDINGS)."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return []
    if not isinstance(result, dict):
        return []
    url = str(result.get("url", ""))
    issues = []
    for item in (result.get("findings") or [])[:MAX_FINDINGS]:
        if not isinstance(item, dict):
            continue
        issues.append(IssueRecord(
            PageIssueKind.ACCESSIBILITY,
            f"Low text contrast {item.get('ratio')}:1 (needs {item.get('needed')}:1) — “{item.get('text', '')}” "
            f"in {item.get('element', '')}, {item.get('color', '')} on {item.get('background', '')}",
            url, level="warning",
        ))
    return issues
