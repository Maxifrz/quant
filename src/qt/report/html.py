"""Das Cockpit als eine einzelne HTML-Datei.

Warum eine Datei und kein Server: `data/` und `reports/` sind gitignored und
verschwinden mit dem Container -- ein laufender Server teilt dieses
Schicksal, eine Datei nicht. Man kann sie herunterladen, in einen Chat
haengen oder committen, und sie ist auf einem Telefon lesbar. Das Paper-Konto
wird ueber Wochen **taeglich** angeschaut; `qt.report.daily` ist genau aus
diesem Grund reiner Text, und diese Fassung ist dieselbe Ueberlegung eine
Stufe weiter.

Deshalb gilt hier: **keine externe Anfrage.** Kein CDN, kein Font, kein
Skript, kein Bild von aussen. Ein Report, der ohne Netz halb leer ist, ist
kein Beleg. Ein Test haelt das fest.

Und: es gibt keinen Knopf, der etwas tut. Befehle stehen als kopierbarer
Text da. Das ist keine fehlende Bequemlichkeit, sondern der Punkt -- der
Kill-Switch verlangt bewusst einen Menschen mit einer Begruendung
(`qt paper reset-killswitch --note`), und ein Knopf dafuer waere der
schleichende Weg, die Freigabe abzuschaffen.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

from qt.core.config import REPORT_DIR
from qt.core.types import timeframe_seconds
from qt.report.cockpit import Cockpit, PaperView, SeriesView, STALE_AFTER_BARS

# Schmalste sichtbare Luecke in der Abdeckungsleiste, in Prozent der Breite.
# Ohne Mindestbreite verschwindet eine Ein-Bar-Luecke im Rundungsfehler; mit
# ihr sieht eine kleine Luecke groesser aus als sie ist. Die zweite Luege ist
# die harmlosere -- sie fuehrt zum Hinsehen, nicht zum Wegsehen -- und sie
# steht in der Legende, damit "sichtbar" nicht mit "gross" verwechselt wird.
MIN_GAP_WIDTH_PCT = 0.4

CSS = """
:root {
  color-scheme: light dark;
  --bg: #f6f6f4; --card: #fff; --ink: #1b1b1a; --muted: #6b6b66;
  --line: #e2e2dd; --ok: #2f7d4f; --warn: #a86400; --alarm: #b3261e;
  --ok-bg: #e8f3ec; --warn-bg: #fdf3e0; --alarm-bg: #fbe9e7; --data: #4a7fb5;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16171a; --card: #1e2024; --ink: #e8e8e4; --muted: #9a9a94;
    --line: #2e3136; --ok: #6cc48c; --warn: #e0a441; --alarm: #f2857a;
    --ok-bg: #1c2a22; --warn-bg: #2c2418; --alarm-bg: #2e1d1b; --data: #6fa3d6;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 16px 14px 48px; background: var(--bg); color: var(--ink);
  font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  -webkit-text-size-adjust: 100%;
}
main { max-width: 780px; margin: 0 auto; }
h1 { font-size: 1.25rem; margin: 0 0 2px; }
h2 { font-size: 0.95rem; margin: 0 0 10px; letter-spacing: .02em; }
.sub { color: var(--muted); font-size: .85rem; margin: 0 0 18px; }
.card {
  background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 14px; margin: 0 0 12px;
}
.card.ok { border-left: 4px solid var(--ok); }
.card.warn { border-left: 4px solid var(--warn); background: var(--warn-bg); }
.card.alarm { border-left: 4px solid var(--alarm); background: var(--alarm-bg); }
.status { font-size: 1.05rem; font-weight: 600; margin: 0 0 6px; }
.status.ok { color: var(--ok); } .status.warn { color: var(--warn); }
.status.alarm { color: var(--alarm); }
.grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
@media (max-width: 370px) { .grid { grid-template-columns: 1fr; } }
.kv { border: 1px solid var(--line); border-radius: 8px; padding: 9px 10px; }
.kv .k { color: var(--muted); font-size: .74rem; text-transform: uppercase;
  letter-spacing: .04em; }
.kv .v { font-size: 1.05rem; font-variant-numeric: tabular-nums; margin-top: 2px; }
.kv .n { color: var(--muted); font-size: .78rem; margin-top: 2px; }
table { width: 100%; border-collapse: collapse; font-size: .86rem;
  font-variant-numeric: tabular-nums; }
th, td { text-align: right; padding: 6px 4px; white-space: nowrap;
  border-bottom: 1px solid var(--line); }
th:first-child, td:first-child { text-align: left; }
th { color: var(--muted); font-weight: 500; font-size: .74rem;
  text-transform: uppercase; letter-spacing: .04em; }
