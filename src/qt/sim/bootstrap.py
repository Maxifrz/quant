"""Bootstrap-Generatoren fuer Pfad-Ensembles.

Aufgabe: aus einer historischen Renditereihe viele plausible Zukuenfte
erzeugen. Der naive Weg -- einzelne Renditen unabhaengig ziehen -- zerstoert
genau die zwei Eigenschaften, die Finanzzeitreihen ausmachen:

* **Autokorrelation**: Trends und Mean-Reversion leben davon, dass die
  Reihenfolge der Renditen nicht beliebig ist. Wuerfelt man sie neu, ist
  jede Trendstrategie im Ensemble per Konstruktion wertlos.
* **Volatilitaets-Clustering**: auf einen grossen Tag folgt ueberdurch-
  schnittlich oft ein weiterer grosser Tag. Ein i.i.d.-Ensemble verteilt die
  grossen Tage gleichmaessig ueber den Horizont, Verluste draengen sich darin
  nicht mehr zusammen.

  Wo das kostet, ist gemessen und nicht vermutet (siehe
  `test_iid_ensemble_understates_short_horizon_tail_risk`): der schlimmste
  20-Bar-Abschnitt eines Pfades faellt im i.i.d.-Ensemble um rund ein Achtel
  milder aus. Beim 5%-Quantil der **End**rendite ueber 250 Bars ist dagegen
  kein Unterschied zu sehen -- ueber einen langen Horizont mittelt sich die
  Vol-Mischung wieder aus. Die uebliche Formel "i.i.d. unterschaetzt die
  Tails" gilt also fuer kurzfristige Verlustserien und Drawdowns, nicht
  pauschal fuer jede Kennzahl. Fuer eine CVaR-Nebenbedingung ist das der
  entscheidende Teil, weil sie ueber einen Zwischenstand ausgeloest wird und
  nicht erst am Ende des Horizonts.

Der **stationaere Bootstrap** (Politis & Romano 1994) loest das, indem er
zusammenhaengende Bloecke zieht: innerhalb eines Blocks bleibt die
Originalreihenfolge erhalten, und damit auch die lokale Abhaengigkeits-
struktur. Der Kern des Verfahrens ist die **geometrisch verteilte**
Blocklaenge. Bei fester Blocklaenge haengt die Verteilung der simulierten
Reihe davon ab, wo im Block man steht -- die Blockgrenzen erzeugen eine
kuenstliche Periodizitaet mit der Blocklaenge als Periode. Die geometrische
Laenge ist gedaechtnislos, deshalb ist jede Position gleichwertig und die
erzeugte Reihe ist stationaer. Das ist der ganze Unterschied zum
gewoehnlichen Block-Bootstrap und der Grund fuer den Namen.

`IIDBootstrap` steht als Kontrast daneben, nicht als ernsthafte Alternative:
ohne den direkten Vergleich waere "erhaelt Autokorrelation und
Vol-Clustering" eine Behauptung statt einer gemessenen Eigenschaft. Die
Tests in `tests/test_sim_bootstrap.py` messen den Unterschied.

Bewusst **keine Registry** in diesem Modul: eine `qt.sim`-Registry gehoert in
ein eigenes Modul (wie `qt.strategy.registry`), weil `regime.py` und
`scenarios.py` sich sonst bei `bootstrap.py` registrieren muessten und das
Bootstrap-Modul zum Knotenpunkt der ganzen Phase wuerde. Solange es zwei
Generatoren gibt, ist der direkte Import ehrlicher.
"""

from __future__ import annotations

import numpy as np

from qt.sim.base import PathEnsemble, PathGenerator, validate_history

# Ziehungen je Verarbeitungsblock. ~2 Mio Indizes sind gross genug, dass der
# Python-Overhead der Blockschleife verschwindet, und klein genug, dass die
# Zwischenmatrizen nicht bei jedem Block frisch vom Betriebssystem angefordert
# werden muessen.
_CHUNK_CELLS = 1 << 21


