"""Risk-Engine -- die Instanz, die entscheidet.

Der Allokator schlaegt vor, diese Datei entscheidet (ARCHITECTURE.md,
ADR-002). Ab Phase 3 sitzt an der Allokator-Stelle ein LLM: die kreative,
unzuverlaessige Komponente. Diese Datei ist ihr Gegenstueck -- dumm,
deterministisch, ueberpruefbar. Ein halluzinierter oder boesartiger
Gewichtsvektor darf hier nicht durchkommen.

Zwei Grundregeln, aus denen sich fast alles Weitere ergibt:

1. **Die Engine darf nur verkleinern, nie vergroessern** -- mit der einen
   bewusst begrenzten Ausnahme des Vol-Targetings, das eine ruhige Position
   hochskaliert (und dabei hart gedeckelt ist). Eine Risikoschicht, die von
   sich aus eine Position eroeffnet, die niemand vorgeschlagen hat, ist
   keine Risikoschicht mehr, sondern eine zweite Strategie.
2. **Jeder Eingriff erzeugt eine Begruendung.** Ein Risikoeingriff, den
   niemand sieht, wird nie untersucht -- und ein staendig greifender Cap ist
   der Hinweis darauf, dass die Strategie falsch dimensioniert ist.

Reihenfolge der Limits (das ist eine echte Designentscheidung, nicht
Geschmack):

    0. Plausibilisierung  -- `nan`/`inf`/absurde Betraege raus
    1. Drawdown-Kill-Switch
    2. Vol-Targeting      (der einzige Schritt, der hochskalieren kann)
    3. Positionszahl-Cap
    4. Max-Gewicht je Symbol
    5. Max-Brutto-Exposure

Begruendung der Reihenfolge:

* **Plausibilisierung zuerst**, weil jeder folgende Schritt rechnet. Ein
  einziges `inf` im Vektor macht die Brutto-Summe zu `inf` und damit den
  Skalierungsfaktor zu `nan` -- der Cap, der schuetzen soll, wuerde selbst
  zur Sicherheitsluecke.
* **Kill-Switch vor allen Formungsschritten**, obwohl er das Ergebnis
  komplett ueberschreibt. Ein Halt ist unbedingt: nichts, was davor
  gerechnet wird, kann ihn noch aendern. Umgekehrt koennte ein Fehler in
  der Vol-Rechnung verhindern, dass der Halt ueberhaupt erreicht wird.
  Die Sicherung gehoert vor die Logik, nicht dahinter.
* **Vol-Targeting vor den Caps**, weil es als einziger Schritt Gewichte
  *vergroessern* kann. Danach gecappt zu werden ist der Sinn der Sache;
  liefe es hinterher, koennte es einen frisch gesetzten Cap wieder
  durchbrechen.
* **Brutto-Exposure zuletzt**, weil jeder vorherige Schritt die Summe
  veraendert. Es ist die einzige Grenze fuer das Konto als Ganzes, also
  muss sie beim Verlassen der Funktion gelten. Sie skaliert nur nach unten
  und kann deshalb keinen der vorher gesetzten Caps verletzen.

Bewusst *nicht* eingebaut: eine erzwungene Mindest-Diversifikation. Sie
liesse sich nur herstellen, indem die Engine Positionen eroeffnet, die der
Allokator nicht vorgeschlagen hat -- siehe Grundregel 1. Die
diversifikations-nahe Grenze, die ohne diesen Bruch auskommt, ist der Cap
auf die Anzahl gleichzeitiger Positionen: er kann nur streichen.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
from pydantic import BaseModel, Field

from qt.core.types import bars_per_year
from qt.features import ta
from qt.portfolio.base import PortfolioWeights, RiskLimits, RiskState

# Fliesskomma-Toleranz am Drawdown-Limit, siehe _check_kill_switch.
_DRAWDOWN_EPS = 1e-9


class RiskConfig(BaseModel):
    """Grenzen der Risk-Engine. Defaults bewusst eng.

    Wie beim Kostenmodell gilt: lieber zu vorsichtig als zu grosszuegig.
    Ein zu enger Cap kostet Rendite und faellt im Report auf, ein zu weiter
    faellt erst auf dem Kontoauszug auf.
    """

    target_vol: float = Field(
        default=0.20,
        gt=0,
        description="Angestrebte annualisierte Volatilitaet je Position.",
    )
    max_vol_scale: float = Field(
        default=1.5,
        ge=1.0,
        description="Obergrenze des Vol-Targeting-Faktors. Pflicht: in einem "
        "sehr ruhigen Markt ergaebe target_vol/vol sonst zweistelligen Hebel "
        "-- und ruhige Maerkte sind genau die, die abrupt enden.",
    )
    vol_lookback: int = Field(
        default=96,
        ge=2,
        description="Bars fuer die realisierte Vola (siehe realised_vol_map).",
    )
    max_weight_per_symbol: float = Field(
        default=0.25,
        gt=0,
        description="Betragsgrenze je Symbol, als Bruchteil des Eigenkapitals.",
    )
    max_gross_exposure: float = Field(
        default=1.0,
        gt=0,
        description="Summe der Betraege ueber alle Symbole. 1.0 = kein Hebel.",
    )
    max_drawdown: float = Field(
        default=0.20,
        gt=0,
        lt=1,
        description="Betrag des Drawdowns, ab dem der Kill-Switch ausloest. "
        "Zuruecksetzen ausschliesslich manuell ueber reset().",
    )
    max_positions: int | None = Field(
        default=None,
        ge=1,
        description="Max. Anzahl gleichzeitig offener Positionen. None = aus. "
        "Behalten werden die betragsgroessten Vorschlaege.",
    )
    max_input_weight: float = Field(
        default=1_000.0,
        gt=0,
        description="Betrag, ab dem ein Vorschlag nicht mehr beschnitten, "
        "sondern verworfen wird. Ein Gewicht von 1e9 ist kein zu grosser "
        "Vorschlag, sondern ein Fehler oder ein Angriff -- und Fehler werden "
        "zu 0, nicht zum Cap.",
    )


class RiskEngine(RiskLimits):
    """Beschneidet Portfolio-Gewichte nach festen, nachvollziehbaren Regeln.

    Die Engine haelt genau einen Zustand: ob der Kill-Switch ausgeloest hat.
    Er ist bewusst klebrig -- siehe `apply`.
    """

    def __init__(self, cfg: RiskConfig | None = None) -> None:
        self.cfg = cfg or RiskConfig()
        self._halted = False

    @property
    def halted(self) -> bool:
        """Ob der Kill-Switch aktiv ist."""
        return self._halted

    def reset(self) -> None:
        """Kill-Switch loesen. Der einzige Weg zurueck fuer einen Menschen.

        Es gibt bewusst keine automatische Entsperrung bei erholter Equity:
        ein Kill-Switch, der sich selbst zurueckstellt, ist keiner. Nach
        einem Halt gehoert ein Mensch zwischen Ausloesung und Wiederanlauf,
        der zuerst klaert, warum das Konto ueberhaupt so weit gefallen ist.
        """
        self._halted = False

    def restore_halted(self, halted: bool) -> None:
        """Zustand aus einem persistierten Konto uebernehmen.

        Anders als `reset()` ist das kein Entscheidungsakt, sondern reines
        Wiederherstellen: ein Paper-Tick baut die Engine bei jedem Aufruf neu
        auf (siehe `qt.live.runner`), und ein `RiskEngine`, der dabei jedes
        Mal unbehaltet startet, wuerde einen ausgeloesten Kill-Switch beim
        naechsten Tick stillschweigend vergessen -- genau die Sorte Fehler,
        die den Sinn eines Kill-Switches aufhebt.
        """
        self._halted = bool(halted)

    def apply(
        self, weights: PortfolioWeights, state: RiskState
    ) -> tuple[PortfolioWeights, list[str]]:
        """Beschnittene Gewichte plus Begruendungen der Eingriffe.

        Der Rueckgabe-Dict enthaelt **jedes** Symbol des Vorschlags, auch
        die auf 0 gesetzten. Ein fehlender Schluessel liesse sich als "keine
        Meinung, Position stehen lassen" lesen -- eine explizite 0 heisst
        unmissverstaendlich "schliessen". Genau darauf kommt es beim Halt an.

        Routine-Skalierung durch das Vol-Targeting innerhalb des Caps
        erzeugt keine Begruendung: sie ist der Normalbetrieb und wuerde den
        Report bei jedem Bar und jedem Symbol fluten. Begruendet wird, was
        eine Grenze beruehrt.
        """
        reasons: list[str] = []
        out, sane_reasons = self._sanitise(weights)
        reasons.extend(sane_reasons)

        halt_reason = self._check_kill_switch(state)
        if halt_reason is not None:
            reasons.append(halt_reason)
            # Auch dem Aufrufer sichtbar machen: der Halt ueberlebt so den
            # Weg in Report und Equity-Kurve, nicht nur im Engine-Objekt.
            state.halted = True
            return dict.fromkeys(out, 0.0), reasons

        out = self._vol_target(out, state, reasons)
        out = self._cap_position_count(out, reasons)
        out = self._cap_per_symbol(out, reasons)
        out = self._cap_gross(out, reasons)
        return out, reasons

    # ------------------------------------------------------------------
    # Schritt 0: Plausibilisierung
    # ------------------------------------------------------------------
    def _sanitise(
        self, weights: PortfolioWeights
    ) -> tuple[PortfolioWeights, list[str]]:
        """Alles rauswerfen, mit dem man nicht rechnen kann.

        `nan` heisst im Rest des Systems "keine Meinung" (siehe
        `portfolio.base.combine`) und wird still zu 0. `inf` und absurde
        Betraege sind dagegen ein Befund und werden laut protokolliert --
        genau so sieht der Output eines LLM aus, das entgleist ist.
        """
        out: PortfolioWeights = {}
        reasons: list[str] = []
        for symbol, raw in weights.items():
            try:
                weight = float(raw)
            except (TypeError, ValueError):
                # Der Typvertrag sagt float. Ein LLM-Output, der durch eine
                # lueckenhafte Validierung gerutscht ist, sagt vielleicht
                # None oder "stark long" -- das darf hier keine Exception
                # werden, sondern muss eine flache Position werden.
                out[symbol] = 0.0
                reasons.append(
                    f"{symbol}: Gewicht {raw!r} ist keine Zahl -- verworfen (0.0)"
                )
                continue
            if math.isnan(weight):
                out[symbol] = 0.0
                continue
            if math.isinf(weight):
                out[symbol] = 0.0
                reasons.append(
                    f"{symbol}: Gewicht ist unendlich -- verworfen (0.0)"
                )
                continue
            if abs(weight) > self.cfg.max_input_weight:
                out[symbol] = 0.0
                reasons.append(
                    f"{symbol}: Gewicht {weight:.4g} jenseits jeder Plausibilitaet "
                    f"(> {self.cfg.max_input_weight:g}) -- verworfen (0.0)"
                )
                continue
            out[symbol] = weight
        return out, reasons

    # ------------------------------------------------------------------
    # Schritt 1: Kill-Switch
    # ------------------------------------------------------------------
    def _check_kill_switch(self, state: RiskState) -> str | None:
        """Begruendung, wenn angehalten werden muss -- sonst None.

        Vier Wege in den Halt: ein frisch ueberschrittenes Drawdown-Limit,
        ein bereits gesetzter Halt (eigener oder von aussen, z.B. dem
        Live-Runner), eine Equity, die sich nicht auswerten laesst, oder
        eine nicht-positive Equity. Die beiden letzten Faelle sind Absicht:
        wer den Drawdown nicht kennt, weiss nicht, ob er noch handeln darf
        -- und handelt dann nicht.
        """
        if self._halted or state.halted:
            self._halted = True
            return "Kill-Switch aktiv -- Handel ausgesetzt bis reset()"

        if not (math.isfinite(state.equity) and math.isfinite(state.peak_equity)):
            self._halted = True
            return (
                f"Kill-Switch: Equity nicht auswertbar "
                f"(equity={state.equity}, peak={state.peak_equity}) -- Halt"
            )

        # `RiskState.drawdown` liefert bei peak_equity <= 0 eine 0.0, also
        # "kein Drawdown" -- ein ruiniertes Konto kaeme damit ungebremst
        # durch den Kill-Switch. Hier abgefangen statt sich auf die
        # Property zu verlassen: die Sicherung darf nicht davon abhaengen,
        # dass der Aufrufer saubere Zahlen liefert.
        if state.equity <= 0 or state.peak_equity <= 0:
            self._halted = True
            return (
                f"Kill-Switch: Equity nicht positiv "
                f"(equity={state.equity:.2f}, peak={state.peak_equity:.2f}) -- Halt"
            )

        drawdown = state.drawdown
        if not math.isfinite(drawdown):
            self._halted = True
            return "Kill-Switch: Drawdown nicht auswertbar -- Halt"

        # Toleranz nach oben, nicht nach unten: `80_000/100_000 - 1` ergibt in
        # Fliesskomma -0.19999999999999996, und ein Kill-Switch, der bei genau
        # erreichtem Limit wegen des letzten Bits nicht ausloest, ist ein Bug
        # mit Kontoauszug. Im Zweifel ausloesen.
        if drawdown <= -self.cfg.max_drawdown + _DRAWDOWN_EPS:
            self._halted = True
            return (
                f"Kill-Switch: Drawdown {drawdown:.2%} erreicht Limit "
                f"{-self.cfg.max_drawdown:.2%} -- alle Gewichte auf 0, "
                f"Wiederanlauf nur ueber reset()"
            )
        return None

    # ------------------------------------------------------------------
    # Schritt 2: Vol-Targeting
    # ------------------------------------------------------------------
    def _vol_target(
        self, weights: PortfolioWeights, state: RiskState, reasons: list[str]
    ) -> PortfolioWeights:
        """Position invers zur realisierten Vola des Symbols skalieren.

        Ohne diesen Schritt bedeutet dasselbe Zielgewicht in einem ruhigen
        und in einem panischen Markt voellig verschiedenes Risiko. Mit ihm
        bedeutet es beide Male ungefaehr `target_vol`.

        Bei unbekannter Vola (`nan`, siehe `RiskState.realised_vol`) wird
        *nicht* hochskaliert: unbekannt ist kein Synonym fuer niedrig.
        """
        out: PortfolioWeights = {}
        for symbol, weight in weights.items():
            if weight == 0.0:
                out[symbol] = 0.0
                continue

            vol = float(state.realised_vol.get(symbol, math.nan))
            if not math.isfinite(vol) or vol <= 0:
                out[symbol] = weight
                reasons.append(
                    f"{symbol}: Vola unbekannt -- kein Vol-Targeting, "
                    f"Gewicht unveraendert bei {weight:.4f}"
                )
                continue

            scale = self.cfg.target_vol / vol
            if scale > self.cfg.max_vol_scale:
                reasons.append(
                    f"{symbol}: Vol-Skalierung {scale:.2f}x auf "
                    f"{self.cfg.max_vol_scale:.2f}x gedeckelt "
                    f"(Vola {vol:.2%} vs. Ziel {self.cfg.target_vol:.2%})"
                )
                scale = self.cfg.max_vol_scale
            out[symbol] = weight * scale
        return out

    # ------------------------------------------------------------------
    # Schritt 3: Anzahl gleichzeitiger Positionen
    # ------------------------------------------------------------------
    def _cap_position_count(
        self, weights: PortfolioWeights, reasons: list[str]
    ) -> PortfolioWeights:
        """Nur die groessten `max_positions` Vorschlaege behalten.

        Rangfolge auf den vol-skalierten, aber noch ungecappten Betraegen:
        nach dem Symbol-Cap liegen zu viele Kandidaten exakt auf dem Cap,
        und die Auswahl waere dann faktisch alphabetisch. Bei echtem
        Gleichstand entscheidet der Symbolname -- das ist willkuerlich, aber
        deterministisch, und Determinismus ist hier wichtiger als Eleganz.
        """
        limit = self.cfg.max_positions
        if limit is None:
            return weights

        open_symbols = [s for s, w in weights.items() if w != 0.0]
        if len(open_symbols) <= limit:
            return weights

        ranked = sorted(open_symbols, key=lambda s: (-abs(weights[s]), s))
        dropped = ranked[limit:]
        out = dict(weights)
        for symbol in dropped:
            out[symbol] = 0.0
        reasons.append(
            f"Positionszahl {len(open_symbols)} > {limit} -- gestrichen: "
            f"{', '.join(sorted(dropped))}"
        )
        return out

    # ------------------------------------------------------------------
    # Schritt 4: Max-Gewicht je Symbol
    # ------------------------------------------------------------------
    def _cap_per_symbol(
        self, weights: PortfolioWeights, reasons: list[str]
    ) -> PortfolioWeights:
        """Betragsgrenze je Symbol. Das Vorzeichen bleibt erhalten."""
        cap = self.cfg.max_weight_per_symbol
        out: PortfolioWeights = {}
        for symbol, weight in weights.items():
            if abs(weight) > cap:
                out[symbol] = math.copysign(cap, weight)
                reasons.append(
                    f"{symbol}: Gewicht {weight:.4f} auf "
                    f"{out[symbol]:+.4f} begrenzt (Symbol-Cap {cap:.2%})"
                )
            else:
                out[symbol] = weight
        return out

    # ------------------------------------------------------------------
    # Schritt 5: Brutto-Exposure
    # ------------------------------------------------------------------
    def _cap_gross(
        self, weights: PortfolioWeights, reasons: list[str]
    ) -> PortfolioWeights:
        """Summe der Betraege begrenzen -- proportional, nicht abschneidend.

        Abschneiden (die letzten Symbole auf 0) waere einfacher, wuerde aber
        die relative Gewichtung zerstoeren und damit die Aussage des
        Allokators verfaelschen. Die Risikoschicht darf das Budget kuerzen,
        nicht die Meinung umschreiben.
        """
        gross = sum(abs(w) for w in weights.values())
        limit = self.cfg.max_gross_exposure
        if gross <= limit or gross == 0.0:
            return weights

        factor = limit / gross
        reasons.append(
            f"Brutto-Exposure {gross:.2%} > {limit:.2%} -- alle Gewichte "
            f"proportional mit {factor:.4f} skaliert"
        )
        return {s: w * factor for s, w in weights.items()}


def realised_vol_map(
    closes: Mapping[str, np.ndarray],
    timeframe: str,
    cfg: RiskConfig | None = None,
) -> dict[str, float]:
    """`RiskState.realised_vol` aus Close-Fenstern je Symbol bauen.

    Duennes Mapping auf `ta.realised_vol` -- bewusst keine zweite
    Vola-Definition. Zwei Volatilitaetsfunktionen im selben System driften
    auseinander, und dann misst die Risikoschicht etwas anderes als die
    Strategie, die sie beschneidet. Zu kurze Historie ergibt `nan`, und die
    Engine behandelt `nan` konservativ (kein Hochskalieren).
    """
    cfg = cfg or RiskConfig()
    py = bars_per_year(timeframe)
    return {
        symbol: ta.realised_vol(window, cfg.vol_lookback, py)
        for symbol, window in closes.items()
    }