.scroll { overflow-x: auto; }
@media (max-width: 520px) {
  table.pos, table.pos tbody, table.pos tr, table.pos td { display: block;
    width: 100%; }
  table.pos thead { display: none; }
  table.pos tr { border-bottom: 1px solid var(--line); padding: 7px 0; }
  table.pos tr:last-child { border-bottom: 0; }
  table.pos td { display: flex; justify-content: space-between; gap: 12px;
    border: 0; padding: 2px 0; text-align: right; }
  table.pos td::before { content: attr(data-l); color: var(--muted);
    font-size: .74rem; text-transform: uppercase; letter-spacing: .04em; }
}
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
pre {
  background: var(--bg); border: 1px solid var(--line); border-radius: 7px;
  padding: 9px 10px; margin: 8px 0 0; font-size: .8rem;
  white-space: pre-wrap; overflow-wrap: anywhere; user-select: all;
}
.bar { height: 9px; border-radius: 5px; background: var(--line);
  overflow: hidden; margin: 6px 0 4px; }
.bar > i { display: block; height: 100%; }
.strip { width: 100%; height: 22px; display: block; }
.strip-wrap { margin: 4px 0 2px; }
.axis { display: flex; justify-content: space-between; color: var(--muted);
  font-size: .72rem; font-variant-numeric: tabular-nums; }
.note { color: var(--muted); font-size: .8rem; margin: 8px 0 0; }
.empty { color: var(--muted); }
.legend { display: flex; gap: 14px; align-items: center; color: var(--muted);
  font-size: .76rem; margin-top: 8px; flex-wrap: wrap; }
.legend i { display: inline-block; width: 13px; height: 9px; border-radius: 2px;
  margin-right: 5px; vertical-align: middle; }
footer { color: var(--muted); font-size: .76rem; margin-top: 22px;
  border-top: 1px solid var(--line); padding-top: 12px; }
