"""Gate 1 als ausfuehrbare Pruefung statt als Absatz in einem Dokument.

`docs/ZIEL.md` nennt sechs Bedingungen, die ein Kandidat vor dem 2027-03-01
gleichzeitig erfuellen muss. Bis hierher standen sie als Prosa da, und die
Werkzeuge lagen als sechs einzelne Befehle daneben, deren Reihenfolge niemand
erzwang und deren Ergebnis niemand zusammenfuehrte.

Das ist genau die Bauform, an der dieses Projekt schon zweimal gescheitert
ist: eine Zusage, die kein Programm nachhaelt. Das Paper-Konto stand wochenlang
still, waehrend die ROADMAP behauptete, es laufe (ADR-051); ein Dokument
widersprach sich in zwei aufeinanderfolgenden Absaetzen (ADR-052). Ein
Kriterium, das nur ein Mensch anwendet, ist ein Vorsatz.

Die Schwellen stehen deshalb als Konstanten hier und **nicht** als
Kommandozeilenoptionen. Es gibt kein `--min-sharpe`. Wer die Latte senken
will, aendert eine Konstante, und das erscheint in einem Diff, in der
Historie und in einer Ueberpruefung. Das ist der ganze Zweck: nach einem
verfehlten Kriterium ist die Versuchung, den Massstab zu korrigieren, am
groessten -- ADR-055 haelt fest, wie nah dieses Projekt daran schon einmal war.

Reihenfolge nach Kosten
-----------------------
Die Pruefungen laufen von billig nach teuer, und die erste, die durchfaellt,
beendet den Lauf:

1. **Aktivitaet und Umschlag** -- ein Backtest, Sekunden. Faellt hier durch,
   wer gar nicht handelt und wer die Kosten nicht tragen kann, die er
   nachweislich zahlt (ADR-056).
2. **Walk-Forward und DSR** -- der teure Teil, aber ohne ihn gibt es keine
   Zahl, ueber die zu reden waere.
3. **Permutation** -- 200 Ziehungen, wenige Sekunden auf 1d.
4. **Querschnitt und Anlageklassen** -- alle Maerkte des Stores.

Der Abbruch ist nicht nur Sparsamkeit. **Jedes abgeschlossene Screening
erhoeht den DSR-Nenner dauerhaft** (ADR-032), also fuer jeden kuenftigen
Kandidaten. Ein Lauf, der nach dem Umschlagtest abbricht, hat die Daten nicht
befragt und zaehlt nicht mit; einer, der bis zum Walk-Forward kommt, schon.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from qt.core.types import Bar

# --------------------------------------------------------------------------
# Die vorab festgelegten Schwellen. Herkunft je Zeile, damit niemand eine
# davon fuer eine Geschmacksfrage haelt.
# --------------------------------------------------------------------------

# Aus n_eff = 5,1 ueber 7,7 Jahre: darunter ist ein Sharpe nicht von null zu
# unterscheiden (ADR-061). Keine Zielvorgabe, eine Nachweisgrenze.
#
# **Diese Zeile ist von 0,41 auf 0,33 gesenkt worden, und das sieht aus wie
# das Verschieben des Torpfostens.** Sie ist es nicht, und der Unterschied
# haengt an einer Reihenfolge, die nachpruefbar ist:
#
#   * Die Regel stand vorher. docs/ZIEL.md, Phase C.1: "Mindest-Sharpe aus
#     Phase A ableiten und vorab als ADR festschreiben." Die Schwelle ist
#     eine Funktion von n_eff, keine freie Zahl.
#   * Gesenkt hat sie die **Datenlage**, nicht ein verfehltes Ergebnis. Zwoelf
#     Reihen aus Anlageklassen, die der Bestand nicht hatte, druecken die
#     mittlere Korrelation von 0,26 auf 0,18 und heben n_eff von 3,4 auf 5,1
#     (ADR-061). Der Standardfehler ist wirklich kleiner geworden.
#   * Kein Kandidat gewinnt dadurch. `macross` steht bei 0,25 und scheitert
#     ohnehin am Umschlagbudget; `crossmom` bei -0,24. Die Senkung rettet
#     nichts, was vorher gescheitert ist -- sie wuerde es sonst auch nicht
#     tun duerfen.
#
# Wer sie ohne eine dieser drei Bedingungen wieder anfasst, senkt die Latte
# und nicht die Nachweisgrenze.
MIN_SHARPE = 0.33

# Ueblich, und seit ADR-005 unveraendert.
MIN_DSR = 0.95

# Anteil der gewuerfelten Fassungen, den die echte Strategie schlagen muss
# (ADR-054).
MIN_PERZENTIL = 0.95

# Aus 0,10 Sharpe Kostenspielraum bei rund 44 % Jahresvolatilitaet und 65 bps
# je Ausfuehrung (ADR-056). Umschlag relativ zum jeweiligen Eigenkapital.
MAX_UMSCHLAG_PRO_JAHR = 7.0

# Ein Effekt in nur einer Anlageklasse ist deren Beta und nicht der Edge.
MIN_ANLAGEKLASSEN = 2

# Ergaenzt am 2026-09-02, nachdem der erste Lauf ueber die Bibliothek zeigte,
# dass `orderflow` und `timesfm` das Umschlagbudget mit 0,1x und 0,0x
# "bestanden" -- weil beiden die Datenquelle fehlt und sie schlicht nicht
# handeln. Ein Kriterium, das sich durch Nichtstun erfuellen laesst, ist
# keines.
#
# Die Schwelle ist keine Meinung ueber gute Strategien, sondern die Grenze,
# unterhalb derer die spaeteren Pruefungen nichts mehr messen koennen: die
# Permutationskontrolle vertauscht Episodenlaengen, und bei fuenf Episoden
# gibt es kaum etwas zu vertauschen.
MIN_FILLS = 20

# Unter 1d frisst die Ausfuehrung den Edge (ADR-047, ADR-056).
ERLAUBTE_TIMEFRAMES = ("1d", "2d", "3d", "1w")


@dataclass(slots=True)
class Kriterium:
    """Ein einzelnes Kriterium mit seinem gemessenen Wert."""

    name: str
    bestanden: bool
    wert: str
    schwelle: str

    def zeile(self) -> str:
        zeichen = "ja  " if self.bestanden else "NEIN"
        return f"  {zeichen}  {self.name:<24} {self.wert:>18}   verlangt {self.schwelle}"


@dataclass(slots=True)
class GateResult:
    """Das Gesamturteil. `bestanden` nur, wenn jedes Kriterium geprueft wurde."""

    strategie: str
    timeframe: str
    kriterien: list[Kriterium] = field(default_factory=list)
    abgebrochen_nach: str = ""
    versuchszaehler: int = 0
    screening_gezaehlt: bool = False

    @property
    def bestanden(self) -> bool:
        return bool(self.kriterien) and all(k.bestanden for k in self.kriterien)

    @property
    def offen(self) -> bool:
        """Wurde der Lauf abgebrochen, bevor alle Kriterien geprueft waren?"""
        return bool(self.abgebrochen_nach)

    def table(self) -> str:
        zeilen = [
            f"  Gate 1 -- {self.strategie} @ {self.timeframe}",
            "  " + "-" * 74,
        ]
        zeilen += [k.zeile() for k in self.kriterien]
        zeilen.append("  " + "-" * 74)
        if self.offen:
            zeilen.append(
                f"  ABGEBROCHEN nach '{self.abgebrochen_nach}' -- die teureren "
                f"Pruefungen liefen nicht."
            )
        zeilen.append(
            f"  Urteil: {'BESTANDEN' if self.bestanden else 'NICHT BESTANDEN'}"
        )
        if self.screening_gezaehlt:
            # Bis ADR-061 stand hier "Dieser Lauf zaehlt als Versuch. Zaehler
            # danach: N" -- und der Zaehler stand hinterher unveraendert da.
            # Das Gate schreibt bewusst nichts in die Registry (ADR-057); die
            # DSR rechnet nur **so**, als zaehlte der Lauf mit. Die Meldung
            # behauptete damit eine dauerhafte Nebenwirkung, die es nicht gab
            # -- bei einer Zahl, die das ganze Overfitting-Budget traegt.
            zeilen.append(
                f"  Der Walk-Forward lief: die DSR rechnet gegen "
                f"{self.versuchszaehler} Versuche, also einschliesslich "
                f"dieses Laufs."
            )
            zeilen.append(
                "  **Eingebucht wird er dadurch nicht** -- das Gate schreibt "
                "nicht in die Registry (ADR-057).\n"
                "  Wer diesen Lauf als Versuch fuehren will, tut das von Hand: "
                "qt trials --backfill"
            )
        else:
            zeilen.append(
                "  Zaehlt NICHT als Versuch -- der Walk-Forward lief nicht "
                "(ADR-032)."
            )
        return "\n".join(zeilen)


def umschlag_pro_jahr(equity, turnover_spalte: str = "turnover") -> float:
    """Umschlag relativ zum jeweiligen Eigenkapital, auf ein Jahr gerechnet.

    Der kumulierte Umschlag in Dollar taugt dafuer nicht: er waechst mit dem
    Konto, und ein Konto, das sich verzwanzigfacht, schlaegt am Ende in einem
    Trade mehr um als am Anfang im ganzen Jahr. Was die Kosten bestimmt, ist
    der Umschlag **im Verhaeltnis zum Kapital, das gerade da ist** (ADR-056).
    """
    if equity is None or len(equity) < 2 or turnover_spalte not in equity:
        return float("nan")
    ts = equity["ts"]
    jahre = (ts.iloc[-1] - ts.iloc[0]).total_seconds() / (365.25 * 86400)
    if jahre <= 0:
        return float("nan")
    zuwachs = equity[turnover_spalte].diff().fillna(0.0)
    anteil = (zuwachs / equity["equity"]).sum()
    return float(anteil / jahre)


def _anlageklasse(symbol: str) -> str:
    """Grobe Zuordnung: alles mit Slash ist Krypto, der Rest kommt aus dem Korb.

    Bewusst grob und bewusst hier: der Test fragt nur, ob ein Effekt mehr als
    eine Klasse beruehrt. Eine feinere Einteilung wuerde die Huerde senken,
    weil sich jede Klasse weiter unterteilen laesst, bis zwei herauskommen.
    """
    if "/" in symbol:
        return "Krypto"
    from qt.data.tiingo import BASKET

    klasse, _ = BASKET.get(symbol, ("Unbekannt", ""))
    return klasse


def run_gate(
    strategy_cls,
    maerkte: dict[str, list[Bar]],
    timeframe: str,
    trial_count: int,
    *,
    haupt_symbole: list[str] | None = None,
    train_bars: int = 1000,
    test_bars: int = 250,
    embargo_bars: int = 20,
    draws: int = 200,
    seed: int = 0,
    on_stage: Callable[[str], None] | None = None,
) -> GateResult:
    """Alle sechs Kriterien in Kostenreihenfolge, Abbruch beim ersten Nein.

    `maerkte` ist der ganze Store; `haupt_symbole` sind die Maerkte, auf denen
    Umschlag, Walk-Forward und Permutation laufen. Ohne Angabe ist das
    BTC/USD, sofern vorhanden -- der Markt, gegen den alle bisherigen Zahlen
    dieses Projekts gemessen wurden.

    `trial_count` schliesst diesen Kandidaten ein.
    """
    from qt.backtest.engine import run_backtest
    from qt.core.config import BacktestConfig, costs_for_symbols
    from qt.research.placebo import cross_market_control, permutation_control
    from qt.research.screening import screen_candidate

    def melde(text: str) -> None:
        if on_stage is not None:
            on_stage(text)

    ergebnis = GateResult(
        strategie=getattr(strategy_cls, "name", strategy_cls.__name__),
        timeframe=timeframe,
        versuchszaehler=trial_count,
    )

    # 0. Zeitebene. Kein Messwert, eine Vorbedingung -- und deshalb kein
    #    Kriterium in der Tabelle, sondern ein Abbruch mit Begruendung.
    if timeframe not in ERLAUBTE_TIMEFRAMES:
        ergebnis.kriterien.append(
            Kriterium(
                "Zeitebene",
                False,
                timeframe,
                " oder ".join(ERLAUBTE_TIMEFRAMES),
            )
        )
        ergebnis.abgebrochen_nach = "Zeitebene"
        return ergebnis

    if haupt_symbole is None:
        # Eine Querschnittsstrategie auf einem Markt laufen zu lassen waere
        # sinnlos: sie bildet eine Rangfolge, und eine Rangfolge ueber einen
        # Namen ist keine. Sie bekommt den ganzen Store (ADR-058).
        from qt.strategy.cross_sectional import CrossSectionalStrategy

        if isinstance(strategy_cls, type) and issubclass(
            strategy_cls, CrossSectionalStrategy
        ):
            haupt_symbole = sorted(maerkte)
        else:
            haupt_symbole = (
                ["BTC/USD"] if "BTC/USD" in maerkte else [sorted(maerkte)[0]]
            )
    haupt_bars = {sym: maerkte[sym] for sym in haupt_symbole if sym in maerkte}
    if not haupt_bars:
        raise ValueError(f"Keine Bars fuer {haupt_symbole} im uebergebenen Store.")

    cfg = BacktestConfig(costs_by_symbol=costs_for_symbols(sorted(maerkte)))

    # 1. Handelt die Strategie ueberhaupt? Vor dem Umschlag, weil ein
    #    Umschlag von 0,0x sonst wie ein bestandenes Kriterium aussieht.
    melde("Aktivitaet")
    lauf = run_backtest(strategy_cls(haupt_symbole, timeframe), haupt_bars, cfg)
    n_fills = len(lauf.fills)
    ergebnis.kriterien.append(
        Kriterium(
            "Ausfuehrungen",
            n_fills >= MIN_FILLS,
            f"{n_fills}",
            f">= {MIN_FILLS}",
        )
    )
    if not ergebnis.kriterien[-1].bestanden:
        ergebnis.abgebrochen_nach = "Aktivitaet"
        return ergebnis

    # 2. Umschlag -- derselbe Backtest, andere Frage.
    melde("Umschlag")
    umschlag = umschlag_pro_jahr(lauf.equity)
    ergebnis.kriterien.append(
        Kriterium(
            "Umschlag/EK/Jahr",
            bool(np.isfinite(umschlag) and umschlag <= MAX_UMSCHLAG_PRO_JAHR),
            f"{umschlag:.1f}x",
            f"<= {MAX_UMSCHLAG_PRO_JAHR:.0f}x",
        )
    )
    if not ergebnis.kriterien[-1].bestanden:
        ergebnis.abgebrochen_nach = "Umschlag"
        return ergebnis

    # 3. Walk-Forward und DSR. Ab hier zaehlt der Lauf als Versuch.
    melde("Walk-Forward und DSR")
    screen = screen_candidate(
        strategy_cls,
        haupt_bars,
        haupt_symbole,
        timeframe,
        trial_count,
        train_bars=train_bars,
        test_bars=test_bars,
        embargo_bars=embargo_bars,
        dsr_threshold=MIN_DSR,
        cfg=cfg,
    )
    ergebnis.screening_gezaehlt = True
    ergebnis.kriterien.append(
        Kriterium(
            "OOS-Sharpe",
            bool(np.isfinite(screen.sharpe) and screen.sharpe >= MIN_SHARPE),
            f"{screen.sharpe:+.2f}",
            f">= {MIN_SHARPE:.2f}",
        )
    )
    dsr = screen.dsr if screen.dsr is not None else float("nan")
    ergebnis.kriterien.append(
        Kriterium(
            f"DSR gegen {trial_count} Versuche",
            bool(np.isfinite(dsr) and dsr >= MIN_DSR),
            f"{dsr:.3f}",
            f">= {MIN_DSR:.2f}",
        )
    )
    if not all(k.bestanden for k in ergebnis.kriterien[-2:]):
        ergebnis.abgebrochen_nach = "Walk-Forward/DSR"
        return ergebnis

    # 4. Permutation.
    melde("Permutation")
    perm = permutation_control(
        lambda: strategy_cls(haupt_symbole, timeframe),
        haupt_bars,
        train_bars=train_bars,
        test_bars=test_bars,
        embargo_bars=embargo_bars,
        draws=draws,
        seed=seed,
        cfg=cfg,
    )
    ergebnis.kriterien.append(
        Kriterium(
            "Permutation-Perzentil",
            perm.bestanden(MIN_PERZENTIL),
            f"{perm.perzentil:.1%}",
            f">= {MIN_PERZENTIL:.0%}",
        )
    )
    if not ergebnis.kriterien[-1].bestanden:
        ergebnis.abgebrochen_nach = "Permutation"
        return ergebnis

    # 5. Querschnitt und Anlageklassen -- eine Rechnung, zwei Kriterien.
    melde("Querschnitt")
    quer = cross_market_control(
        strategy_cls,
        maerkte,
        timeframe,
        train_bars=train_bars,
        test_bars=test_bars,
        embargo_bars=embargo_bars,
        cfg=cfg,
    )
    ergebnis.kriterien.append(
        Kriterium(
            "Median-Sharpe quer",
            bool(np.isfinite(quer.median_sharpe) and quer.median_sharpe > 0),
            f"{quer.median_sharpe:+.2f}",
            "> 0",
        )
    )

    klassen = {
        _anlageklasse(lauf_.symbol)
        for lauf_ in quer.getestet
        if np.isfinite(lauf_.sharpe) and lauf_.sharpe >= MIN_SHARPE
    }
    ergebnis.kriterien.append(
        Kriterium(
            "Anlageklassen ueber Latte",
            len(klassen) >= MIN_ANLAGEKLASSEN,
            f"{len(klassen)} ({', '.join(sorted(klassen)) or 'keine'})",
            f">= {MIN_ANLAGEKLASSEN}",
        )
    )
    return ergebnis
