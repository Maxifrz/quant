"""Die Kritik wird abgerechnet: jede Zahl eines Modells gegen das, was eintrat.

Eine Zahl, die ein Sprachmodell ausgibt, ist nur dann eine Aussage, wenn sich
hinterher sagen laesst, ob sie stimmte. Laya (NandhaKishorM/laya, ADR-080)
zieht das konsequent durch: jede Antwort ist die Wahrscheinlichkeit eines
benannten Ereignisses, trainiert und bewertet mit strikt properen Scoring
Rules, und die Konfidenz wird auf zurueckgehaltenen Daten nachkalibriert. Der
lehrreichste Befund dort: auf Khmer 0,000 Trefferquote bei 0,952 Konfidenz.
Die eigene Sicherheit eines Modells warnt nicht -- messen muss man sie.

Hier stand bis ADR-080 das Gegenteil: `overfitting_risk`, "wie stark der Code
nach Anpassung an Vergangenes aussieht", eine Skala ohne Ereignis. Dieses
Modul rechnet ab, was sich abrechnen laesst:

* die drei Wahrscheinlichkeiten, die die Kritik seit ADR-080 ausgibt, gegen
  die Ereignisse, die die Pipeline selbst misst -- mit dem Brier-Score;
* den Altbestand, soweit er Ereignisse beruehrt: die Empfehlung gegen den
  Ausgang und das Umschlags-Flag gegen den gemessenen Umschlag.

**Der Massstab ist die Basisrate, nicht der Muenzwurf.** Bei null Treffern in
24 Versuchen ist "faellt durch" die bestmoegliche konstante Prognose. Ein
Kritiker, der sie nicht schlaegt, filtert nichts, was die Statistik nicht
ohnehin filtert. Die Basisrate wird im Nachhinein aus denselben Faellen
bestimmt; das ist das Beste, was eine konstante Prognose haette leisten
koennen, und damit ein strenger, aber fairer Gegner.

Das Nachmessen des Umschlags ist **kein Versuch**: gemessen wird die Kritik,
nicht der Kandidat. Es folgt keine Auswahl daraus, und der Umschlag ist eine
Eigenschaft der Handelsfrequenz, nicht der Rendite (ADR-032, ADR-057).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from qt.core.types import Bar
from qt.research.gate import MAX_UMSCHLAG_PRO_JAHR
from qt.research.registry import SCREENING_PASSED

#: Wie der Altbestand nachgemessen wird. Ein fester Massstab fuer alle
#: Kandidaten, gleich welcher Lauf sie erzeugt hat: das Gate misst den
#: Umschlag einer Timing-Strategie auf BTC/USD, und die 7x sind dort definiert.
UMSCHLAG_MARKT = "BTC/USD"
UMSCHLAG_TIMEFRAME = "1d"
UMSCHLAG_QUELLE = (
    f"nachgemessen: {UMSCHLAG_MARKT} {UMSCHLAG_TIMEFRAME}, "
    "Groessenschicht, Gate-Kosten (ADR-080)"
)


@dataclass(frozen=True, slots=True)
class Ereignis:
    """Ein beobachtbares Ereignis und die Spalte, in der die Kritik es prognostiziert."""

    spalte: str
    feld: str
    text: str


EREIGNISSE: tuple[Ereignis, ...] = (
    Ereignis(
        "critic_p_umschlag",
        "p_umschlag_ueber_budget",
        f"Umschlag/EK/Jahr > {MAX_UMSCHLAG_PRO_JAHR:.0f}x",
    ),
    Ereignis("critic_p_oos_positiv", "p_oos_sharpe_positiv", "OOS-Sharpe > 0"),
    Ereignis("critic_p_dsr", "p_dsr_bestanden", "DSR bestanden"),
)


def brier(prognose: np.ndarray, ausgang: np.ndarray) -> float:
    """Mittlerer quadrierter Abstand -- strikt proper, 0 ist perfekt."""
    p = np.asarray(prognose, dtype=float)
    y = np.asarray(ausgang, dtype=float)
    if len(p) == 0:
        return float("nan")
    return float(np.mean((p - y) ** 2))


def _spalte(frame: pd.DataFrame, name: str) -> pd.Series:
    if name in frame.columns:
        return frame[name]
    return pd.Series([None] * len(frame), index=frame.index, dtype=object)


def _gesetzt(werte: pd.Series) -> pd.Series:
    """Flag-Spalte als bool; fehlend heisst nicht gesetzt."""
    return werte.map(lambda v: bool(v) if pd.notna(v) else False).astype(bool)


def ausgang(frame: pd.DataFrame, ereignis: Ereignis) -> pd.Series:
    """Was eintrat, je Kandidat: 1.0, 0.0 oder NaN fuer "nicht beobachtet".

    * Umschlag: beobachtet, sobald ein Umschlag gemessen ist.
    * OOS-Sharpe: nur nach einem echten Walk-Forward. Ein Kandidat, der am
      Sanity-Check scheiterte, hat keinen OOS-Sharpe -- weder einen positiven
      noch einen negativen.
    * DSR: beobachtet, sobald das Screening abgeschlossen ist. Wer am
      Sanity-Check scheiterte, hat die DSR nicht bestanden.
    """
    ergebnis = pd.Series(np.nan, index=frame.index, dtype=float)
    if ereignis.spalte == "critic_p_umschlag":
        umschlag = pd.to_numeric(_spalte(frame, "umschlag_pro_jahr"), errors="coerce")
        da = umschlag.notna()
        ergebnis[da] = (umschlag[da] > MAX_UMSCHLAG_PRO_JAHR).astype(float)
    elif ereignis.spalte == "critic_p_oos_positiv":
        status = _spalte(frame, "screening_status")
        fenster = pd.to_numeric(_spalte(frame, "n_windows"), errors="coerce").fillna(0)
        sharpe = pd.to_numeric(_spalte(frame, "sharpe"), errors="coerce")
        da = status.notna() & (fenster > 0) & np.isfinite(sharpe)
        ergebnis[da] = (sharpe[da] > 0).astype(float)
    elif ereignis.spalte == "critic_p_dsr":
        status = _spalte(frame, "screening_status")
        da = status.notna()
        ergebnis[da] = (status[da] == SCREENING_PASSED).astype(float)
    else:
        raise ValueError(f"Unbekanntes Ereignis {ereignis.spalte!r}")
    return ergebnis


@dataclass(slots=True)
class Abrechnung:
    """Eine Wahrscheinlichkeit der Kritik gegen ihren Ausgang."""

    ereignis: Ereignis
    n: int
    ohne_prognose: int
    prognose_mittel: float = float("nan")
    haeufigkeit: float = float("nan")
    brier: float = float("nan")
    brier_basisrate: float = float("nan")
    brier_muenzwurf: float = float("nan")

    @property
    def schlaegt_basisrate(self) -> bool | None:
        """`None`, solange es nichts abzurechnen gibt -- nicht `False`."""
        if self.n == 0:
            return None
        return self.brier < self.brier_basisrate


def _beurteilt(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[_spalte(frame, "critic_recommendation").notna()]


def abrechnen(frame: pd.DataFrame) -> list[Abrechnung]:
    """Jede Wahrscheinlichkeit der Kritik gegen das, was die Pipeline mass."""
    beurteilt = _beurteilt(frame)
    ergebnisse = []
    for ereignis in EREIGNISSE:
        y = ausgang(beurteilt, ereignis)
        p = pd.to_numeric(_spalte(beurteilt, ereignis.spalte), errors="coerce")
        beobachtet = y.notna()
        paare = beobachtet & p.notna()
        zeile = Abrechnung(
            ereignis=ereignis,
            n=int(paare.sum()),
            ohne_prognose=int((beobachtet & p.isna()).sum()),
        )
        if zeile.n:
            yy = y[paare].to_numpy()
            pp = p[paare].to_numpy()
            rate = float(yy.mean())
            zeile.prognose_mittel = float(pp.mean())
            zeile.haeufigkeit = rate
            zeile.brier = brier(pp, yy)
            zeile.brier_basisrate = brier(np.full_like(yy, rate), yy)
            zeile.brier_muenzwurf = brier(np.full_like(yy, 0.5), yy)
        ergebnisse.append(zeile)
    return ergebnisse


#: Die Flags der Kritik, wie sie in der Registry stehen.
FLAGS = (
    ("critic_magic_constants", "magische Preiskonstanten"),
    ("critic_unrealistic_turnover", "unrealistischer Umsatz"),
    ("critic_excess_dof", "zu viele Freiheitsgrade"),
    ("critic_rationale_mismatch", "Begruendung passt nicht zum Code"),
)


@dataclass(slots=True)
class Altbestand:
    """Was sich an den Urteilen vor ADR-080 noch messen laesst."""

    urteile: int = 0
    ablehnungen: int = 0
    durchgelassen_gescreent: int = 0
    durchgelassen_bestanden: int = 0
    flags: dict[str, int] = field(default_factory=dict)
    umschlag_gemessen: int = 0
    umschlag_ueber_budget: int = 0
    flag_erkannt: int = 0
    flag_uebersehen: int = 0
    flag_fehlalarm: int = 0
    umschlag_quellen: list[str] = field(default_factory=list)
    risiko_n: int = 0
    risiko_min: float = float("nan")
    risiko_max: float = float("nan")
    risiko_spearman: float = float("nan")


def altbestand(frame: pd.DataFrame) -> Altbestand:
    """Empfehlung gegen Ausgang, Umschlags-Flag gegen Messung.

    `overfitting_risk` wird nur beschrieben, nicht abgerechnet: zu einer
    Skala ohne Ereignis gibt es keinen Ausgang. Die Rangkorrelation mit dem
    OOS-Sharpe steht dabei, weil sie die einzige Frage ist, die sich an eine
    solche Zahl ueberhaupt stellen laesst -- ordnet sie die Kandidaten?
    """
    beurteilt = _beurteilt(frame)
    ergebnis = Altbestand(urteile=len(beurteilt))
    if beurteilt.empty:
        return ergebnis

    empfehlung = _spalte(beurteilt, "critic_recommendation")
    status = _spalte(beurteilt, "screening_status")
    ergebnis.ablehnungen = int((empfehlung == "reject").sum())
    durch = empfehlung == "proceed"
    ergebnis.durchgelassen_gescreent = int((durch & status.notna()).sum())
    ergebnis.durchgelassen_bestanden = int((durch & (status == SCREENING_PASSED)).sum())

    for spalte, text in FLAGS:
        ergebnis.flags[text] = int(_gesetzt(_spalte(beurteilt, spalte)).sum())

    umschlag = pd.to_numeric(_spalte(beurteilt, "umschlag_pro_jahr"), errors="coerce")
    gemessen = umschlag.notna()
    ueber = gemessen & (umschlag > MAX_UMSCHLAG_PRO_JAHR)
    flag = _gesetzt(_spalte(beurteilt, "critic_unrealistic_turnover"))
    ergebnis.umschlag_gemessen = int(gemessen.sum())
    ergebnis.umschlag_ueber_budget = int(ueber.sum())
    ergebnis.flag_erkannt = int((ueber & flag).sum())
    ergebnis.flag_uebersehen = int((ueber & ~flag).sum())
    ergebnis.flag_fehlalarm = int((gemessen & ~ueber & flag).sum())
    quellen = _spalte(beurteilt, "umschlag_quelle")
    ergebnis.umschlag_quellen = sorted({str(q) for q in quellen[gemessen] if q})

    risiko = pd.to_numeric(_spalte(beurteilt, "critic_overfitting_risk"), errors="coerce")
    sharpe = pd.to_numeric(_spalte(beurteilt, "sharpe"), errors="coerce")
    beide = risiko.notna() & np.isfinite(sharpe)
    ergebnis.risiko_n = int(risiko.notna().sum())
    if ergebnis.risiko_n:
        ergebnis.risiko_min = float(risiko.min())
        ergebnis.risiko_max = float(risiko.max())
    if int(beide.sum()) >= 3 and risiko[beide].nunique() > 1:
        from scipy import stats

        rho, _ = stats.spearmanr(risiko[beide], sharpe[beide])
        ergebnis.risiko_spearman = float(rho)
    return ergebnis


def _zahl(wert: float, format_: str = ".3f") -> str:
    return "-" if not np.isfinite(wert) else format(wert, format_)


def bericht(frame: pd.DataFrame) -> str:
    """Die Abrechnung als Text fuer `qt trials --kritik`."""
    zeilen = ["Kritik-Abrechnung (ADR-080)", ""]
    zeilen.append("Wahrscheinlichkeiten gegen Ausgang, Brier-Score (niedriger ist besser):")
    kopf = (
        f"  {'Ereignis':<24} {'n':>3} {'ohne Prognose':>14} {'Prognose':>9} "
        f"{'eingetreten':>12} {'Brier':>7} {'Basisrate':>10} {'Muenzwurf':>10}"
    )
    zeilen.append(kopf)
    abrechnung = abrechnen(frame)
    for a in abrechnung:
        zeilen.append(
            f"  {a.ereignis.text:<24} {a.n:>3} {a.ohne_prognose:>14} "
            f"{_zahl(a.prognose_mittel, '.2f'):>9} {_zahl(a.haeufigkeit, '.0%'):>12} "
            f"{_zahl(a.brier):>7} {_zahl(a.brier_basisrate):>10} "
            f"{_zahl(a.brier_muenzwurf):>10}"
        )
    if not any(a.n for a in abrechnung):
        zeilen.append(
            "  Noch nichts abzurechnen: keine Kritik mit Wahrscheinlichkeit und "
            "beobachtetem Ausgang."
        )
    else:
        for a in abrechnung:
            if a.n:
                urteil = "schlaegt" if a.schlaegt_basisrate else "schlaegt NICHT"
                zeilen.append(f"  {a.ereignis.text}: die Kritik {urteil} die Basisrate.")

    alt = altbestand(frame)
    zeilen += ["", "Altbestand, soweit er Ereignisse beruehrt:"]
    zeilen.append(
        f"  Urteile {alt.urteile}, davon abgelehnt {alt.ablehnungen}; "
        f"durchgelassen und gescreent {alt.durchgelassen_gescreent}, "
        f"davon bestanden {alt.durchgelassen_bestanden}"
    )
    zeilen.append(
        "  Gesetzte Flags: "
        + ", ".join(f"{text} {anzahl}" for text, anzahl in alt.flags.items())
    )
    if alt.umschlag_gemessen:
        zeilen.append(
            f"  Umschlags-Flag gegen Messung ({alt.umschlag_gemessen} gemessen): "
            f"ueber Budget {alt.umschlag_ueber_budget}, davon erkannt "
            f"{alt.flag_erkannt}, uebersehen {alt.flag_uebersehen}; "
            f"Fehlalarme {alt.flag_fehlalarm}"
        )
        for quelle in alt.umschlag_quellen:
            zeilen.append(f"    Quelle: {quelle}")
    else:
        zeilen.append(
            "  Umschlag noch nicht gemessen -- `qt trials --umschlag-nachmessen`."
        )
    if alt.risiko_n:
        zeilen.append(
            f"  overfitting_risk: {alt.risiko_n} Werte zwischen "
            f"{_zahl(alt.risiko_min, '.2f')} und {_zahl(alt.risiko_max, '.2f')}, "
            f"Rangkorrelation mit dem OOS-Sharpe {_zahl(alt.risiko_spearman, '+.2f')}."
        )
        zeilen.append(
            "    Nicht abrechenbar: zu einer Skala ohne Ereignis gibt es keinen Ausgang."
        )
    return "\n".join(zeilen)


def umschlag_messen(
    code: str,
    class_name: str,
    bars: dict[str, list[Bar]],
    timeframe: str,
) -> float:
    """Umschlag/EK/Jahr eines gespeicherten Kandidaten.

    Der Code laeuft durch dieselben zwei Schloesser wie im Loop: erst die
    Whitelist, dann das Laden (ADR-029). Gemessen wird mit
    Positionsgroessen-Schicht, weil der Loop jeden Kandidaten so rechnet
    (ADR-069), und mit den Kosten des Gates.
    """
    from qt.backtest.engine import run_backtest
    from qt.core.config import BacktestConfig, costs_for_symbols
    from qt.research import sandbox
    from qt.research.gate import umschlag_pro_jahr
    from qt.research.groesse import mit_groessenschicht
    from qt.research.loop import BRUTTOGRENZE

    report = sandbox.check(code)
    if not report.ok:
        grund = report.reasons[0] if report.reasons else "abgelehnt"
        raise ValueError(f"Sandbox: {grund}")
    klasse = mit_groessenschicht(sandbox.load_strategy_class(code, class_name), BRUTTOGRENZE)
    symbole = sorted(bars)
    cfg = BacktestConfig(costs_by_symbol=costs_for_symbols(symbole))
    lauf = run_backtest(klasse(symbole, timeframe), bars, cfg)
    return umschlag_pro_jahr(lauf.equity)


def umschlag_nachmessen(
    registry,
    bars: dict[str, list[Bar]],
    timeframe: str = UMSCHLAG_TIMEFRAME,
    quelle: str = UMSCHLAG_QUELLE,
    echo: Callable[[str], None] | None = None,
) -> dict[str, float | str]:
    """Fehlenden Umschlag aller von der Kritik beurteilten Kandidaten messen.

    Idempotent: wer schon einen Umschlag hat, wird nicht noch einmal
    gemessen. Rueckgabe je Kandidaten-ID der Messwert oder der Grund, warum
    es keinen gibt.
    """
    frame = registry.history()
    if frame.empty:
        return {}
    offen = frame[
        _spalte(frame, "critic_recommendation").notna()
        & pd.to_numeric(_spalte(frame, "umschlag_pro_jahr"), errors="coerce").isna()
        & _spalte(frame, "code").notna()
    ]
    ergebnis: dict[str, float | str] = {}
    for zeile in offen.itertuples():
        try:
            wert = umschlag_messen(zeile.code, zeile.class_name, bars, timeframe)
        except Exception as exc:  # noqa: BLE001 -- ein Kandidat darf scheitern
            ergebnis[zeile.id] = f"{type(exc).__name__}: {exc}"
            if echo is not None:
                echo(f"  {zeile.class_name}: nicht messbar ({type(exc).__name__})")
            continue
        registry.record_umschlag(zeile.id, wert, quelle)
        ergebnis[zeile.id] = wert
        if echo is not None:
            echo(f"  {zeile.class_name}: {wert:.1f}x")
    return ergebnis
