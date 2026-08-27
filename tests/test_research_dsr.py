"""Tests der Deflated Sharpe Ratio aus `qt.research.dsr`.

Die DSR ist eine Formel, die man falsch abschreiben kann, ohne dass
irgendetwas fehlschlaegt: das Ergebnis bleibt eine Wahrscheinlichkeit
zwischen 0 und 1 und sieht in jedem Report plausibel aus. Ein vergessener
Term, ein gedrehtes Vorzeichen bei der Schiefe, eine Excess- statt einer
rohen Woelbung -- nichts davon wirft, alles davon verschiebt jedes Urteil
ueber jede Strategie in dieselbe Richtung.

Deshalb pruefen diese Tests Eigenschaften und keine Zwischenschritte:

* den einen Punkt, an dem die geschlossene Form von Hand nachrechenbar ist
  (Sharpe genau auf dem Massstab -> Phi(0) = 0,5),
* die drei Monotonien, die den Zweck der Korrektur ueberhaupt ausmachen,
* eine zweite, **unabhaengig hergeleitete** Implementierung
  (`reference_dsr`, siehe deren Docstring zur Herleitung),
* die beiden Konventionen -- rohe Woelbung, Sharpe je Bar --, deren
  Verwechslung still wirkt,
* und die Fehlerfaelle, die alle werfen muessen statt nan zu liefern.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats

from qt.core.types import bars_per_year
from qt.research.dsr import (
    DSRResult,
    deflated_sharpe_ratio,
    dsr_from_returns,
    expected_max_sharpe,
)


# ----------------------------------------------------------------------
# Zweite Implementierung, unabhaengig hergeleitet
# ----------------------------------------------------------------------


def reference_dsr(
    sharpe: float,
    n_trials: int,
    track_record_length: int,
    skewness: float,
    kurtosis: float,
    sharpe_std: float,
) -> float:
    """Dieselbe Groesse, aus der Literaturform neu zusammengesetzt.

    Bewusst **nicht** aus `dsr.py` abgeschrieben, sondern entlang der
    Herleitung gebaut, aus der die DSR entsteht -- sonst pruefte der Test
    nur, ob zwei Kopien desselben Tippfehlers uebereinstimmen.

    Schritt 1 -- Standardfehler des Sharpe-Schaetzers, Delta-Methode nach
    Lo (2002) / Mertens (2002), in der **ausgeschriebenen** Form:

        Var(SR) = ( 1 + SR^2 / 2 - g3 * SR + (g4 - 3) / 4 * SR^2 ) / (T - 1)

    Die Terme stehen hier einzeln nebeneinander: `1` fuer den Zaehler des
    Sharpe, `SR^2 / 2` fuer die Schaetzunsicherheit der Streuung im Nenner,
    `-g3 * SR` fuer die Kovarianz zwischen beiden bei schiefen Renditen,
    `(g4 - 3)/4 * SR^2` fuer den Aufschlag durch fette Enden -- der bei einer
    Normalverteilung (g4 = 3) verschwindet, was der schnellste Selbsttest
    dieser Zeile ist. `dsr.py` fasst `SR^2/2` und `(g4-3)/4 * SR^2` zu
    `(g4-1)/4 * SR^2` zusammen; faellt beim Zusammenfassen etwas weg, zeigt
    es sich genau hier.

    Schritt 2 -- Massstab aus der Ordnungsstatistik, ueber `ppf(1 - q)` statt
    ueber `isf(q)` und mit ausgeschriebener Euler-Mascheroni-Konstante.

    Schritt 3 -- Phi der studentisierten Differenz.

    Uebernommen ist einzig die Konvention (T - 1) statt T: sie ist die
    Kleinstichprobenkorrektur des PSR-Papiers, also eine Setzung und keine
    Herleitung. Diese Wahl zu wiederholen ist Absicht -- geprueft wird die
    Formel, nicht die Frage, ob durch T oder T-1 geteilt wird.
    """
    variance = (
        1.0
        + sharpe**2 / 2.0
        - skewness * sharpe
        + (kurtosis - 3.0) / 4.0 * sharpe**2
    ) / (track_record_length - 1)

    euler_gamma = 0.5772156649015329
    z_n = stats.norm.ppf(1.0 - 1.0 / n_trials)
    z_ne = stats.norm.ppf(1.0 - 1.0 / (n_trials * math.exp(1.0)))
    benchmark = sharpe_std * ((1.0 - euler_gamma) * z_n + euler_gamma * z_ne)

    return float(stats.norm.cdf((sharpe - benchmark) / math.sqrt(variance)))


# (sharpe je Bar, n_trials, T, Schiefe, rohe Woelbung, sharpe_std)
# Handverlesen: normal und nicht-normal, schief in beide Richtungen, ein
# negativer Sharpe, ein sehr langer und ein sehr kurzer Track Record.
CASES = [
    (0.05, 100, 2_000, 0.0, 3.0, 0.02),
    (0.03, 25, 500, -0.5, 8.0, 0.02),
    (0.10, 1_000, 5_000, 0.4, 4.5, 0.05),
    (-0.02, 50, 800, -1.2, 12.0, 0.01),
    (0.001, 10, 100_000, 0.0, 3.0, 0.005),
    (0.20, 2, 300, 1.5, 9.0, 0.10),
]


@pytest.mark.parametrize("case", CASES)
def test_matches_an_independently_derived_reference(case):
    """Implementierung und unabhaengige Herleitung, Fall fuer Fall."""
    sharpe, n_trials, length, skew, kurt, sd = case

    assert deflated_sharpe_ratio(
        sharpe, n_trials, length, skew, kurt, sharpe_std=sd
    ) == pytest.approx(reference_dsr(sharpe, n_trials, length, skew, kurt, sd))


@pytest.mark.parametrize("case", CASES)
def test_the_null_hypothesis_default_matches_the_reference(case):
    """Auch der Default `sharpe_std=None` ist nachgerechnet.

    Er steht fuer die Streuung, die N reine Rauschstrategien ueber T Bars
    zeigen wuerden: unter H0 ist der wahre Sharpe null, damit fallen die
    Momententerme im Standardfehler weg und es bleibt 1 / sqrt(T - 1).
    Der Wert wird hier ausgeschrieben statt importiert -- er ist eine
    Entscheidung des Moduls und soll nicht unbemerkt wandern koennen.
    """
    sharpe, n_trials, length, skew, kurt, _ = case
    null_std = 1.0 / math.sqrt(length - 1)

    assert deflated_sharpe_ratio(
        sharpe, n_trials, length, skew, kurt
    ) == pytest.approx(reference_dsr(sharpe, n_trials, length, skew, kurt, null_std))


# ----------------------------------------------------------------------
# Geschlossene Form am Rand
# ----------------------------------------------------------------------


@pytest.mark.parametrize("length", [2, 10, 500, 20_000, 1_000_000])
def test_a_sharpe_exactly_on_the_benchmark_is_one_half(length):
    """Sharpe = Massstab -> Phi(0) = 0,5, fuer jede Laenge des Track Records.

    Der einzige Punkt der Formel, der ohne externe Referenz von Hand
    nachrechenbar ist: der Zaehler wird null, und dann ist es voellig egal,
    durch welchen Standardfehler man teilt. Faellt dieser Test, stimmt der
    Massstab nicht mit dem ueberein, gegen den tatsaechlich verglichen wird
    -- und dann ist jede andere Zahl aus diesem Modul auch falsch.
    """
    benchmark = expected_max_sharpe(200, sharpe_std=0.03)

    dsr = deflated_sharpe_ratio(
        benchmark, 200, length, skewness=-0.4, kurtosis=7.0, sharpe_std=0.03
    )

    assert dsr == pytest.approx(0.5)


def test_a_single_trial_is_the_plain_probabilistic_sharpe():
    """Ohne Suche kein Abschlag: bei N=1 ist der Massstab exakt null.

    Die DSR faellt damit auf die Probabilistic Sharpe Ratio gegen null
    zurueck -- die Frage "ist der Sharpe ueberhaupt positiv", ohne
    Selektionskorrektur.
    """
    assert expected_max_sharpe(1) == 0.0
    assert expected_max_sharpe(1, sharpe_std=0.5) == 0.0

    sharpe, length = 0.04, 1_000
    psr = stats.norm.cdf(sharpe * math.sqrt(length - 1) / math.sqrt(1 + sharpe**2 / 2))

    assert deflated_sharpe_ratio(sharpe, 1, length, 0.0, 3.0) == pytest.approx(psr)


# ----------------------------------------------------------------------
# Monotonien -- der eigentliche Regressionstest
# ----------------------------------------------------------------------


def test_dsr_rises_strictly_with_the_sharpe():
    values = [
        deflated_sharpe_ratio(sr, 100, 5_000, -0.3, 6.0)
        for sr in (0.0, 0.01, 0.02, 0.04, 0.08)
    ]

    assert values == sorted(values)
    assert all(a < b for a, b in zip(values, values[1:]))


def test_dsr_falls_strictly_with_more_trials():
    """Die Korrektur selbst. Faellt dieser Test, tut das Modul nichts.

    Dieselbe Renditereihe, dieselbe Laenge -- nur die Zahl der Versuche
    waechst, aus denen dieser Gewinner stammt. Genau das ist der Grund,
    warum die DSR laut ADR-005 ab Tag 1 mitlaeuft: ohne diese Abhaengigkeit
    ist sie nur ein umstaendlich geschriebener Sharpe.
    """
    values = [
        deflated_sharpe_ratio(0.04, n, 5_000, -0.3, 6.0)
        for n in (1, 2, 5, 10, 50, 100, 500, 5_000, 100_000)
    ]

    assert all(a > b for a, b in zip(values, values[1:]))


def test_dsr_rises_with_a_longer_track_record():
    """Mehr Evidenz fuer eine Kante, die den Massstab schlaegt."""
    values = [
        deflated_sharpe_ratio(0.03, 100, length, -0.3, 6.0)
        for length in (100, 500, 1_000, 5_000, 20_000)
    ]

    assert all(a < b for a, b in zip(values, values[1:]))


def test_more_evidence_cuts_both_ways():
    """Unter dem Massstab senkt ein laengerer Track Record die DSR.

    Kein Widerspruch zum Test darueber, sondern dieselbe Aussage: mehr
    Beobachtungen machen das Urteil sicherer, nicht freundlicher. Wer einen
    Sharpe unterhalb dessen liefert, was der beste Zufall aus 100 Versuchen
    ohnehin erreicht, wird mit jedem weiteren Bar ueberzeugender widerlegt.

    Hier mit fest vorgegebenem `sharpe_std`, weil der H0-Default den
    Massstab selbst mit sqrt(T) schrumpfen laesst -- dann gewinnt jeder
    positive Sharpe irgendwann, und der Fall waere nicht darstellbar.
    """
    values = [
        deflated_sharpe_ratio(0.05, 100, length, 0.0, 3.0, sharpe_std=0.05)
        for length in (100, 500, 1_000, 5_000)
    ]

    assert all(a > b for a, b in zip(values, values[1:]))


def test_negative_skew_and_fat_tails_lower_the_dsr():
    """Die Nicht-Normalitaetskorrektur wirkt, und zwar in diese Richtung.

    Beides vergroessert den Standardfehler des Sharpe-Schaetzers: eine Reihe
    mit seltenen grossen Verlusten hat einen unzuverlaessigeren Sharpe, als
    ihr Punktschaetzer behauptet. Ein gedrehtes Vorzeichen beim
    Schiefe-Term wuerde genau hier auffallen und sonst nirgends.
    """
    normal = deflated_sharpe_ratio(0.04, 100, 5_000, 0.0, 3.0)
    left_skewed = deflated_sharpe_ratio(0.04, 100, 5_000, -1.0, 3.0 + 1.0)
    fat_tailed = deflated_sharpe_ratio(0.04, 100, 5_000, 0.0, 12.0)
    right_skewed = deflated_sharpe_ratio(0.04, 100, 5_000, 1.0, 3.0 + 1.0)

    assert left_skewed < normal
    assert fat_tailed < normal
    assert right_skewed > normal


# ----------------------------------------------------------------------
# Der Massstab selbst
# ----------------------------------------------------------------------


def test_expected_max_grows_with_the_number_of_trials():
    values = [expected_max_sharpe(n) for n in (1, 2, 5, 10, 100, 1_000, 10_000)]

    assert values[0] == 0.0
    assert all(a < b for a, b in zip(values, values[1:]))


def test_expected_max_scales_linearly_with_the_trial_dispersion():
    """SR0 ist die Ordnungsstatistik mal Streuung -- nichts weiter.

    Diese Linearitaet ist der Grund, warum die Frequenzkonvention
    durchschlaegt: wer `sharpe_std` in annualisierten Einheiten uebergibt,
    bekommt einen um sqrt(bars_per_year) zu grossen Massstab.
    """
    unit = expected_max_sharpe(250)

    assert expected_max_sharpe(250, sharpe_std=0.02) == pytest.approx(0.02 * unit)
    assert expected_max_sharpe(250, sharpe_std=0.0) == 0.0


@pytest.mark.parametrize("n_trials", [10, 100, 1_000])
def test_expected_max_is_close_to_the_simulated_maximum(n_trials):
    """Gegen Monte Carlo statt gegen die Formel -- die einzige echte Referenz.

    `expected_max_sharpe` ist eine Naeherung an den Erwartungswert des
    Maximums von N Standardnormal-Ziehungen. Ob sie das wirklich naehert,
    kann nur eine Simulation sagen: eine zweite Herleitung derselben
    Naeherung wuerde denselben Approximationsfehler machen.
    """
    rng = np.random.default_rng(20260826)
    simulated = rng.standard_normal((4_000, n_trials)).max(axis=1).mean()

    assert expected_max_sharpe(n_trials) == pytest.approx(simulated, rel=0.05)


def test_the_approximation_is_weakest_for_two_trials():
    """Bei N=2 liegt die Naeherung rund 9% zu tief -- bekannt und akzeptiert.

    Festgehalten, damit niemand die Abweichung spaeter fuer einen Bug haelt:
    die Gumbel-Grenzverteilung ist eine asymptotische Aussage ueber viele
    Ziehungen, und zwei sind nicht viele. Die Richtung ist die harmlose --
    ein zu niedriger Massstab urteilt milder, und bei zwei Versuchen ist
    Selektionsverzerrung ohnehin kein Thema.
    """
    rng = np.random.default_rng(20260826)
    simulated = rng.standard_normal((40_000, 2)).max(axis=1).mean()

    approximation = expected_max_sharpe(2)

    assert approximation < simulated
    assert approximation == pytest.approx(simulated, rel=0.12)


# ----------------------------------------------------------------------
# Konventionen: rohe Woelbung, Sharpe je Bar
# ----------------------------------------------------------------------


def test_kurtosis_is_the_raw_convention_not_the_excess_one():
    """Normalverteilung heisst hier 3,0 -- und 0,0 wird abgelehnt.

    Der teuerste Tippfehler dieses Moduls: `scipy.stats.kurtosis` liefert
    per Default die Excess-Woelbung. Wer sie durchreicht, rechnet im Nenner
    mit (0-1)/4 statt (3-1)/4 und bekommt fuer jede Strategie eine zu hohe
    DSR -- ohne Fehlermeldung. Die Konvention muss deshalb an einem Wert
    haengen, der ohne die Formel nachpruefbar ist.
    """
    sharpe, length = 0.06, 4_000
    # Bei Normalitaet reduziert sich der Standardfehler auf die Form von
    # Lo (2002): sqrt((1 + SR^2/2) / (T-1)). Von Hand hingeschrieben.
    expected = stats.norm.cdf(
        sharpe * math.sqrt(length - 1) / math.sqrt(1.0 + sharpe**2 / 2.0)
    )

    assert deflated_sharpe_ratio(
        sharpe, 1, length, 0.0, kurtosis=3.0
    ) == pytest.approx(expected)

    with pytest.raises(ValueError, match="ROHE Woelbung"):
        deflated_sharpe_ratio(sharpe, 1, length, 0.0, kurtosis=0.0)


def test_moments_from_returns_use_the_raw_convention():
    """`dsr_from_returns` liefert 3 fuer normale Renditen, nicht 0."""
    rng = np.random.default_rng(7)
    returns = rng.normal(0.0005, 0.01, 20_000)

    result = dsr_from_returns(returns, n_trials=10)

    assert result.kurtosis == pytest.approx(3.0, abs=0.15)
    assert result.excess_kurtosis == pytest.approx(0.0, abs=0.15)
    assert result.skewness == pytest.approx(0.0, abs=0.1)


def test_fat_tailed_returns_show_a_much_higher_kurtosis():
    """Eine t-verteilte Reihe muss auch wirklich fette Enden haben.

    Die Fixture ist der Test: eine mit normalverteilten Innovationen
    gebaute "Fat-Tail"-Reihe hat keine fetten Enden, und ein Test darauf
    haette bestanden, ohne irgendetwas zu pruefen -- der Fehler ist in
    diesem Projekt schon einmal passiert. Deshalb steht hier die Woelbung
    der Fixture selbst als Zusicherung, bevor ueberhaupt eine DSR gerechnet
    wird.
    """
    rng = np.random.default_rng(11)
    fat = rng.standard_t(4, 20_000)

    result = dsr_from_returns(fat * 0.01 + 0.0005, n_trials=10)

    assert result.kurtosis > 6.0


def symmetrised(sample: np.ndarray, mean: float, std: float) -> np.ndarray:
    """Reihe an null gespiegelt, dann auf Mittelwert und Streuung gesetzt.

    Die Spiegelung macht die Schiefe **exakt** null (zu jedem x liegt -x in
    der Reihe), laesst die Woelbung aber unberuehrt. Nur so laesst sich der
    Effekt der fetten Enden isoliert messen: die zufaellige Stichprobenschiefe
    einer t-Reihe wirkt im Standardfehler staerker als ihre Woelbung und
    wuerde den Vergleich sonst dominieren.
    """
    mirrored = np.concatenate([sample, -sample])
    return mirrored / mirrored.std(ddof=1) * std + mean


def test_fat_tails_cost_dsr_at_an_identical_sharpe():
    """Gleicher Sharpe, gleiche Laenge, gleiche Schiefe -- nur dickere Enden.

    Beide Reihen werden auf denselben Mittelwert, dieselbe Streuung und
    Schiefe null gebracht. Der einzige verbleibende Unterschied ist die
    Woelbung, der Vergleich misst also wirklich die
    Nicht-Normalitaetskorrektur und nicht eine zufaellig bessere Rendite.

    Der Abstand ist klein, und das ist kein Fehler, sondern eine
    Eigenschaft der Frequenzkonvention: der Woelbungsterm im Standardfehler
    ist `(g4 - 1)/4 * SR^2` und damit zweiter Ordnung in einem Sharpe **je
    Bar**, der typischerweise bei 0,03 liegt. Bei Bar-Frequenz dominiert die
    Selektionskorrektur; sichtbar wird die Momentenkorrektur erst bei
    ausgepraegter Schiefe (dort geht sie linear in SR ein, siehe
    `test_negative_skew_and_fat_tails_lower_the_dsr`).
    """
    rng = np.random.default_rng(4)
    mean, std = 0.000384, 0.012
    normal = symmetrised(rng.standard_normal(10_000), mean, std)
    fat = symmetrised(rng.standard_t(4, 10_000), mean, std)

    thin_result = dsr_from_returns(normal, n_trials=100)
    fat_result = dsr_from_returns(fat, n_trials=100)

    assert fat_result.sharpe == pytest.approx(thin_result.sharpe, rel=1e-9)
    assert abs(fat_result.skewness) < 1e-9
    assert abs(thin_result.skewness) < 1e-9
    assert thin_result.kurtosis < 3.2
    assert fat_result.kurtosis > 6.0
    assert fat_result.dsr < thin_result.dsr
    assert thin_result.dsr < 0.999  # sonst prueft der Vergleich nur 1,0 < 1,0


def test_annualisation_does_not_touch_the_dsr():
    """`periods_per_year` ist Bericht, nicht Rechnung.

    Die eingebaute Absicherung gegen den stillen Frequenzfehler aus ADR-020:
    haette die Annualisierung Einfluss auf das Ergebnis, gaebe es zwei
    Zahlen, die zueinander passen muessen -- und irgendwann tun sie es
    nicht mehr.
    """
    rng = np.random.default_rng(5)
    returns = rng.normal(0.0006, 0.011, 6_000)

    plain = dsr_from_returns(returns, n_trials=50)
    annualised = dsr_from_returns(
        returns, n_trials=50, periods_per_year=bars_per_year("4h")
    )

    assert annualised.dsr == plain.dsr
    assert annualised.sharpe == plain.sharpe
    assert math.isnan(plain.annualised_sharpe)
    assert annualised.annualised_sharpe == pytest.approx(
        plain.sharpe * math.sqrt(bars_per_year("4h"))
    )


def test_dsr_from_returns_equals_the_explicit_call():
    """Ein Ergebnis, zwei Wege -- sie duerfen nicht auseinanderlaufen."""
    rng = np.random.default_rng(9)
    returns = rng.normal(0.0004, 0.009, 3_000)

    result = dsr_from_returns(returns, n_trials=250)

    assert result.dsr == pytest.approx(
        deflated_sharpe_ratio(
            result.sharpe,
            250,
            result.track_record_length,
            result.skewness,
            result.kurtosis,
        )
    )
    assert result.expected_max == pytest.approx(
        expected_max_sharpe(250, result.sharpe_std)
    )


# ----------------------------------------------------------------------
# Das Szenario aus ADR-005
# ----------------------------------------------------------------------


def test_the_same_sharpe_survives_ten_trials_and_fails_five_hundred():
    """Der Fall, um den es in ADR-005 geht -- als Zahl.

    Ein annualisierter Sharpe von 2,0 ueber drei Jahre 4h-Bars. Als
    Einzelidee getestet ist er ueberzeugend; als Gewinner einer Suche ueber
    500 Kandidaten ist er ungefaehr das, was reines Rauschen ohnehin
    liefert. Renditereihe, Laenge und Sharpe sind in beiden Faellen
    identisch -- nur die Zahl der Versuche unterscheidet sich, und genau
    die zaehlt die Registry mit.
    """
    length = 3 * round(bars_per_year("4h"))
    sharpe = 2.0 / math.sqrt(bars_per_year("4h"))

    ten = deflated_sharpe_ratio(sharpe, 10, length, 0.0, 3.0)
    five_hundred = deflated_sharpe_ratio(sharpe, 500, length, 0.0, 3.0)

    assert ten > 0.95
    assert five_hundred < 0.8
    assert 0.5 < five_hundred


def test_the_best_of_pure_noise_lands_at_one_half():
    """Kalibrierung gegen simulierte Nullhypothese -- der Test ohne Formel.

    Beide Implementierungen in dieser Datei sind aus derselben Literatur
    hergeleitet. Teilen sie einen Denkfehler, faellt er in ihrem Vergleich
    nicht auf. Dieser Test kommt deshalb ganz ohne Formel aus: er baut die
    Welt, fuer die die DSR gedacht ist -- N Strategien, von denen **keine**
    eine Kante hat -- und schaut, was das Modul ueber den Gewinner sagt.

    Erwartung, direkt aus der Konstruktion: der beste aus N Rauschversuchen
    liegt per Definition ungefaehr auf dem Massstab "bester aus N
    Rauschversuchen". Seine DSR muss also um 0,5 streuen, und praktisch nie
    die 0,95-Schwelle reissen. Genau das ist die Zusage aus ADR-005:
    Overfitting-Gewinner kommen hier nicht durch.
    """
    rng = np.random.default_rng(20260826)
    n_trials, length, experiments = 40, 600, 150

    winners = []
    for _ in range(experiments):
        noise = rng.normal(0.0, 0.01, (n_trials, length))
        sharpes = noise.mean(axis=1) / noise.std(axis=1, ddof=1)
        best = noise[int(np.argmax(sharpes))]
        winners.append(dsr_from_returns(best, n_trials=n_trials).dsr)

    values = np.array(winners)

    assert float(np.median(values)) == pytest.approx(0.5, abs=0.1)
    assert float((values >= 0.95).mean()) < 0.02


def test_a_real_edge_still_gets_through():
    """Die Gegenprobe: ein Gate, das nie jemanden durchlaesst, ist keines.

    Eine Reihe mit echter, deutlicher Kante ueber einen langen Track Record
    besteht auch gegen 1000 Versuche.
    """
    rng = np.random.default_rng(1)
    edge = rng.normal(0.0008, 0.01, 8_000)

    assert dsr_from_returns(edge, n_trials=1_000).passed()


def test_passed_reads_the_threshold_as_a_probability():
    result = DSRResult(
        dsr=0.96,
        sharpe=0.04,
        n_trials=100,
        track_record_length=5_000,
        skewness=0.0,
        kurtosis=3.0,
        expected_max=0.02,
        sharpe_std=0.014,
    )

    assert result.passed() is True
    assert result.passed(0.99) is False
    assert "durchgefallen" not in result.describe()

    with pytest.raises(ValueError, match="0.95, nicht 95"):
        result.passed(95)


# ----------------------------------------------------------------------
# Fehlerfaelle: werfen statt nan
# ----------------------------------------------------------------------


@pytest.mark.parametrize("n_trials", [0, -5])
def test_too_few_trials_raise(n_trials):
    with pytest.raises(ValueError, match="n_trials"):
        deflated_sharpe_ratio(0.04, n_trials, 1_000, 0.0, 3.0)


def test_a_fractional_trial_count_raises():
    with pytest.raises(ValueError, match="ganze Zahl"):
        expected_max_sharpe(2.5)


@pytest.mark.parametrize("length", [1, 0, -10])
def test_too_short_a_track_record_raises(length):
    with pytest.raises(ValueError, match="track_record_length"):
        deflated_sharpe_ratio(0.04, 100, length, 0.0, 3.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sharpe": float("nan")},
        {"sharpe": float("inf")},
        {"skewness": float("nan")},
        {"kurtosis": float("inf")},
    ],
)
def test_non_finite_inputs_raise(kwargs):
    args = {
        "sharpe": 0.04,
        "n_trials": 100,
        "track_record_length": 1_000,
        "skewness": 0.0,
        "kurtosis": 3.0,
    }
    args.update(kwargs)

    with pytest.raises(ValueError, match="endlich"):
        deflated_sharpe_ratio(**args)


def test_impossible_moment_combinations_raise():
    """kurtosis >= 1 + skewness^2 gilt fuer jede Verteilung."""
    with pytest.raises(ValueError, match="unmoeglich"):
        deflated_sharpe_ratio(0.04, 100, 1_000, skewness=3.0, kurtosis=4.0)


def test_a_constant_return_series_raises():
    """Standardabweichung 0 -- meist eine Strategie, die nie gehandelt hat."""
    with pytest.raises(ValueError, match="Konstante Renditereihe"):
        dsr_from_returns(np.zeros(500), n_trials=10)


def test_a_series_too_short_for_moments_raises():
    with pytest.raises(ValueError, match="mindestens 4"):
        dsr_from_returns(np.array([0.01, -0.02, 0.005]), n_trials=10)


def test_non_finite_returns_raise():
    returns = np.full(100, 0.01)
    returns[42] = np.nan

    with pytest.raises(ValueError, match="Nicht-endliche Renditen"):
        dsr_from_returns(returns, n_trials=10)


def test_a_nonsensical_annualisation_raises():
    rng = np.random.default_rng(2)

    with pytest.raises(ValueError, match="periods_per_year"):
        dsr_from_returns(rng.normal(0, 0.01, 500), n_trials=10, periods_per_year=0.0)