footer dl { display: grid; grid-template-columns: auto 1fr; gap: 2px 12px; margin: 0; }
footer dt { color: var(--muted); }
footer dd { margin: 0; overflow-wrap: anywhere; }
"""


def render(cockpit: Cockpit) -> str:
    """Die ganze Seite als String."""
    parts = [
        "<!doctype html>",
        '<html lang="de"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(_title(cockpit))}</title>",
        f"<style>{CSS}</style></head><body><main>",
        _header(cockpit),
    ]
    parts += _paper_section(cockpit)
    parts.append(_data_section(cockpit))
    parts.append(_footer(cockpit))
    parts.append("</main></body></html>")
    return "\n".join(parts)


def write(cockpit: Cockpit, out: Path | None = None) -> Path:
    """Die Seite schreiben. Legt `reports/` an, falls noetig."""
    path = out or REPORT_DIR / "cockpit.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(cockpit), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Abschnitte
# --------------------------------------------------------------------------


def _title(cockpit: Cockpit) -> str:
    if cockpit.paper is None:
        return "Cockpit"
    return f"Cockpit -- {cockpit.paper.strategy} {' '.join(cockpit.paper.symbols)}"


def _header(cockpit: Cockpit) -> str:
    if cockpit.paper is None:
        head = "Cockpit"
        sub = escape("Kein Paper-Konto -- unten steht, was es anlegt.")
    else:
        p = cockpit.paper
        head = (
            f"{escape(p.strategy)} &middot; {escape(', '.join(p.symbols))} "
            f"&middot; {escape(p.timeframe)}"
        )
        sub = f"Stand {_stamp(cockpit.now)}"
    warn = "".join(f"<p class='note'>{escape(w)}</p>" for w in cockpit.warnings)
    return f"<h1>{head}</h1><p class='sub'>{sub}</p>{warn}"


def _paper_section(cockpit: Cockpit) -> list[str]:
    if cockpit.paper is None:
        path = escape(str(cockpit.missing_paper_path))
        return [
            "<div class='card'><h2>Paper-Konto</h2>"
            f"<p class='empty'>Kein Konto unter <code>{path}</code>.</p>"
            "<pre>qt paper run --strategy macross --symbols BTC/USD --tf 1d</pre>"
            "<p class='note'>Der erste Lauf legt nur den Startpunkt fest -- "
            "gehandelt wird ab dem naechsten Bar, der danach schliesst.</p></div>"
        ]
    return [_killswitch(cockpit.paper), _freshness(cockpit.paper), _account(cockpit.paper)]


def _killswitch(p: PaperView) -> str:
    """Der Kill-Switch steht oben, nicht unten.

    Uebernommen aus `qt.report.daily`: es ist die eine Zeile, die ein
    Mensch, der den Report ueberfliegt, nicht uebersehen darf.
    """
    schwelle = (
        f"<p class='note'>Angenommene Schwelle {p.max_drawdown:.0%} Drawdown vom "
        "Hoechststand. Sie ist ein Aufrufparameter von <code>qt paper run</code> "
        "und steht nicht im Kontostand -- wer dort einen anderen Wert gesetzt "
        "hat, sieht hier die falsche Grenze.</p>"
    )

    if not p.state.halted and p.headroom > 0:
        return (
            "<div class='card ok'><h2>Kill-Switch</h2>"
            "<p class='status ok'>Laeuft</p>" + schwelle + "</div>"
        )

    if not p.state.halted:
        # Der Zustand sagt "laeuft", die Zahlen sagen "haette halten muessen".
        # Das ist kein Widerspruch, sondern eine Aussage ueber die Zeit
        # dazwischen: `halted` beschreibt, was der **letzte Tick** entschieden
        # hat, der Drawdown dagegen die aktuellen Preise. Ein gruenes "laeuft"
        # ueber dieser Lage waere die eine Zeile, wegen der jemand nicht
        # hinsieht.
        cmd = (
            f"qt paper run --strategy {p.strategy} "
            f"--symbols {','.join(p.symbols)} --tf {p.timeframe}"
        )
        return (
            "<div class='card warn'><h2>Kill-Switch</h2>"
            "<p class='status warn'>Nicht ausgeloest &mdash; aber die Schwelle "
            "ist rechnerisch ueberschritten</p>"
            f"<p class='note'>Drawdown {p.drawdown:.2%} gegen {p.max_drawdown:.0%}. "
            "Der gespeicherte Zustand stammt vom letzten Tick; bewertet ist die "
            "Lage erst, wenn wieder einer laeuft:</p>"
            f"<pre>{escape(cmd)}</pre>" + schwelle + "</div>"
        )

    reasons = "".join(
        f"<li>{escape(r)}</li>" for r in p.state.halt_reasons[-3:]
    )
    cmd = (
        f"qt paper reset-killswitch --strategy {p.strategy} "
        f"--symbols {','.join(p.symbols)} --tf {p.timeframe} --note \"...\""
    )
    return (
        "<div class='card alarm'><h2>Kill-Switch</h2>"
        "<p class='status alarm'>AUSGELOEST &mdash; keine neuen Positionen</p>"
        f"<ul class='note'>{reasons}</ul>"
        "<p class='note'>Es gibt bewusst keinen automatischen Wiederanlauf. "
        "Zwischen Ausloesung und Neustart gehoert ein Mensch, der klaert, warum "
        "das Konto ueberhaupt so weit gefallen ist:</p>"
        f"<pre>{escape(cmd)}</pre></div>"
    )


def _freshness(p: PaperView) -> str:
    """Alter und Rueckstand -- die Kachel, die die Textausgabe nicht hat.

    Ein Paper-Konto, das Wochen laufen soll, scheitert nicht spektakulaer.
    Es hoert auf, getickt zu werden, und ein Kontostand ohne Alter sieht
    dabei jeden Tag gleich gesund aus.
    """
    step = timeframe_seconds(p.timeframe)
    age = p.age_seconds
    stale = age is not None and age > STALE_AFTER_BARS * step
    behind = p.unprocessed_bars > 0

    if behind:
        level, status = "warn", f"{p.unprocessed_bars} Bar(s) nicht verarbeitet"
    elif stale:
        level, status = "warn", "Kein frischer Bar"
    else:
        level, status = "ok", "Aktuell"

    cmd = (
        f"qt paper run --strategy {p.strategy} "
        f"--symbols {','.join(p.symbols)} --tf {p.timeframe}"
    )
    hint = (
        f"<pre>{escape(cmd)}</pre>"
        if behind
        else "<p class='note'>Der Store hat nichts, was ein Tick noch "
        "verarbeiten koennte.</p>"
    )
    return (
        f"<div class='card {level}'><h2>Frische</h2>"
        f"<p class='status {level}'>{escape(status)}</p>"
        "<div class='grid'>"
        + _kv("Letzter verarbeiteter Bar", _stamp(p.last_processed), _ago(age))
        + _kv(
            "Juengster Bar im Store",
            _stamp(p.newest_close),
            "Close-Zeit, wie der Tick sie sieht",
        )
        + "</div>"
        + hint
        + "</div>"
    )


def _account(p: PaperView) -> str:
    dd, room = p.drawdown, p.headroom
    level = "alarm" if room <= 0 else ("warn" if room < p.max_drawdown / 4 else "ok")
    filled = min(100.0, dd / p.max_drawdown * 100) if p.max_drawdown > 0 else 0.0
    colour = "var(--alarm)" if level == "alarm" else (
        "var(--warn)" if level == "warn" else "var(--ok)"
    )

    positions = (
        _positions(p)
        if p.state.positions
        else "<p class='note'>Keine Positionen &mdash; flach.</p>"
    )

    warm = (
        f"Warm seit {_stamp(datetime.fromisoformat(p.state.warmup_end))}"
        if p.state.warmup_end
        else "Noch im Warmup &mdash; die Strategie hat noch keine Meinung."
    )

    return (
        "<div class='card'><h2>Konto</h2>"
        "<div class='grid'>"
        + _kv("Eigenkapital", f"{p.equity:,.2f}", "zu letzten bekannten Preisen")
        + _kv("Hoechststand", f"{p.state.peak_equity:,.2f}", "Basis des Kill-Switches")
        + _kv("Cash", f"{p.state.cash:,.2f}", "")
        + _kv("Fills", f"{p.state.n_fills:,}",
              f"Gebuehren {p.state.fees_paid:,.2f} &middot; Umsatz {p.state.turnover:,.0f}")
        + "</div>"
        f"<p class='note' style='margin-top:12px'>Drawdown {dd:.2%} von "
        f"{p.max_drawdown:.0%} &mdash; {_room(room)}.</p>"
        f"<div class='bar'><i style='width:{filled:.1f}%;background:{colour}'></i></div>"
        f"{positions}"
        f"<p class='note'>{warm}</p>"
        "</div>"
    )


def _positions(p: PaperView) -> str:
    """Positionen als Tabelle -- auf schmalen Schirmen gestapelt.

    Fuenf Zahlenspalten passen nicht auf ein Telefon. Waagerecht scrollen
    waere die bequemere Loesung und die schlechtere: der Abschnitt sieht
    dann aus wie ein Anzeigefehler, und die abgeschnittene Spalte ist
    ausgerechnet der Marktwert. Gestapelt bleibt jede Zahl sichtbar.
    """
    rows = "".join(
        "<tr>"
        f"<td data-l='Symbol'>{escape(sym)}</td>"
        f"<td data-l='Menge'>{pos['qty']:+.6f}</td>"
        f"<td data-l='Einstand'>{pos['avg_price']:,.2f}</td>"
        f"<td data-l='Preis'>{price}</td>"
        f"<td data-l='Wert'>{value:,.2f}</td>"
        "</tr>"
        for sym, pos in sorted(p.state.positions.items())
        for price in [
            f"{p.prices[sym]:,.2f}" if sym in p.prices else "&mdash;"
        ]
        for value in [pos["qty"] * p.prices.get(sym, pos["avg_price"])]
    )
    return (
        "<table class='pos'><thead><tr><th>Symbol</th><th>Menge</th>"
        "<th>Einstand</th><th>Preis</th><th>Wert</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _data_section(cockpit: Cockpit) -> str:
    if not cockpit.series:
        return (
            "<div class='card'><h2>Datenbestand</h2>"
            "<p class='empty'>Store ist leer.</p>"
            "<pre>qt data pull --symbols \"BTC/USD,ETH/USD\" --tf \"1h,4h,1d\" "
            "--since 2019-01-01</pre></div>"
        )

    broken = [s for s in cockpit.series if not s.report.ok]
    level = "alarm" if broken else ""
    head = (
        f"<p class='status alarm'>{len(broken)} Datensatz/Datensaetze mit "
        "kaputten Bars</p>"
        if broken
        else ""
    )
    return (
        f"<div class='card {level}'><h2>Datenbestand</h2>{head}"
        + "".join(_series(s) for s in cockpit.series)
        + "<div class='legend'>"
        f"<span><i style='background:var(--data)'></i>vorhanden</span>"
        f"<span><i style='background:var(--alarm)'></i>Luecke</span>"
        f"<span>Luecken unter {MIN_GAP_WIDTH_PCT}% Breite werden auf "
        f"{MIN_GAP_WIDTH_PCT}% gezogen &mdash; sichtbar heisst hier nicht gross.</span>"
        "</div></div>"
    )


def _series(s: SeriesView) -> str:
    r = s.report
    if r.n_bars == 0:
        return f"<p class='empty'>{escape(r.symbol)} {escape(r.timeframe)}: leer</p>"

    flag = "" if r.ok else " &middot; <span style='color:var(--alarm)'>kaputte Bars</span>"
    largest = s.largest_gap
    largest_txt = (
        f" &middot; groesste Luecke {largest.missing_bars:,} Bars "
        f"({_days(largest)} Tage)"
        if largest
        else ""
    )
    return (
        f"<p class='note' style='color:var(--ink);margin-top:14px'>"
        f"<strong>{escape(r.symbol)} {escape(r.timeframe)}</strong> &middot; "
        f"{r.n_bars:,} Bars &middot; Abdeckung {r.coverage:.2%} &middot; "
        f"{len(r.gaps)} Luecken ({r.missing_bars:,} Bars){largest_txt}{flag}</p>"
        f"<div class='strip-wrap'>{_strip(s)}</div>"
        f"<div class='axis'><span>{r.start:%Y-%m-%d}</span>"
        f"<span>{r.end:%Y-%m-%d}</span></div>"
    )


def _strip(s: SeriesView) -> str:
    """Die Abdeckungsleiste als Inline-SVG.

    SVG und nicht PNG: es skaliert auf jeder Bildschirmbreite, kostet keine
    zweite Zeichenbibliothek und bleibt im Quelltext lesbar -- man kann der
    Datei ansehen, wo die Luecke sitzt, ohne sie zu rendern.
    """
    rects = [
        '<rect x="0" y="0" width="100" height="22" fill="var(--data)" rx="2"/>'
    ]
    for seg in s.segments:
        if not seg.is_gap:
            continue
        x0 = seg.x0 * 100
        width = max(MIN_GAP_WIDTH_PCT, (seg.x1 - seg.x0) * 100)
        x0 = min(x0, 100 - width)
        rects.append(
            f'<rect x="{x0:.3f}" y="0" width="{width:.3f}" height="22" '
            f'fill="var(--alarm)"><title>{escape(str(seg.start.date()))} bis '
            f"{escape(str(seg.end.date()))} &mdash; {seg.missing_bars:,} Bars"
            "</title></rect>"
        )
    return (
        '<svg class="strip" viewBox="0 0 100 22" preserveAspectRatio="none" '
        f'role="img" aria-label="Abdeckung {escape(s.report.symbol)} '
        f'{escape(s.report.timeframe)}">{"".join(rects)}</svg>'
    )


def _footer(cockpit: Cockpit) -> str:
    """Herkunft. Jede Zahl auf dieser Seite gehoert zu genau einem Lauf."""
    rows = {
        "Befehl": cockpit.command or "qt report html",
        "Commit": cockpit.git_commit,
        "Erzeugt": _stamp(cockpit.now),
        "Datenverzeichnis": str(cockpit.data_dir),
    }
    if cockpit.paper is not None:
        rows["Kontostand"] = str(cockpit.paper.path)
        rows["Konto erstellt"] = cockpit.paper.state.created_at
        rows["Zuletzt geschrieben"] = cockpit.paper.state.updated_at
    items = "".join(
        f"<dt>{escape(k)}</dt><dd>{escape(str(v))}</dd>" for k, v in rows.items()
    )
    return (
        f"<footer><dl>{items}</dl>"
        "<p>Diese Seite liest nur. Alle Befehle stehen als Text da, weil ein "
        "Knopf, der ein Konto veraendert, hier nicht hingehoert.</p></footer>"
    )


# --------------------------------------------------------------------------
# Kleinteile
# --------------------------------------------------------------------------


def _kv(key: str, value: str, note: str = "") -> str:
    note_html = f"<div class='n'>{note}</div>" if note else ""
    return (
        f"<div class='kv'><div class='k'>{escape(key)}</div>"
        f"<div class='v'>{value}</div>{note_html}</div>"
    )


def _stamp(ts: datetime | None) -> str:
    return "&mdash;" if ts is None else ts.strftime("%Y-%m-%d %H:%M UTC")


def _ago(seconds: float | None) -> str:
    """Alter relativ. Ein absoluter Zeitstempel allein wird nicht gelesen."""
    if seconds is None:
        return "nie"
    if seconds < 0:
        return "in der Zukunft"
    if seconds < 3600:
        return f"vor {int(seconds // 60)} Min"
    if seconds < 172800:
        return f"vor {seconds / 3600:.0f} Std"
    return f"vor {seconds / 86400:.0f} Tagen"


def _room(room: float) -> str:
    """Luft bis zur Schwelle -- oder, wenn sie weg ist, wieviel darueber."""
    if room >= 0:
        return f"noch {room:.2%} Luft"
    return f"<strong>{-room:.2%} darueber</strong>"


def _days(seg) -> str:
    return f"{(seg.end - seg.start).total_seconds() / 86400:.0f}"