def _check_request(horizon: int, n_paths: int) -> tuple[int, int]:
    """Horizont und Pfadzahl pruefen.

    Beides muss positiv sein. `PathEnsemble` faengt ein leeres Ergebnis zwar
    ab, wuerde aber ueber die Form klagen statt ueber den Aufruf -- und
    `horizon=0` ist ein Aufruffehler, kein Datenfehler.
    """
    horizon = int(horizon)
    n_paths = int(n_paths)
    if horizon < 1:
        raise ValueError(f"horizon muss mindestens 1 sein, ist aber {horizon}.")
    if n_paths < 1:
        raise ValueError(f"n_paths muss mindestens 1 sein, ist aber {n_paths}.")
    return horizon, n_paths


class _BootstrapBase(PathGenerator):
    """Gemeinsames Geruest beider Bootstraps.

    Beide unterscheiden sich ausschliesslich darin, **welche Indizes** sie in
    die Historie ziehen. Validierung, Zentrierung, Ensemble-Bau und Metadaten
    sind identisch -- stuenden sie zweimal da, wuerde eine spaetere Korrektur
    verlaesslich nur an einer der beiden Stellen ankommen.
    """

    def __init__(self, demean: bool = False, allow_gaps: bool = False) -> None:
        self.demean = bool(demean)
        # Eine Datenluecke ist per Default ein Fehler (siehe validate_history).
        # Durchgereicht, damit ein Aufrufer sie bewusst hinnehmen kann, ohne
        # die Historie vorher selbst filtern zu muessen -- und damit die
        # Entscheidung im Aufruf sichtbar bleibt statt im Vorverarbeiten.
        self.allow_gaps = bool(allow_gaps)

    def _min_history(self) -> int:
        return 32

    def _draw_indices(
        self, n_history: int, horizon: int, n_paths: int, rng: np.random.Generator
    ) -> np.ndarray:
        """Indexmatrix (n_paths, horizon) in die Historie."""
        raise NotImplementedError

    def generate(
        self, returns: np.ndarray, horizon: int, n_paths: int, seed: int
    ) -> PathEnsemble:
        horizon, n_paths = _check_request(horizon, n_paths)
        history = validate_history(
            returns, min_len=self._min_history(), allow_gaps=self.allow_gaps
        )

        # Eigener Generator statt np.random.seed(): globaler Zustand macht das
        # Ergebnis davon abhaengig, was vorher im Prozess gelost hat -- ein
        # Ensemble, das je nach Aufrufreihenfolge anders ausfaellt, ist als
        # Entscheidungsgrundlage wertlos (siehe PathGenerator-Vertrag).
        rng = np.random.default_rng(seed)

        hist_mean = float(history.mean())
        sample = history - hist_mean if self.demean else history

        idx = self._draw_indices(len(history), horizon, n_paths, rng)
        paths = sample[idx]

        return PathEnsemble(
            paths=paths,
            meta={
                "generator": self.name,
                "seed": int(seed),
                "n_history": int(len(history)),
                "demean": self.demean,
                # Der historische Mittelwert bleibt auch bei demean=True
                # sichtbar: was zentriert wurde, soll nachvollziehbar sein
                # statt spurlos zu verschwinden.
                "history_mean": hist_mean,
                "history_std": float(history.std(ddof=1)),
            },
        )


