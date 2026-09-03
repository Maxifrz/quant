"""Rank IC: sagt ein Signal die **Reihenfolge** der Maerkte voraus?

Bisher misst dieses Projekt jede Strategie je Markt einzeln und mittelt
hinterher (`qt placebo cross`). Bei 27 Maerkten mit mittlerer Paarkorrelation
0,26 sind das 3,4 effektive Tests (ADR-055) -- die Korrelation ist reiner
Verlust.

Der Querschnitts-IC dreht das um. Er fragt zu jedem Zeitpunkt: ordnet das
Signal die Maerkte richtig nach ihrer kommenden Rendite? Was allen Maerkten
gemeinsam ist, faellt dabei heraus; die Korrelation, die 26 Einzeltests
entwertet, ist hier das, was neutralisiert wird. Die Idee stammt aus NVIDIAs
`quantitative-signal-discovery-agent` (Apache-2.0), die Umsetzung nicht --
und der Unterschied liegt in genau zwei Punkten.

--------------------------------------------------------------------------
1. Die Vorwaertsrendite beginnt am **Open t+1**, nicht am Close t
--------------------------------------------------------------------------
Der Blueprint rechnet `close.shift(-k) / close - 1`: das Signal entsteht aus
dem Schluss von t und wird an einer Rendite gemessen, die bei genau diesem
Schluss beginnt. Das unterstellt, man koenne zu einem Kurs handeln, den man
gerade erst benutzt hat, um sich zu entscheiden.

Dieses Projekt fuellt am **Open des naechsten Bars** (ADR-001), und der IC
rechnet genauso: `open.shift(-1-k) / open.shift(-1) - 1`. Der Unterschied ist
keine Feinheit -- er ist genau der Lookahead, gegen den `LookaheadError` und
`tests/test_lookahead.py` sonst ueberall im Projekt stehen.

--------------------------------------------------------------------------
2. Die Signifikanz rechnet mit **T_eff**, nicht mit T
--------------------------------------------------------------------------
Der Blueprint bildet `t = mittel / (std / sqrt(T))` mit T = Zahl der Tage.
Das unterstellt, die taeglichen IC-Werte seien unabhaengig. Sie sind es nicht:
ein Signal mit 252 Tagen Rueckschau aendert sich von Tag zu Tag kaum, also
aendert sich auch sein IC kaum.

Gemessen an den 27 Maerkten dieses Stores, mit 12-1-Momentum:

    rho(IC_t, IC_t+1) = 0,796      T = 2192      T_eff = 249
    t naiv = +4,22  (p = 0,0000)   t korrigiert = +1,42  (p = 0,16)

Dasselbe Signal besteht das Kriterium des Blueprints und faellt bei ehrlicher
Rechnung durch. Deshalb ist die Korrektur hier **kein Schalter**: `t_naiv`
wird danebengestellt, damit die Differenz sichtbar bleibt, aber `bestanden`
haengt am korrigierten Wert.

**Die Formel ist nicht die aus ADR-052.** Dort ging es um den Mittelwert von
n gleichzeitig beobachteten, korrelierten Reihen: `n / (1 + (n-1)*rho)`. Hier
geht es um den Mittelwert **einer** Reihe mit Autokorrelation ueber die Zeit,
und dafuer gilt `T * (1 - rho) / (1 + rho)`. Beide heissen "effektive
Stichprobe" und sind verschiedene Groessen. Sie zu verwechseln waere derselbe
Fehler wie in ADR-055, nur in die andere Richtung.

Die Korrektur unterstellt AR(1). Eine IC-Reihe aus einem 252-Tage-Signal hat
laengeres Gedaechtnis als das, also ist `T_eff` hier eher zu **gross** und die
Korrektur zu **milde**. Das ist die Richtung, in der ein Fehler verzeihlich
ist.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats

# Weniger Namen als das ergeben zu jedem Zeitpunkt keinen Querschnitt: eine
# Rangkorrelation ueber vier Werte ist Rauschen mit Dezimalstellen.
MIN_NAMEN = 8

# Schwelle fuer `bestanden`. Zweiseitig, weil ein Signal auch invers
# funktionieren darf -- aber dann muss man es vorher gesagt haben, und
# `mittel` traegt das Vorzeichen sichtbar mit.
T_SCHWELLE = 1.96


@dataclass(slots=True)
class ICResult:
    """Rank IC einer Signalmatrix gegen die Vorwaertsrenditen."""

    mittel: float
    streuung: float
    n_perioden: int
    rho: float
    n_eff: float
    anteil_positiv: float
    horizont: int
    ics: np.ndarray = field(default_factory=lambda: np.empty(0), repr=False)

    @property
    def t_naiv(self) -> float:
        """Der Wert, den der Blueprint berichten wuerde. Nur zum Vergleich."""
        if self.streuung <= 0 or self.n_perioden < 2:
            return float("nan")
        return self.mittel / (self.streuung / np.sqrt(self.n_perioden))

    @property
    def t_korrigiert(self) -> float:
        if self.streuung <= 0 or self.n_eff < 2:
            return float("nan")
        return self.mittel / (self.streuung / np.sqrt(self.n_eff))

    @property
    def p_wert(self) -> float:
        t = self.t_korrigiert
        if not np.isfinite(t) or self.n_eff < 2:
            return float("nan")
        return float(2 * (1 - stats.t.cdf(abs(t), df=self.n_eff - 1)))

    @property
    def bestanden(self) -> bool:
        return bool(np.isfinite(self.t_korrigiert) and abs(self.t_korrigiert) >= T_SCHWELLE)

    @property
    def aufblaehung(self) -> float:
        """Um welchen Faktor der naive t-Wert zu gross ist."""
        if not np.isfinite(self.t_korrigiert) or self.t_korrigiert == 0:
            return float("nan")
        return abs(self.t_naiv / self.t_korrigiert)

    def table(self) -> str:
        zeilen = [
            f"  mittlerer Rank IC   {self.mittel:+.4f}   ueber {self.n_perioden} Zeitpunkte",
            f"  IC-Streuung         {self.streuung:.4f}",
            f"  Anteil IC > 0       {self.anteil_positiv:.1%}",
            f"  Horizont            {self.horizont} Bars, Fill am Open t+1",
            "",
            f"  Autokorrelation     rho = {self.rho:+.3f}",
            f"  effektive Punkte    {self.n_eff:.0f} statt {self.n_perioden}",
            "",
            f"  t naiv              {self.t_naiv:+.2f}   <- so rechnet der Blueprint",
            f"  t korrigiert        {self.t_korrigiert:+.2f}   p = {self.p_wert:.4f}",
            f"  Aufblaehung         Faktor {self.aufblaehung:.2f}",
            "",
            f"  Urteil: {'signifikant' if self.bestanden else 'NICHT signifikant'} "
            f"(|t| >= {T_SCHWELLE})",
        ]
        return "\n".join(zeilen)


def forward_returns(opens: pd.DataFrame, horizont: int) -> pd.DataFrame:
    """Rendite, die ein Signal vom Close des Bars t tatsaechlich erreichen kann.

    Eingestiegen wird am Open von t+1, ausgestiegen am Open von t+1+horizont.
    Der Zeitstempel der Zeile bleibt t -- das ist der Moment, in dem das
    Signal entstand, und nur so passen Signal und Rendite zusammen.
    """
    if horizont < 1:
        raise ValueError(f"horizont={horizont} muss mindestens 1 sein.")
    einstieg = opens.shift(-1)
    ausstieg = opens.shift(-1 - horizont)
    return ausstieg / einstieg.where(einstieg > 0) - 1.0


def autokorrelation(werte: np.ndarray) -> float:
    """Autokorrelation erster Ordnung; 0.0 wenn nicht berechenbar."""
    if len(werte) < 3:
        return 0.0
    a, b = werte[:-1], werte[1:]
    if np.std(a) < 1e-15 or np.std(b) < 1e-15:
        return 0.0
    rho = float(np.corrcoef(a, b)[0, 1])
    return rho if np.isfinite(rho) else 0.0


def effektive_perioden(n: int, rho: float) -> float:
    """`T * (1 - rho) / (1 + rho)`, gedeckelt auf [1, n].

    Negative Autokorrelation wuerde die effektive Stichprobe rechnerisch
    ueber n heben. Das ist fuer einen Mittelwert zwar korrekt, aber als
    Grundlage einer Signifikanzaussage zu grosszuegig -- deshalb der Deckel.
    """
    if n < 2:
        return float(max(n, 1))
    faktor = (1.0 - rho) / (1.0 + rho) if rho > -0.999 else float(n)
    return float(np.clip(n * faktor, 1.0, n))


def rank_ic(
    signal: pd.DataFrame,
    renditen: pd.DataFrame,
    *,
    horizont: int = 21,
    min_namen: int = MIN_NAMEN,
) -> ICResult:
    """Spearman-Rangkorrelation je Zeitpunkt, dann ueber die Zeit gemittelt.

    `renditen` sind bereits Vorwaertsrenditen aus `forward_returns` -- sie
    hier nicht selbst zu bilden ist Absicht: die Fill-Konvention gehoert an
    genau eine Stelle, sonst wandert sie irgendwann auseinander.
    """
    gemeinsame_zeiten = signal.index.intersection(renditen.index)
    gemeinsame_namen = signal.columns.intersection(renditen.columns)
    if len(gemeinsame_zeiten) == 0 or len(gemeinsame_namen) < min_namen:
        return ICResult(
            mittel=float("nan"), streuung=float("nan"), n_perioden=0,
            rho=float("nan"), n_eff=0.0, anteil_positiv=float("nan"),
            horizont=horizont,
        )

    s = signal.loc[gemeinsame_zeiten, gemeinsame_namen]
    r = renditen.loc[gemeinsame_zeiten, gemeinsame_namen]

    werte: list[float] = []
    for ts in gemeinsame_zeiten:
        s_zeile, r_zeile = s.loc[ts].dropna(), r.loc[ts].dropna()
        gemeinsam = s_zeile.index.intersection(r_zeile.index)
        if len(gemeinsam) < min_namen:
            continue
        a = s_zeile[gemeinsam].to_numpy(dtype=float)
        b = r_zeile[gemeinsam].to_numpy(dtype=float)
        # Eine Rangkorrelation braucht Varianz auf beiden Seiten; ein
        # konstantes Signal hat keine Meinung, keine Korrelation von 0.
        if np.std(a) < 1e-12 or np.std(b) < 1e-12:
            continue
        c = stats.spearmanr(a, b).statistic
        if np.isfinite(c):
            werte.append(float(c))

    if not werte:
        return ICResult(
            mittel=float("nan"), streuung=float("nan"), n_perioden=0,
            rho=float("nan"), n_eff=0.0, anteil_positiv=float("nan"),
            horizont=horizont,
        )

    ic = np.asarray(werte, dtype=float)
    rho = autokorrelation(ic)
    return ICResult(
        mittel=float(ic.mean()),
        streuung=float(ic.std()),
        n_perioden=int(ic.size),
        rho=rho,
        n_eff=effektive_perioden(ic.size, rho),
        anteil_positiv=float(np.mean(ic > 0)),
        horizont=horizont,
        ics=ic,
    )
