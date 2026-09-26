"""Vollstaendigkeit jederzeit, Ergebnis erst nach dem letzten Testtag (ADR-081).

Zwei Befehle, bewusst getrennt:

* `stand` zeigt, ob die Sammlung laeuft -- wie viele Tage, wie viele
  fehlerfreie Datensaetze, welche Fehler. **Keine Rendite, kein Signal, kein
  Kurs.** Das darf man jeden Tag ansehen.
* `auswerten` rechnet das Urteil. Vor `AUSWERTUNG_AB` verweigert es den
  Dienst, und es gibt keine Option, die das umgeht. Ein Test, den man
  unterwegs ansieht, endet erfahrungsgemaess dann, wenn er gerade gut aussieht.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import numpy as np

from qt.meme import registrierung as reg
from qt.meme.papier import Handel, simulieren
from qt.meme.sammeln import VERZEICHNIS, lies_meta, lies_tag, vollstaendig


class ZuFrueh(RuntimeError):
    """Vor dem registrierten Auswertungszeitpunkt gibt es kein Ergebnis."""


def stand(verzeichnis: Path = VERZEICHNIS) -> str:
    """Vollstaendigkeit je Tag. Liest nur die Metadaten, nie die Datensaetze."""
    zeilen = ["Memecoin-Papiertest (ADR-081): Stand der Sammlung", ""]
    zeilen.append(f"  {'Tag':<12} {'Starts':>8} {'Stichprobe':>11} {'fehlerfrei':>11} {'Versuche':>9}  Status")
    fertig = 0
    for tag in reg.alle_tage():
        meta = lies_meta(tag, verzeichnis)
        rolle = " (Kalibrierung)" if tag == reg.KALIBRIERTAG else ""
        if meta is None:
            zeilen.append(f"  {tag.isoformat():<12} {'-':>8} {'-':>11} {'-':>11} {'-':>9}  offen{rolle}")
            continue
        status = "vollstaendig" if vollstaendig(meta) else ("unvollstaendig" if meta.get("fertig") else "in Arbeit")
        if vollstaendig(meta) and tag != reg.KALIBRIERTAG:
            fertig += 1
        zeilen.append(
            f"  {tag.isoformat():<12} {meta['starts_gesamt']:>8} {meta['stichprobe']:>11} "
            f"{meta['fehlerfrei']:>11} {meta['versuche']:>9}  {status}{rolle}"
        )
        if meta.get("fehlerarten"):
            arten = ", ".join(f"{k} {v}" for k, v in sorted(meta["fehlerarten"].items()))
            zeilen.append(f"  {'':<12} Fehler: {arten}")
    zeilen += [
        "",
        f"Vollstaendige Testtage: {fertig} von {len(reg.testtage())} "
        f"(mindestens {reg.MIN_TESTTAGE} noetig).",
        f"Auswertung ab {reg.AUSWERTUNG_AB:%Y-%m-%d %H:%M} UTC -- vorher zeigt dieser "
        "Test keine Rendite, auch nicht in Teilen.",
    ]
    return "\n".join(zeilen)


@dataclass(slots=True)
class Kennzahlen:
    n: int
    mittel: float
    median: float
    anteil_positiv: float
    anteil_graduiert: float


@dataclass(slots=True)
class Bericht:
    urteil: str
    gruende: list[str]
    testtage: list[date]
    kennzahlen: dict[tuple[str, str], Kennzahlen]
    intervall: tuple[float, float]
    haelften: tuple[float, float]
    nicht_handelbar: dict[str, int]
    fehlende_tage: list[date] = field(default_factory=list)

    def text(self) -> str:
        zeilen = [f"Memecoin-Papiertest (ADR-081): {self.urteil}", ""]
        zeilen += [f"  - {g}" for g in self.gruende]
        zeilen += ["", f"  {'Regel':<5} {'Szenario':<9} {'n':>6} {'Mittel':>9} {'Median':>9} {'> 0':>6} {'grad.':>6}"]
        for (regel, sz), k in sorted(self.kennzahlen.items()):
            zeilen.append(
                f"  {regel:<5} {sz:<9} {k.n:>6} {k.mittel:>+9.2%} {k.median:>+9.2%} "
                f"{k.anteil_positiv:>6.0%} {k.anteil_graduiert:>6.1%}"
            )
        lo, hi = self.intervall
        zeilen += [
            "",
            f"  R1 primaer, 95-%-Intervall (Block-Bootstrap ueber Tage): [{lo:+.2%}, {hi:+.2%}]",
            f"  R1 primaer, Haelften: {self.haelften[0]:+.2%} / {self.haelften[1]:+.2%}",
            f"  Vollstaendige Testtage: {len(self.testtage)}; nicht handelbar (Kurve voll): {self.nicht_handelbar}",
        ]
        if self.fehlende_tage:
            zeilen.append(f"  Nicht gezaehlte Tage: {', '.join(t.isoformat() for t in self.fehlende_tage)}")
        return "\n".join(zeilen)


def _kennzahlen(handel: list[Handel]) -> Kennzahlen:
    r = np.array([h.rendite for h in handel], dtype=float)
    if len(r) == 0:
        return Kennzahlen(0, float("nan"), float("nan"), float("nan"), float("nan"))
    grad = np.mean([h.art == "graduiert" for h in handel])
    return Kennzahlen(len(r), float(r.mean()), float(np.median(r)), float((r > 0).mean()), float(grad))


def bootstrap(je_tag: dict[date, list[float]], tage: list[date]) -> tuple[float, float]:
    """95-%-Intervall des gepoolten Mittels, Tage als Bloecke gezogen."""
    rng = np.random.default_rng(reg.BOOTSTRAP_SAAT)
    summen = np.array([sum(je_tag.get(t, [])) for t in tage], dtype=float)
    anzahlen = np.array([len(je_tag.get(t, [])) for t in tage], dtype=float)
    mittel = []
    for _ in range(reg.BOOTSTRAP_ZIEHUNGEN):
        idx = rng.integers(0, len(tage), len(tage))
        n = anzahlen[idx].sum()
        if n > 0:
            mittel.append(summen[idx].sum() / n)
    if not mittel:
        return float("nan"), float("nan")
    lo, hi = np.quantile(mittel, reg.INTERVALL)
    return float(lo), float(hi)


def entscheiden(
    r1: Kennzahlen,
    r0: Kennzahlen,
    intervall: tuple[float, float],
    haelften: tuple[float, float],
    n_tage: int,
) -> tuple[str, list[str]]:
    """Das Urteil nach der Tabelle in ADR-081, in genau dieser Reihenfolge."""
    if n_tage < reg.MIN_TESTTAGE:
        return reg.UNENTSCHIEDEN, [f"nur {n_tage} vollstaendige Testtage, noetig {reg.MIN_TESTTAGE}"]
    if r1.n < reg.MIN_HANDEL_R1:
        return reg.UNENTSCHIEDEN, [f"nur {r1.n} R1-Handel, noetig {reg.MIN_HANDEL_R1}"]
    if not r1.mittel > 0:
        gruende = [f"mittlere Rendite von R1 {r1.mittel:+.2%} <= 0"]
        if intervall[1] < 0:
            gruende.append(f"auch die obere Intervallgrenze {intervall[1]:+.2%} liegt unter null")
        return reg.NEIN, gruende
    bedingungen = [
        (intervall[0] > 0, f"untere Intervallgrenze {intervall[0]:+.2%} > 0"),
        (r1.mittel > r0.mittel, f"R1 {r1.mittel:+.2%} besser als R0 {r0.mittel:+.2%}"),
        # `all`, nicht `min`: min((0.1, nan)) ist 0.1, eine leere Haelfte waere bestanden.
        (all(h > 0 for h in haelften), f"beide Haelften positiv ({haelften[0]:+.2%} / {haelften[1]:+.2%})"),
    ]
    if all(ok for ok, _ in bedingungen):
        return reg.VIELLEICHT, [text for _, text in bedingungen] + [
            "Vielleicht ist kein Ja: die Rechnung ist systematisch zu freundlich (ADR-081)"
        ]
    return reg.UNENTSCHIEDEN, [f"nicht erfuellt: {text}" for ok, text in bedingungen if not ok]


def auswerten(jetzt: datetime, verzeichnis: Path = VERZEICHNIS) -> Bericht:
    if jetzt < reg.AUSWERTUNG_AB:
        raise ZuFrueh(
            f"Auswertung erst ab {reg.AUSWERTUNG_AB:%Y-%m-%d %H:%M} UTC (ADR-081). "
            "Bis dahin zeigt `qt meme stand` die Vollstaendigkeit, sonst nichts."
        )
    tage: dict[date, list[dict]] = {}
    vollstaendige: list[date] = []
    fehlend: list[date] = []
    for tag in reg.alle_tage():
        meta, saetze = lies_tag(tag, verzeichnis)
        if not vollstaendig(meta):
            if tag != reg.KALIBRIERTAG:
                fehlend.append(tag)
            continue
        schwelle_fehlt = tag != reg.KALIBRIERTAG and not tage
        tage[tag] = saetze
        if schwelle_fehlt:
            # Ohne einen vollstaendigen Tag davor hat R1 keine Schwelle; der
            # Tag zaehlt dann nicht, statt nur mit R0, liefert aber die
            # Schwelle fuer den naechsten (ADR-081, Nachtrag).
            fehlend.append(tag)
            continue
        if tag != reg.KALIBRIERTAG:
            vollstaendige.append(tag)

    handel, nicht_handelbar = simulieren(tage, vollstaendige)
    kennzahlen: dict[tuple[str, str], Kennzahlen] = {}
    for regel in reg.REGELN:
        for sz in reg.SZENARIEN:
            auswahl = [h for h in handel if h.regel == regel and h.szenario == sz.name]
            kennzahlen[(regel, sz.name)] = _kennzahlen(auswahl)

    primaer_r1 = [h for h in handel if h.regel == "R1" and h.szenario == reg.PRIMAER]
    je_tag: dict[date, list[float]] = {}
    for h in primaer_r1:
        je_tag.setdefault(h.tag, []).append(h.rendite)
    intervall = bootstrap(je_tag, vollstaendige)

    testtage = reg.testtage()
    mitte = len(testtage) // 2
    haelften = tuple(
        float(np.mean(r)) if (r := [h.rendite for h in primaer_r1 if h.tag in teil]) else float("nan")
        for teil in (set(testtage[:mitte]), set(testtage[mitte:]))
    )

    urteil, gruende = entscheiden(
        kennzahlen[("R1", reg.PRIMAER)],
        kennzahlen[("R0", reg.PRIMAER)],
        intervall,
        haelften,  # type: ignore[arg-type]
        len(vollstaendige),
    )
    return Bericht(urteil, gruende, vollstaendige, kennzahlen, intervall, haelften, nicht_handelbar, fehlend)  # type: ignore[arg-type]
