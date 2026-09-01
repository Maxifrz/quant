"""Deflated Sharpe Ratio -- ein Sharpe, der weiss, wieviele Versuche er gekostet hat.

Wer 500 Strategien testet, findet garantiert eine mit Sharpe 2 -- auch wenn
alle 500 reines Rauschen sind. Das ist keine Vermutung, sondern
Ordnungsstatistik: das Maximum aus 500 Ziehungen einer Nullverteilung liegt
im Mittel rund drei Standardabweichungen ueber deren Mittelwert. Der Backtest
des Gewinners einer Suche zeigt deshalb systematisch zu viel, und die Zahl,
die er zeigt, ist nicht falsch gerechnet -- sie ist nur die falsche Zahl.
Genau deshalb steht die DSR laut ADR-005 ab Tag 1 im Research-Loop und nicht
spaeter.

Die DSR beantwortet die richtige Frage: **wie wahrscheinlich ist es, dass
dieser Sharpe echt ist, wenn man weiss, dass er der Beste aus N Versuchen
war?** Ergebnis ist eine Wahrscheinlichkeit in [0, 1], keine Sharpe-Zahl.
Ueblich ist eine Schwelle von 0,95.

Zwei Korrekturen stecken darin, und beide zaehlen:

1. **Selektion.** Der Massstab ist nicht null, sondern der *erwartete beste
   Sharpe unter der Nullhypothese* nach N Versuchen. Er waechst mit N, und
   deshalb faellt die DSR mit N.
2. **Nicht-Normalitaet.** Der klassische Sharpe-Test unterstellt normale
   Renditen; Handelsrenditen sind schief und fettschwaenzig. Negative
   Schiefe und hohe Woelbung machen den Sharpe-Schaetzer unzuverlaessiger,
   als der Standardfehler unter Normalitaet behauptet -- die DSR faellt.

Formel (Bailey / Lopez de Prado, "The Deflated Sharpe Ratio: Correcting for
Selection Bias, Backtest Overfitting and Non-Normality", 2014):

    DSR = Phi( (SR - SR0) * sqrt(T - 1)
               / sqrt(1 - g3*SR + (g4 - 1)/4 * SR^2) )

    SR0 = sigma_SR * ( (1 - gamma) * Phi^-1(1 - 1/N)
                       + gamma * Phi^-1(1 - 1/(N*e)) )

mit Phi = Verteilungsfunktion der Standardnormalverteilung, T =
Beobachtungen, N = Versuche, g3 = Schiefe, g4 = Woelbung, gamma =
Euler-Mascheroni-Konstante.

Reine Statistik: dieses Modul liest keine Datei, kennt keine Registry und
haelt keinen Zustand. Wer N liefert, entscheidet anderswo -- laut ADR-005
zaehlt die Kandidaten-Registry *alle je getesteten* Versuche, nicht nur die
der aktuellen Charge.

--------------------------------------------------------------------------
KURTOSIS-KONVENTION -- einmal lesen, ueberall gueltig
--------------------------------------------------------------------------
**`kurtosis` ist immer die ROHE Woelbung: Normalverteilung = 3.**

    Normalverteilung        kurtosis = 3.0      (nicht 0.0)
    fettschwaenzig          kurtosis > 3.0
    t-Verteilung mit df=4   kurtosis ~ 16

`scipy.stats.kurtosis` liefert per Default die *Excess*-Woelbung
(Normalverteilung = 0, Parameter `fisher=True`). Wer sie ungeprueft
durchreicht, rechnet im Nennerterm mit (0 - 1)/4 = -0,25 statt mit
(3 - 1)/4 = +0,5 und bekommt einen systematisch zu hohen DSR -- ohne
Fehlermeldung, ohne nan, nur mit einem zu milden Urteil. `dsr_from_returns`
ruft deshalb ausdruecklich `kurtosis(..., fisher=False)`.

Der Parameter heisst darum `kurtosis` und nicht `excess_kurtosis`, und er
wird geprueft: fuer jede Verteilung gilt `kurtosis >= 1 + skewness^2`. Eine
versehentlich uebergebene Excess-Woelbung nahe 0 faellt damit auf und wirft,
statt still ein zu gutes Ergebnis zu liefern. Wasserdicht ist die Pruefung
nicht -- eine Excess-Woelbung von 5 ist eine gueltige rohe Woelbung --,
weshalb die Konvention hier im Docstring steht und nicht nur im Test.

--------------------------------------------------------------------------
FREQUENZKONVENTION -- Sharpe und Laenge muessen zusammenpassen
--------------------------------------------------------------------------
**`sharpe` ist der Sharpe JE BAR (nicht annualisiert), und
`track_record_length` zaehlt genau diese Bars.**

    4h-Bars ueber 3 Jahre  ->  track_record_length = 6570
                               sharpe = mean(r) / std(r) ueber dieselben
                               6570 Bar-Renditen, ohne sqrt(bars_per_year)

Der annualisierte Sharpe aus `qt.backtest.metrics.sharpe` ist um den Faktor
`sqrt(bars_per_year(tf))` groesser -- bei 4h-Bars rund 47. Wer ihn hier
einsetzt und die Laenge weiter in Bars zaehlt, bekommt fuer jede beliebige
Rauschstrategie eine DSR von 1,0. Kein Fehlschlag, keine Warnung, nur eine
Zahl, die alles durchwinkt: dieselbe Fehlerklasse wie in ADR-020 und
ADR-016 -- zwei Groessen an verschiedenen Orten, die zueinander passen
muessen, und niemand prueft es.

Deshalb ist `dsr_from_returns` der empfohlene Einstieg. Es bekommt die
Renditereihe und leitet Sharpe, Momente und Laenge aus *derselben* Reihe ab;
sie koennen gar nicht auseinanderfallen. `periods_per_year` ist dort reine
Berichtsgroesse (`DSRResult.annualised_sharpe`) und geht in die DSR nicht
ein.

Dass die Annualisierung folgenlos bleibt, ist kein Versehen, sondern die
Probe aufs Exempel: verdichtet man 4h-Bars zu Tagesbars, waechst der Sharpe
je Bar um sqrt(6), waehrend T durch 6 faellt. Das Produkt `SR * sqrt(T - 1)`,
auf das allein es ankommt, bleibt gleich. Die DSR haengt an der Menge der
Evidenz, nicht an der Wahl der Zeiteinheit.

--------------------------------------------------------------------------
Der Massstab SR0: wie stark streuen die Versuche?
--------------------------------------------------------------------------
`sharpe_std` (sigma_SR) ist die Streuung der Sharpe-Werte **ueber die
Versuche hinweg**, in derselben Frequenz wie `sharpe`. Sie zu kennen setzt
voraus, alle N Versuche gemessen zu haben; wer nur den Gewinner hat, hat sie
nicht.

`deflated_sharpe_ratio(..., sharpe_std=None)` setzt deshalb den Wert ein,
den die Nullhypothese vorgibt: unter H0 (wahrer Sharpe = 0) hat ein ueber T
Bars geschaetzter Sharpe den Standardfehler `1 / sqrt(T - 1)`. Die Terme mit
Schiefe und Woelbung fallen dabei weg, weil sie mit dem wahren Sharpe
multipliziert werden -- und der ist unter H0 null. Das ist exakt die
Streuung, die N reine Rauschstrategien derselben Laenge zeigen wuerden, und
sie schrumpft mit laengerem Track Record. Genau richtig: der beste Zufall
aus 500 Versuchen ueber 100 Bars ist beeindruckend, ueber 100.000 Bars nicht.

Der Default `sharpe_std=1.0` von `expected_max_sharpe` ist etwas anderes:
die nackte Ordnungsstatistik in Einheiten von Standardabweichungen. Als
eigenstaendiger Baustein richtig, als Deflations-Massstab fuer einen Sharpe
je Bar unbrauchbar -- 1,0 je Bar waere ein annualisierter Sharpe von rund
47, und jede reale Strategie fiele durch. `deflated_sharpe_ratio` erbt
diesen Default deshalb bewusst nicht.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats

# Uebliche Schwelle. 0,95 heisst: nur eine von zwanzig derart bewerteten
# Strategien ist im Nachhinein Zufall -- gemessen an der Zahl der Versuche,
# die noetig war, um sie zu finden.
DEFAULT_THRESHOLD = 0.95

# Woelbung der Normalverteilung in roher Konvention. Steht als Konstante da,
# damit die 3 in den Formeln nicht wie eine willkuerliche Zahl aussieht.
NORMAL_KURTOSIS = 3.0


@dataclass(frozen=True, slots=True)
class DSRResult:
    """Ergebnis einer Deflation, mit allem zum Nachrechnen Noetigen.

    `sharpe`, `expected_max` und `sharpe_std` sind Groessen **je Bar**, in
    derselben Frequenz wie `track_record_length` sie zaehlt (siehe
    Modul-Docstring). `kurtosis` ist **roh** (Normalverteilung = 3).

    Der Massstab `expected_max` steht mit im Ergebnis, weil die
    interessante Frage nach einem durchgefallenen Kandidaten immer dieselbe
    ist: war der Sharpe zu klein oder die Zahl der Versuche zu gross? Ein
    Ergebnis, das nur die Wahrscheinlichkeit zurueckgibt, kann darauf nicht
    antworten.
    """

    dsr: float
    sharpe: float
    n_trials: int
    track_record_length: int
    skewness: float
    kurtosis: float
    expected_max: float
    sharpe_std: float
    periods_per_year: float | None = None

    def passed(self, threshold: float = DEFAULT_THRESHOLD) -> bool:
        """Haelt die DSR die Schwelle?

        `threshold` ist eine Wahrscheinlichkeit in (0, 1), kein Prozentwert
        und kein Sharpe. 95 statt 0.95 wuerde jede Strategie durchfallen
        lassen und sieht dabei aus wie eine besonders strenge Einstellung --
        deshalb die Pruefung.
        """
        if not 0.0 < threshold < 1.0:
            raise ValueError(
                f"threshold ist eine Wahrscheinlichkeit und muss echt "
                f"zwischen 0 und 1 liegen, ist aber {threshold}. Fuer 95% "
                f"lautet der Wert 0.95, nicht 95."
            )
        return bool(self.dsr >= threshold)

    @property
    def annualised_sharpe(self) -> float:
        """Der Sharpe in der Form, in der ihn `qt.backtest.metrics` ausweist.

        `nan`, wenn `periods_per_year` unbekannt ist -- lieber gar keine
        Zahl als eine mit geratener Frequenz. Diese Groesse ist reine
        Darstellung: in die DSR geht ausschliesslich `sharpe` je Bar ein.
        """
        if self.periods_per_year is None:
            return float("nan")
        return self.sharpe * math.sqrt(self.periods_per_year)

    @property
    def excess_kurtosis(self) -> float:
        """Woelbung in der scipy-Default-Konvention (Normalverteilung = 0)."""
        return self.kurtosis - NORMAL_KURTOSIS

    def describe(self) -> str:
        mark = "bestanden" if self.passed() else "durchgefallen"
        ann = ""
        if self.periods_per_year is not None:
            ann = f" (annualisiert {self.annualised_sharpe:.2f})"
        return (
            f"DSR {self.dsr:.3f} -- {mark} bei {DEFAULT_THRESHOLD:.0%} | "
            f"Sharpe/Bar {self.sharpe:.4f}{ann} gegen Massstab "
            f"{self.expected_max:.4f} aus {self.n_trials} Versuchen | "
            f"T={self.track_record_length} Bars, Schiefe {self.skewness:+.2f}, "
            f"Woelbung {self.kurtosis:.2f}"
        )


def expected_max_sharpe(n_trials: int, sharpe_std: float = 1.0) -> float:
    """Erwarteter bester Sharpe aus `n_trials` Versuchen unter der Nullhypothese.

    Der Massstab, den ein gefundener Sharpe schlagen muss: so gut ist der
    Beste aus N Versuchen im Mittel, wenn keiner der N eine echte Kante hat.

        E[max] = sharpe_std * ( (1 - gamma) * Phi^-1(1 - 1/N)
                                + gamma * Phi^-1(1 - 1/(N*e)) )

    Die Naeherung folgt aus der Gumbel-Grenzverteilung des Maximums
    normalverteilter Ziehungen. Fuer N >= 10 liegt sie innerhalb von rund 2%
    des per Monte Carlo bestimmten Erwartungswerts, bei N = 2 rund 9%
    darunter -- beides in den Tests festgehalten. Fuer den Zweck hier ist das
    ausreichend: der Unterschied zwischen 500 und 550 Versuchen aendert den
    Massstab um weniger als ein Prozent, und die Zahl der Versuche selbst
    kennt man ohnehin nur so genau, wie die Registry mitgezaehlt hat.

    `sharpe_std` ist die Streuung der Sharpe-Werte ueber die Versuche, in
    derselben Frequenz wie der spaeter zu deflationierende Sharpe (siehe
    Modul-Docstring). Der Default 1,0 liefert die reine Ordnungsstatistik in
    Standardabweichungen und ist damit als Deflations-Massstab fuer einen
    Sharpe je Bar zu gross -- `deflated_sharpe_ratio` uebernimmt ihn nicht.

    `n_trials=1` gibt exakt 0,0 zurueck. Die Naeherung ist dort nicht
    definiert (Phi^-1(1 - 1/1) = Phi^-1(0) = -unendlich), der exakte Wert
    aber trivial: das Maximum einer einzigen Ziehung ist die Ziehung selbst,
    und deren Erwartungswert ist null. Ohne Suche keine Selektionsverzerrung.
    """
    n = _as_count("n_trials", n_trials)
    if n < 1:
        raise ValueError(
            f"n_trials ist die Zahl der getesteten Strategien und muss >= 1 "
            f"sein, ist aber {n_trials!r}. Ein einziger, nie wiederholter "
            f"Versuch ist n_trials=1 -- nicht 0."
        )
    sd = _finite("sharpe_std", sharpe_std)
    if sd < 0:
        raise ValueError(
            f"sharpe_std ist eine Standardabweichung und darf nicht negativ "
            f"sein, ist aber {sd}."
        )

    if n == 1:
        return 0.0

    # `isf(q)` statt `ppf(1 - q)`: bei grossen N ist 1 - 1/N in doppelter
    # Genauigkeit irgendwann exakt 1,0, und Phi^-1(1) ist unendlich. Ueber
    # die Ueberlebensfunktion tritt diese Ausloeschung gar nicht erst auf.
    z_n = float(stats.norm.isf(1.0 / n))
    z_ne = float(stats.norm.isf(1.0 / (n * math.e)))
    return sd * ((1.0 - np.euler_gamma) * z_n + np.euler_gamma * z_ne)


def deflated_sharpe_ratio(
    sharpe: float,
    n_trials: int,
    track_record_length: int,
    skewness: float,
    kurtosis: float,
    sharpe_std: float | None = None,
) -> float:
    """Wahrscheinlichkeit in [0, 1], dass der Sharpe die Selektion ueberlebt.

    Argumente -- die Konventionen stehen ausfuehrlich im Modul-Docstring,
    hier nur das Noetigste:

    * `sharpe` -- Sharpe **je Bar**, nicht annualisiert.
    * `track_record_length` -- Zahl genau dieser Bars (T).
    * `skewness` / `kurtosis` -- Momente **derselben** Bar-Renditen.
      `kurtosis` ist **roh**: Normalverteilung = 3, nicht 0.
    * `sharpe_std` -- Streuung der Sharpe-Werte ueber die N Versuche. `None`
      setzt den Nullhypothesen-Wert `1 / sqrt(T - 1)` ein.

    Zwei Eigenschaften, die man beim Lesen des Ergebnisses kennen muss:

    Die DSR faellt streng mit `n_trials`. Das ist der ganze Zweck der Uebung
    und keine unerwuenschte Nebenwirkung -- dieselbe Renditereihe verdient
    weniger Vertrauen, wenn sie aus 500 Versuchen ausgewaehlt wurde statt aus
    zehn.

    Ein laengerer Track Record hebt die DSR nur, solange der Sharpe **ueber**
    dem Massstab liegt. Liegt er darunter, senkt jeder weitere Bar sie: mehr
    Evidenz macht die Aussage sicherer, nicht freundlicher. Mit dem Default
    `sharpe_std=None` faellt der Massstab selbst mit sqrt(T), weshalb dort
    jeder positive Sharpe irgendwann gewinnt -- zu Recht, denn eine Kante,
    die ueber 100.000 Bars haelt, ist keine Auswahlverzerrung mehr.
    """
    sr = _finite("sharpe", sharpe)
    g3 = _finite("skewness", skewness)
    g4 = _finite("kurtosis", kurtosis)
    n = _as_count("n_trials", n_trials)
    length = _track_record(track_record_length)
    _check_moments(g3, g4)

    benchmark = expected_max_sharpe(n, _trial_sharpe_std(length, sharpe_std))
    return _probabilistic_sharpe(sr, benchmark, length, g3, g4)


def dsr_from_returns(
    returns: np.ndarray,
    n_trials: int,
    periods_per_year: float | None = None,
    sharpe_std: float | None = None,
) -> DSRResult:
    """DSR direkt aus einer Bar-Renditereihe -- der empfohlene Einstieg.

    Sharpe, Schiefe, Woelbung und Laenge stammen alle aus **derselben**
    Reihe. Damit ist die Frequenzkonvention aus dem Modul-Docstring nicht
    Vereinbarung, sondern Konstruktion: es gibt keinen Weg, einen
    annualisierten Sharpe mit einer in Bars gezaehlten Laenge zu mischen.

    `returns` sind **einfache Bar-Renditen** (r_t = P_t/P_{t-1} - 1), so wie
    `qt.backtest.metrics` sie verwendet -- keine Log-Renditen, keine
    Equity-Kurve. Der Sharpe wird ohne risikofreien Zins gerechnet, wie im
    ganzen Projekt (rf = 0); wer einen braucht, zieht ihn vorher ab.

    `periods_per_year` -- etwa `bars_per_year(timeframe)` aus
    `qt.core.types` -- **aendert das Ergebnis nicht**. Es fuellt nur
    `DSRResult.annualised_sharpe`, damit der ausgewiesene Sharpe neben dem
    aus `qt.backtest.metrics` stehen kann. Dass die Annualisierung folgenlos
    ist, ist die eingebaute Absicherung gegen den stillen Frequenzfehler.
    """
    values = np.asarray(returns, dtype=float).ravel()

    # Vier statt zwei: die erwartungstreuen Schaetzer fuer Schiefe und
    # Woelbung teilen durch (n-2) beziehungsweise (n-2)(n-3) und sind
    # darunter nicht definiert. Lieber hier eine klare Meldung als ein nan,
    # das erst in der Formel auffaellt.
    if values.size < 4:
        raise ValueError(
            f"Renditereihe hat {values.size} Werte; fuer Schiefe und Woelbung "
            f"sind mindestens 4 noetig. Eine so kurze Historie traegt ohnehin "
            f"keine Aussage ueber Overfitting -- mehr Bars laden."
        )
    if not np.all(np.isfinite(values)):
        raise ValueError(
            "Nicht-endliche Renditen in der Reihe. Ein einziges nan macht "
            "Mittelwert, Streuung und beide Momente unbrauchbar -- die Reihe "
            "vorher bereinigen, statt hier eine Zahl zu erzwingen."
        )

    sd = float(values.std(ddof=1))
    if sd <= 0.0:
        raise ValueError(
            "Konstante Renditereihe (Standardabweichung 0) -- ihr Sharpe ist "
            "nicht definiert. Das ist typischerweise eine Strategie, die nie "
            "eine Position eingegangen ist; sie hat kein Ergebnis, das man "
            "deflationieren koennte."
        )

    sharpe = float(values.mean() / sd)
    skewness = float(stats.skew(values, bias=False))
    # `fisher=False` ist die Kurtosis-Konvention dieses Moduls: roh, nicht
    # excess. `bias=False` gibt die erwartungstreuen Schaetzer, wie sie auch
    # `pandas.Series.skew()/.kurt()` liefern -- damit stimmen die Momente im
    # Report mit denen in der Formel ueberein.
    kurtosis = float(stats.kurtosis(values, fisher=False, bias=False))

    length = values.size
    std_used = _trial_sharpe_std(length, sharpe_std)
    benchmark = expected_max_sharpe(n_trials, std_used)
    dsr = _probabilistic_sharpe(sharpe, benchmark, length, skewness, kurtosis)

    ppy = None
    if periods_per_year is not None:
        ppy = _finite("periods_per_year", periods_per_year)
        if ppy <= 0:
            raise ValueError(
                f"periods_per_year muss positiv sein, ist aber {ppy}. Fuer "
                f"4h-Bars liefert qt.core.types.bars_per_year('4h') rund 2192."
            )

    return DSRResult(
        dsr=dsr,
        sharpe=sharpe,
        n_trials=_as_count("n_trials", n_trials),
        track_record_length=length,
        skewness=skewness,
        kurtosis=kurtosis,
        expected_max=benchmark,
        sharpe_std=std_used,
        periods_per_year=ppy,
    )


# ----------------------------------------------------------------------
# Interna
# ----------------------------------------------------------------------


def _probabilistic_sharpe(
    sharpe: float,
    benchmark: float,
    length: int,
    skewness: float,
    kurtosis: float,
) -> float:
    """PSR: Wahrscheinlichkeit, dass der wahre Sharpe ueber `benchmark` liegt.

    Der Nenner ist der Standardfehler des Sharpe-Schaetzers nach Lo (2002)
    beziehungsweise Mertens (2002), zusammengefasst zu

        Var(SR) = (1 - g3*SR + (g4 - 1)/4 * SR^2) / (T - 1)

    Die beiden Momententerme sind der Grund, warum hier nicht einfach
    sqrt(T) steht: negative Schiefe (-g3*SR wird positiv) und hohe Woelbung
    vergroessern die Unsicherheit des Schaetzers. Eine Strategie, die selten
    viel verliert und oft wenig gewinnt, hat einen unzuverlaessigeren
    Sharpe, als ihr Punktschaetzer suggeriert.

    Groessenordnung, damit niemand mehr erwartet, als die Formel hergibt:
    bei einem Sharpe **je Bar** um 0,03 geht die Woelbung nur quadratisch
    ein und verschiebt den Standardfehler um Promille; die Schiefe geht
    linear ein und wird ab etwa g3 = -1 spuerbar. Bei Bar-Frequenz traegt
    also die Selektionskorrektur den Grossteil der Deflation -- die
    Nicht-Normalitaet ist eine Korrektur, kein Hauptterm.

    (T - 1) statt T ist die Kleinstichprobenkorrektur aus dem PSR-Papier --
    Konvention, keine Herleitung; bei realistischen Laengen macht sie
    Promille aus.
    """
    variance_factor = 1.0 - skewness * sharpe + 0.25 * (kurtosis - 1.0) * sharpe**2
    if variance_factor <= 0.0:
        # Nach `_check_moments` (g4 >= 1 + g3^2) gilt immer
        # variance_factor >= (1 - g3*SR/2)^2 >= 0; exakt null bleibt als
        # Grenzfall moeglich, und dort waere die Division sinnlos.
        raise ValueError(
            f"Der Standardfehler des Sharpe ist null (Schiefe {skewness}, "
            f"Woelbung {kurtosis}, Sharpe {sharpe}). Diese Kombination aus "
            f"Momenten ist ein Grenzfall ohne Streuung -- vermutlich sind "
            f"die Momente nicht aus derselben Reihe wie der Sharpe."
        )
    std_error = math.sqrt(variance_factor / (length - 1))
    return float(stats.norm.cdf((sharpe - benchmark) / std_error))


def _trial_sharpe_std(length: int, sharpe_std: float | None) -> float:
    """Streuung der Sharpe-Werte ueber die Versuche -- gemessen oder aus H0.

    Einzige Quelle dieses Defaults im Modul: `deflated_sharpe_ratio` und
    `dsr_from_returns` muessen denselben Massstab benutzen, sonst liefern
    zwei Wege zur selben Zahl zwei Zahlen.
    """
    if sharpe_std is None:
        return 1.0 / math.sqrt(length - 1)
    sd = _finite("sharpe_std", sharpe_std)
    if sd < 0:
        raise ValueError(
            f"sharpe_std ist eine Standardabweichung und darf nicht negativ "
            f"sein, ist aber {sd}."
        )
    return sd


def _check_moments(skewness: float, kurtosis: float) -> None:
    """Sind Schiefe und Woelbung ueberhaupt gemeinsam moeglich?

    Fuer jede Verteilung mit endlichen vierten Momenten gilt
    `kurtosis >= 1 + skewness^2` (Cauchy-Schwarz). Die Pruefung faengt vor
    allem einen Fall ab: eine versehentlich uebergebene *Excess*-Woelbung.
    Bei annaehernd normalen Renditen liegt die nahe 0 und verletzt die
    Schranke sofort -- statt still ein zu mildes Ergebnis zu liefern.
    """
    bound = 1.0 + skewness**2
    if kurtosis >= bound - 1e-9:
        return
    if kurtosis < 1.0:
        raise ValueError(
            f"kurtosis={kurtosis} ist kleiner als 1 und damit als rohe "
            f"Woelbung unmoeglich. Dieses Modul erwartet die ROHE Woelbung "
            f"(Normalverteilung = 3), nicht die Excess-Woelbung "
            f"(Normalverteilung = 0). Aus scipy: "
            f"stats.kurtosis(returns, fisher=False) -- oder auf einen "
            f"vorhandenen Excess-Wert 3 addieren."
        )
    raise ValueError(
        f"kurtosis={kurtosis} und skewness={skewness} sind gemeinsam "
        f"unmoeglich: es gilt immer kurtosis >= 1 + skewness^2 = {bound:.4f}. "
        f"Vermutlich stammen die beiden Momente aus verschiedenen Reihen oder "
        f"die Woelbung ist eine Excess-Woelbung (Normalverteilung = 0 statt 3)."
    )


def _track_record(track_record_length: int) -> int:
    length = _as_count("track_record_length", track_record_length)
    if length < 2:
        raise ValueError(
            f"track_record_length zaehlt die Bar-Renditen und muss >= 2 sein, "
            f"ist aber {track_record_length!r}. Bei einer einzigen Beobachtung "
            f"ist sqrt(T - 1) = 0: der Sharpe haette keinen Standardfehler und "
            f"die DSR keine Aussage."
        )
    return length


def _as_count(name: str, value: int) -> int:
    """Ganzzahl aus einer Anzahl -- oder eine Meldung, die den Namen nennt."""
    try:
        count = int(value)
        exact = bool(count == value)
    except (TypeError, ValueError, OverflowError):
        exact = False
        count = 0
    if not exact:
        raise ValueError(
            f"{name} ist eine Anzahl und muss eine ganze Zahl sein, ist aber "
            f"{value!r}."
        )
    return count


def _finite(name: str, value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"{name} muss eine Zahl sein, ist aber {value!r}."
        ) from None
    if not math.isfinite(number):
        raise ValueError(
            f"{name} muss endlich sein, ist aber {number}. Ein nan an dieser "
            f"Stelle stammt fast immer aus einer leeren oder konstanten "
            f"Renditereihe -- dort liegt der Fehler, nicht hier."
        )
    return number