class StationaryBootstrap(_BootstrapBase):
    """Stationaerer Bootstrap nach Politis & Romano (1994).

    Verfahren: bei jedem Schritt wird mit Wahrscheinlichkeit `p = 1/mean_block`
    ein neuer Block an einer gleichverteilt gezogenen Stelle begonnen, sonst
    laeuft der aktuelle Block eine Position weiter. Die Blocklaenge ist damit
    geometrisch verteilt mit Erwartungswert `mean_block`.

    **Zirkulaer.** Laeuft ein Block ueber das Ende der Historie hinaus, geht es
    vorne weiter (Modulo). Das ist keine Bequemlichkeit, sondern korrigiert
    einen Bias: ohne Wrap kann die vorletzte Beobachtung nur noch von genau
    zwei Blockpositionen aus erreicht werden, die letzte nur von einer. Die
    juengsten Beobachtungen -- also ausgerechnet die aktuelle Marktphase --
    waeren systematisch untergewichtet. Mit Wrap hat jede Beobachtung exakt
    dieselbe Ziehwahrscheinlichkeit.

    Die Wahl von `mean_block` ist ein Zielkonflikt: zu klein zerstoert die
    Abhaengigkeitsstruktur (bei `mean_block=1` ist das Verfahren exakt der
    i.i.d.-Bootstrap), zu gross erzeugt Pfade, die nur noch verschobene
    Kopien der Historie sind und damit ein Ensemble ohne echte Streuung.
    Default 20: bei 4h-Bars gut drei Tage, lang genug fuer ein Vol-Cluster
    und kurz genug, dass ein 720-Bar-Pfad aus vielen Bloecken besteht.

    `demean` ist per Default **aus**, siehe `__init__`.
    """

    name = "stationary_bootstrap"

    def __init__(
        self, mean_block: int = 20, demean: bool = False, allow_gaps: bool = False
    ) -> None:
        """
        `mean_block`: erwartete Blocklaenge in Bars.

        `demean`: die Historie vor dem Ziehen zentrieren.

        Zum Default `demean=False`. Der Zielkonflikt ist echt: der Bootstrap
        traegt den historischen Mittelwert unveraendert in die Zukunft. Stammt
        die Historie aus einem Bullenmarkt, hat **jeder** simulierte Pfad eine
        eingebaute Aufwaertsdrift, und jede Allokationsentscheidung darueber
        ist optimistisch verzerrt -- bei Krypto-Historien ein grosser Effekt.

        Trotzdem ist `False` der Default, aus drei Gruenden:

        1. `demean=True` ist keine neutrale Korrektur, sondern die
           Gegenannahme: erwartete Rendite exakt null. Das ist genauso eine
           Behauptung ueber die Zukunft wie "der Bullenmarkt geht weiter",
           nur in die andere Richtung. Unter einer CVaR-Nebenbedingung wuerde
           sie die Zielfunktion zuverlaessig in Richtung "gar nicht
           investieren" druecken -- eine stille Verzerrung, die niemand als
           Entscheidung wahrnimmt, weil sie im Default steckt.
        2. Ein Resampling-Verfahren, das seine Eingabe stillschweigend
           veraendert, ist eine Falle: wer die Ensemble-Statistik gegen die
           Historie haelt und Abweichungen findet, sucht den Fehler zuerst
           im Code.
        3. Die Driftannahme gehoert dorthin, wo sie sichtbar ist -- in den
           Aufruf und, per Szenario-Prior, in die Umgewichtung des Ensembles.
           Genau dafuer existiert `PathEnsemble.reweighted`.

        Die Konsequenz ist, dass der Aufrufer sich entscheiden muss. `meta`
        haelt fest, wie er sich entschieden hat.
        """
        super().__init__(demean=demean, allow_gaps=allow_gaps)
        mean_block = int(mean_block)
        if mean_block < 1:
            raise ValueError(
                f"mean_block muss mindestens 1 sein, ist aber {mean_block}. "
                f"(1 entspricht dem i.i.d.-Bootstrap.)"
            )
        self.mean_block = mean_block

    def _min_history(self) -> int:
        """Untergrenze fuer brauchbare Historie.

        Ueber das allgemeine Minimum von 32 hinaus wird verlangt, dass die
        Historie mehrere mittlere Blocklaengen umfasst. Sonst deckt ein
        einzelner Block schon einen grossen Teil der Reihe ab, alle Pfade
        bestehen aus wenigen fast identischen Stuecken, und das Ensemble
        taeuscht eine Streuung vor, die es nicht hat.
        """
        return max(32, 3 * self.mean_block)

    def _draw_indices(
        self, n_history: int, horizon: int, n_paths: int, rng: np.random.Generator
    ) -> np.ndarray:
        # Vektorisiert ueber Pfade *und* Zeit. Eine Schleife ueber Pfade
        # kostet bei 10.000 x 720 rund 7,2 Mio Python-Iterationen und ist
        # damit fuer `qt sim --paths 10000` unbrauchbar.
        #
        # Der Trick, der die sequentielle Fortschreibung aufloest: ein Block,
        # der bei t0 mit Index b beginnt, liefert bei t den Index
        # b + (t - t0). Der gesuchte Index ist also ueberall `offset + t` mit
        # `offset = b - t0` -- und dieses offset ist innerhalb eines Blocks
        # konstant. Aus "fortschreiben" wird damit "Offset der letzten
        # Startposition nach vorne fuellen", und Forward-Fill ist
        # `maximum.accumulate` ueber die Positionen der Blockanfaenge.
        steps = np.arange(horizon, dtype=np.int32)
        out = np.empty((n_paths, horizon), dtype=np.int32)

        # In Bloecken von Pfaden statt am Stueck. Der Grund ist Speicher, nicht
        # Geschwindigkeit an sich: am Stueck braucht das Verfahren mehrere
        # Zwischenmatrizen der vollen Groesse (n_paths x horizon) gleichzeitig,
        # der Spitzenbedarf waechst also linear mit der Pfadzahl. Mit fester
        # Blockgroesse bleibt er konstant, und die immer gleich grossen Puffer
        # werden vom Allokator wiederverwendet. Auf der Testmaschine ist das
        # bei 10.000 x 720 der Unterschied zwischen unter einer Sekunde und
        # ueber 30 Sekunden -- dort ist das Anfordern frischer Seiten teurer
        # als das Rechnen darauf. Der Faktor ist maschinenabhaengig, die
        # Richtung nicht. int32 aus demselben Grund: Indizes in eine Historie
        # von ein paar tausend Werten brauchen keine 64 Bit.
        rows = max(1, min(n_paths, _CHUNK_CELLS // horizon))
        for lo in range(0, n_paths, rows):
            hi = min(lo + rows, n_paths)
            shape = (hi - lo, horizon)

            offset = rng.integers(0, n_history, size=shape, dtype=np.int32)
            offset -= steps  # nur an Startpositionen gueltig

            # Ein Blockanfang mit Wahrscheinlichkeit p = 1/mean_block. Als
            # ganzzahlige Ziehung statt `rng.random() < p`, weil p hier per
            # Konstruktion der Kehrwert einer ganzen Zahl ist -- das trifft
            # die Wahrscheinlichkeit exakt und spart eine float64-Matrix.
            starts = rng.integers(0, self.mean_block, size=shape, dtype=np.int32) == 0
            starts[:, 0] = True  # der erste Schritt ist immer ein Blockanfang

            pos = np.where(starts, steps, np.int32(0))
            np.maximum.accumulate(pos, axis=1, out=pos)

            chunk = np.take_along_axis(offset, pos, axis=1)
            chunk += steps
            # Modulo statt Abschneiden: das ist das zirkulaere Ziehen. Der
            # Offset kann negativ sein (Block beginnt spaet und laeuft ueber
            # das Ende), numpys Modulo liefert auch dann einen Index in [0, n).
            chunk %= n_history
            out[lo:hi] = chunk

        return out


class IIDBootstrap(_BootstrapBase):
    """Einzelne Renditen unabhaengig ziehen -- der Kontrastfall.

    Existiert nicht, weil er gut waere, sondern damit sich messen laesst, was
    der stationaere Bootstrap leistet: dieselbe Randverteilung der
    Einzelrenditen, aber jede zeitliche Struktur zerstoert. Autokorrelation
    und Vol-Clustering fallen auf null.

    Als Nebeneffekt ist er die ehrliche Referenz fuer die Frage "wie viel
    Tail-Risiko wuerde man uebersehen" -- der Unterschied in CVaR zwischen
    beiden Ensembles auf derselben Historie ist genau der Betrag, den ein
    naives Verfahren unterschlaegt.
    """

    name = "iid_bootstrap"

    def _draw_indices(
        self, n_history: int, horizon: int, n_paths: int, rng: np.random.Generator
    ) -> np.ndarray:
        return rng.integers(0, n_history, size=(n_paths, horizon), dtype=np.int32)
