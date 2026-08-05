"""Self-contained HTML status report for an evidence archive."""

from datetime import datetime, timezone
from html import escape
from pathlib import Path

from . import integrity


def _cell(value):
    return escape("" if value is None else str(value))


def render(store):
    """Return a complete HTML dashboard generated from the current archive."""
    names = store.check_names()
    findings, counts = integrity.check(store)
    broken = integrity.broken(findings)
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    controls = store.control_checks()

    cards = []
    total = store.stats()
    values = [
        ("Overall", "Healthy" if not broken else "Broken",
         "good" if not broken else "bad"),
        ("Polls", total["polls"], ""),
        ("Failures", total["failures"],
         "bad" if total["failures"] else "good"),
        ("Retained responses", counts["referenced"], ""),
        ("Unanchored polls", counts["exposed"],
         "warn" if counts["exposed"] else "good"),
    ]
    for label, value, css in values:
        cards.append(
            f'<div class="card {css}"><span>{_cell(label)}</span>'
            f'<strong>{_cell(value)}</strong></div>')

    target_rows = []
    for name in names:
        stats = store.stats(name)
        last = store.last_poll(name)
        target_rows.append("<tr>" + "".join([
            f"<td>{_cell(name)}{' <small>control</small>' if name in controls else ''}</td>",
            f"<td>{stats['polls']}</td>",
            f"<td>{stats['failures']}</td>",
            f"<td>{stats['changes']}</td>",
            f"<td>{stats['normalised_changes']}</td>",
            f"<td>{store.unanchored_polls(name)}</td>",
            f"<td>{_cell(last['polled_at'] if last else 'never')}</td>",
        ]) + "</tr>")

    anchors = store.anchors()
    manifests = {}
    for row in anchors:
        manifests.setdefault(row["manifest_ref"], row)
    anchor_rows = [
        "<tr>" + "".join([
            f"<td>{_cell(row['anchored_at'])}</td>",
            f"<td><span class=\"pill {_cell(row['status'])}\">{_cell(row['status'])}</span></td>",
            f"<td>{_cell(ref)}</td>",
        ]) + "</tr>"
        for ref, row in reversed(list(manifests.items()))
    ][:12]

    recent = store.recent_polls(limit=30)
    recent_rows = [
        "<tr>" + "".join([
            f"<td>{row['id']}</td>",
            f"<td>{_cell(row['polled_at'])}</td>",
            f"<td>{_cell(row['check_name'])}</td>",
            f"<td>{'ok' if row['ok'] else 'failed'}</td>",
            f"<td>{'yes' if row['changed'] else 'no'}</td>",
            f"<td title=\"{_cell(row['error'])}\">{_cell((row['error'] or '')[:90])}</td>",
        ]) + "</tr>" for row in recent
    ]

    finding_rows = [
        f'<li class="{_cell(f.severity)}"><strong>{_cell(f.severity)}</strong> '
        f'{_cell(f.kind)} — {_cell(f.detail)}</li>' for f in findings
    ] or ["<li class=\"good\">No integrity findings.</li>"]

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Evidence archive dashboard</title>
<style>
:root{{--bg:#f4f6f8;--panel:#fff;--text:#18212b;--muted:#687586;--line:#dce2e8;
--good:#177245;--warn:#9a6700;--bad:#b42318}}*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,sans-serif}}
main{{max-width:1240px;margin:auto;padding:28px}}h1{{margin:0;font-size:25px}}h2{{font-size:17px;margin:0 0 14px}}
.sub{{color:var(--muted);margin:4px 0 22px}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}
.card,section{{background:var(--panel);border:1px solid var(--line);border-radius:10px;box-shadow:0 1px 2px #0000000a}}
.card{{padding:15px}}.card span{{display:block;color:var(--muted)}}.card strong{{font-size:24px}}.good strong,.good{{color:var(--good)}}
.warn strong,.SUSPECT{{color:var(--warn)}}.bad strong,.BROKEN{{color:var(--bad)}}section{{padding:18px;margin-top:16px;overflow:auto}}
table{{border-collapse:collapse;width:100%;white-space:nowrap}}th,td{{text-align:left;padding:9px;border-bottom:1px solid var(--line)}}th{{color:var(--muted);font-weight:600}}
small,.pill{{background:#e8edf2;border-radius:999px;padding:2px 7px;font-size:11px}}.complete{{background:#dff3e8;color:var(--good)}}
.pending{{background:#fff1c7;color:var(--warn)}}ul{{margin:0;padding-left:21px}}li{{margin:7px 0}}
@media(prefers-color-scheme:dark){{:root{{--bg:#11161c;--panel:#18212b;--text:#e8edf2;--muted:#9ba8b7;--line:#32404d}}}}
</style></head><body><main>
<h1>Evidence archive dashboard</h1><p class="sub">Generated {_cell(generated)} from {_cell(store.root)}. Refresh by running <code>kibitzr archive report</code>.</p>
<div class="cards">{''.join(cards)}</div>
<section><h2>Checks</h2><table><thead><tr><th>Check</th><th>Polls</th><th>Failures</th><th>Raw changes</th><th>Document changes</th><th>Unanchored</th><th>Last poll</th></tr></thead><tbody>{''.join(target_rows)}</tbody></table></section>
<section><h2>Integrity</h2><ul>{''.join(finding_rows)}</ul></section>
<section><h2>Recent anchors</h2><table><thead><tr><th>Time</th><th>Status</th><th>Manifest</th></tr></thead><tbody>{''.join(anchor_rows)}</tbody></table></section>
<section><h2>Recent polls</h2><table><thead><tr><th>ID</th><th>Time</th><th>Check</th><th>Result</th><th>Raw changed</th><th>Error</th></tr></thead><tbody>{''.join(recent_rows)}</tbody></table></section>
</main></body></html>"""


def write(store, output):
    path = Path(output).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(store), encoding="utf-8")
    return path
