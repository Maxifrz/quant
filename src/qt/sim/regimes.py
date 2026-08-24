"""Volatilitaets- und Regimemodelle als Pfadgeneratoren.

Der Block-Bootstrap (`qt.sim.bootstrap`) resampelt die Vergangenheit. Das ist
seine Staerke -- er erfindet nichts -- und zugleich seine Grenze: er kann per
Konstruktion **nur Regime erzeugen, die schon vorkamen**. Die Volatilitaet,
die er produziert, ist ein Mosaik historischer Stuecke, kein Prozess. Wer
Tail-Risiko damit schaetzt, schaetzt implizit "der schlimmste Tag der Zukunft
ist so schlimm wie der schlimmste Tag der Historie". Das ist genau die
Annahme, an der Risikomodelle regelmaessig scheitern.

Zwei Modelle, die diese Luecke schliessen -- jedes auf seine Weise, und jedes
mit einer Schwaeche, die das andere nicht hat:

**GARCH(1,1)** modelliert Volatilitaet als *Prozess* mit Persistenz. Ein
Vol-Schock klingt langsam ab, statt sofort zum Mittelwert zurueckzuspringen.
Deshalb kann eine GARCH-Simulation Vol-Niveaus erreichen, die so nie
beobachtet wurden, aber aus der geschaetzten Dynamik folgen. Schwaeche: ein
einziger, glatter Vol-Prozess -- keine Spruenge, keine diskreten Zustaende.

**HMM** modelliert diskrete Regimewechsel (ruhig / turbulent, oder mehr
Zustaende) mit Uebergangswahrscheinlichkeiten. Es erzeugt Pfade, die
*zwischen* Regimen wechseln, statt eines gemittelten Dauerzustands. Schwaeche:
innerhalb eines Zustands ist die Rendite normalverteilt, die bedingten Tails
sind also duenn -- die dicken Tails entstehen erst aus der Mischung.

Beide sind hier ausdruecklich Ergaenzungen, kein Ersatz fuereinander und kein
Ersatz fuer den Bootstrap. Ein Ensemble aus mehreren Generatoren ist ehrlicher
als eines aus dem "besten" Modell.

## Zwei Fehlerquellen, die hier bewusst adressiert sind

**Skalierung.** `arch` rechnet konventionell auf Renditen in Prozent (also
x100). Der Grund ist numerisch: bei Rohrenditen der Groessenordnung 1e-3 liegt
`omega` bei 1e-8, und der Optimierer laeuft in Konditionsprobleme. Ein
vergessener Faktor 100 faellt nirgends als Fehler auf -- die Simulation laeuft
durch, nur sind alle Risikozahlen um zwei Groessenordnungen daneben. Deshalb
wird hier explizit rein- und wieder rausskaliert (`PERCENT`), `rescale=False`
gesetzt, damit `arch` nicht zusaetzlich still selbst skaliert, und die
Skalierung durch einen eigenen Test festgenagelt.

Fuer das HMM gilt dasselbe aus einem anderen Grund: `hmmlearn` hat einen
Kovarianz-Prior (`covars_prior=0.01`) in den Einheiten der Daten. Auf
Rohrenditen (Varianz ~1e-5) ist dieser Prior groesser als das Signal und
blaeht die geschaetzten Regime-Varianzen um Faktoren auf. In Prozent ist er
vernachlaessigbar. Also derselbe Faktor 100, aus voellig anderem Anlass.

**Determinismus.** `seed` ist laut `PathGenerator` Pflicht. Beide Pakete
brauchen dafuer unterschiedliche Behandlung:

- `arch`: die Simulation laeuft hier gar nicht ueber `arch`, sondern ueber
  eine eigene, vektorisierte GARCH-Rekursion mit `numpy.random.Generator`.
  `arch` schaetzt nur die Parameter -- und die Schaetzung ist ein
  deterministischer Optimierer ohne Zufall.
- `hmmlearn`: der Fit selbst ist zufallsabhaengig (k-Means-Initialisierung).
  Er bekommt deshalb ein **festes** `random_state` (`fit_seed`), das
  *nicht* am `seed` haengt -- sonst wuerde ein anderer Sampling-Seed auch die
  geschaetzten Regime verschieben, und "Zustand 1 ist das turbulente Regime"
  waere von Lauf zu Lauf eine andere Aussage. Der `seed` steuert
  ausschliesslich das Ziehen der Pfade.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.special import gammaln

from qt.sim.base import PathEnsemble, PathGenerator, validate_history

# `arch` erwartet Renditen in Prozent -- siehe Modul-Docstring. Als Konstante
# und nicht als literale 100.0 im Code, damit die Hin- und die Rueckrichtung
# nicht unabhaengig voneinander geaendert werden koennen.
PERCENT = 100.0

# Unterhalb dieser Standardabweichung (in Prozent, also 1e-8 in Rohrenditen)
# ist eine Reihe fuer beide Modelle degeneriert. Der Wert ist bewusst nicht
# exakt null: eine Reihe aus lauter 1e-15-Rauschen ist praktisch konstant,
# treibt die Optimierer aber trotzdem in undefiniertes Verhalten.
MIN_STD_PCT = 1e-6

# GARCH mit vier bis fuenf Parametern auf weniger als ein paar hundert Bars zu
# schaetzen liefert Zahlen, aber keine Information -- die Standardfehler von
# alpha und beta sind dann groesser als die Schaetzer selbst.
DEFAULT_MIN_HISTORY = 250

_DIST_ALIASES = {
    "normal": "normal",
    "gaussian": "normal",
    "t": "t",
    "studentst": "t",
    "skewt": "skewt",
    "skewstudent": "skewt",
}

# Was `arch_model(dist=...)` versteht. Getrennt von den Aliassen oben, weil
# die oeffentliche Schreibweise dieses Moduls nicht an der von `arch` haengen
# soll.
_ARCH_DIST = {"normal": "normal", "t": "t", "skewt": "skewstudent"}


class RegimeFitFailed(RuntimeError):
    """Ein Modell liess sich auf dieser Reihe nicht brauchbar schaetzen.

    Eigene Exception statt eines durchgereichten Paketfehlers, aus demselben
    Grund wie `ForecasterUnavailable` in der TimesFM-Strategie: der Aufrufer
    soll entscheiden koennen, ob er auf einen anderen Generator ausweicht,
    das Symbol ueberspringt oder abbricht. Dafuer muss der Fehlschlag ein
    benennbares Ereignis sein und keine beliebige `LinAlgError` aus der
    Tiefe eines Optimierers.

    Ein Fehlschlag ist hier ein normaler Betriebszustand, kein Bug: kurze
    Historien, konstante Reihen und Perioden ohne Vol-Variation kommen in
    einem Walk-Forward-Lauf regelmaessig vor.
    """


# --------------------------------------------------------------------------
# GARCH
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GARCHFit:
    """Geschaetzte GARCH-Parameter, alles in Prozent-Einheiten.

    `eps2_init` und `sigma2_init` sind der **Zustand am Ende der Historie**,
    neueste Beobachtung zuerst. Sie sind der eigentliche Grund, warum eine
    GARCH-Simulation etwas anderes ist als ziehen aus der unbedingten
    Verteilung: ein Pfad, der mitten in einem Vol-Cluster startet, beginnt
    auch turbulent. Wer stattdessen mit der langfristigen Varianz startet,
    beantwortet die Frage "wie sieht ein durchschnittliches Jahr aus" statt
    "was passiert als Naechstes".
    """

    mu: float
    omega: float
    alpha: np.ndarray
    beta: np.ndarray
    dist: str
    dist_params: np.ndarray
    eps2_init: np.ndarray
    sigma2_init: np.ndarray
    loglikelihood: float

    @property
    def persistence(self) -> float:
        """alpha + beta -- wie langsam ein Vol-Schock abklingt.

        Bei Krypto typisch ueber 0,95. Werte sehr nahe 1 bedeuten, dass die
        Simulation ueber lange Horizonte praktisch nicht mehr zur
        Ausgangsvolatilitaet zurueckkehrt; das ist eine Eigenschaft der Daten
        und keine Fehlfunktion, aber ein Grund, lange Horizonte skeptisch zu
        lesen.
        """
        return float(self.alpha.sum() + self.beta.sum())

    @property
    def uncond_vol_pct(self) -> float:
        """Unbedingte (langfristige) Volatilitaet in Prozent je Bar."""
        rest = 1.0 - self.persistence
        if rest <= 0:
            return float("nan")
        return float(np.sqrt(self.omega / rest))


class GARCHPaths(PathGenerator):
    """Pfade aus einem GARCH(p,q)-Prozess mit fettschwaenzigen Innovationen.

    ## Die Verteilungsannahme ist hier die wichtigste Entscheidung

    Default ist die standardisierte **Student-t**, nicht die Normalverteilung.
    Der Grund ist nicht Geschmack: GARCH erzeugt aus normalverteilten
    Innovationen zwar eine fettschwaenzige *unbedingte* Verteilung (weil die
    Varianz schwankt), aber die so entstehenden Tails sind fuer Krypto
    nachweislich zu duenn. Standardisierte GARCH-Residuen von BTC haben eine
    Kurtosis deutlich ueber 3; die geschaetzten Freiheitsgrade landen typisch
    im Bereich 3-6. Diese ganze Uebung dient der Tail-Risiko-Schaetzung -- eine
    zu duenne Verteilungsannahme macht sie nicht ungenau, sondern wertlos, und
    zwar in genau die Richtung, die weh tut (CVaR zu klein, Allokation zu
    gross).

    Warum trotzdem nicht Skew-t als Default: die Schiefe ist der Parameter mit
    dem groessten Standardfehler im Modell. Auf den hier ueblichen
    Stichprobengroessen ist ihre Schaetzung instabil, und ein instabil
    geschaetzter Schiefeparameter verschiebt die Quantile mal in die eine,
    mal in die andere Richtung -- er verschlechtert die Reproduzierbarkeit
    mehr, als er an Realismus bringt. `dist="skewt"` steht zur Verfuegung,
    wer die Asymmetrie ausdruecklich untersuchen will.

    `dist="normal"` existiert im Wesentlichen als Vergleichspunkt: der
    Unterschied in der Kurtosis der erzeugten Pfade zeigt, wieviel die
    Verteilungsannahme ausmacht.

    ## Was simuliert wird und was nicht

    Simuliert wird die Rekursion selbst -- mit den geschaetzten Parametern,
    aber eigenem Zufallszahlengenerator (siehe Modul-Docstring zum
    Determinismus). `arch` liefert nur die Schaetzung.

    Der geschaetzte konstante Mittelwert `mu` geht per Default in die Pfade
    ein (`include_mean=True`). Das ist eine bewusste, angreifbare Wahl: ein
    aus wenigen Jahren geschaetzter Drift hat einen enormen Standardfehler und
    dominiert ueber 720 Bars die Endrenditen. Ihn wegzulassen waere aber auch
    keine neutrale Entscheidung -- dann haette *jedes* Ensemble eine
    Medianrendite nahe null, und eine Zielfunktion "Median unter
    CVaR-Nebenbedingung" haette keinen Zaehler mehr. Wer den Drift nicht
    glaubt, setzt `include_mean=False` und bekommt reine Risikopfade.
    """

    name = "garch"

    def __init__(
        self,
        p: int = 1,
        q: int = 1,
        dist: str = "t",
        include_mean: bool = True,
        min_history: int = DEFAULT_MIN_HISTORY,
        max_iter: int = 200,
    ) -> None:
        # `p` ist bei `arch` die ARCH-Ordnung (Lags der quadrierten Residuen),
        # `q` die GARCH-Ordnung (Lags der Varianz) -- in der Literatur wird
        # das auch andersherum notiert. Hier gilt die arch-Konvention, weil
        # die Parameter direkt dorthin durchgereicht werden.
        if p < 1 or q < 0:
            raise ValueError("GARCH braucht p >= 1 und q >= 0.")
        key = _DIST_ALIASES.get(str(dist).lower())
        if key is None:
            raise ValueError(
                f"Unbekannte Verteilung {dist!r}. Erlaubt: "
                f"{sorted(set(_DIST_ALIASES))}."
            )
        if min_history < 32:
            raise ValueError("min_history unter 32 ist fuer GARCH nicht sinnvoll.")

        self.p = int(p)
        self.q = int(q)
        self.dist = key
        self.include_mean = bool(include_mean)
        self.min_history = int(min_history)
        self.max_iter = int(max_iter)
        self._memo: tuple[Any, GARCHFit] | None = None

    def describe(self) -> str:
        return f"garch({self.p},{self.q}) dist={self.dist}"

    # -- Schaetzung ---------------------------------------------------------

    def fit(self, returns: np.ndarray) -> GARCHFit:
        """Parameter schaetzen, mit einer Ergebnisabfrage statt blindem Vertrauen.

        Ein Ergebnis von `arch` bedeutet nicht, dass der Optimierer
        konvergiert ist -- bei degenerierten Reihen kommt ein vollstaendig
        besetztes Parameterobjekt zurueck, das aus den Startwerten besteht.
        Deshalb wird `convergence_flag` geprueft, statt sich auf das Ausbleiben
        einer Exception zu verlassen.
        """
        r = validate_history(
            returns, min_len=self.min_history, allow_gaps=getattr(self, "allow_gaps", False)
        )
        memo_key = self._memo_key(r)
        if self._memo is not None and self._memo[0] == memo_key:
            return self._memo[1]

        scaled = r * PERCENT
        _reject_degenerate(scaled, self.name)

        arch_model = _load_arch()
        try:
            # Warnungen des Pakets werden hier unterdrueckt und weiter unten
            # durch eine eigene, sprechende Exception ersetzt. Ungefiltert
            # waeren sie eine Zeile Rauschen pro Fit -- in einem
            # Walk-Forward-Lauf mit hunderten Fits macht das jede echte
            # Meldung unsichtbar. `np.errstate`, weil bei entarteten Reihen
            # innerhalb des Optimierers durch null geteilt wird, bevor
            # `arch` selbst das bemerkt.
            with warnings.catch_warnings(), np.errstate(all="ignore"):
                warnings.simplefilter("ignore")
                model = arch_model(
                    scaled,
                    mean="Constant",
                    vol="GARCH",
                    p=self.p,
                    q=self.q,
                    dist=_ARCH_DIST[self.dist],
                    # Ohne dies skaliert `arch` bei ungewoehnlichen
                    # Groessenordnungen selbst noch einmal und gibt die
                    # Parameter in *dieser* Skala zurueck. Zwei Skalierungen
                    # uebereinander sind genau der Faktor-100-Fehler, den
                    # dieses Modul vermeiden soll.
                    rescale=False,
                )
                res = model.fit(disp="off", show_warning=False, options={"maxiter": self.max_iter})
        except Exception as exc:  # noqa: BLE001 -- Paketfehler bewusst breit
            raise RegimeFitFailed(
                f"GARCH({self.p},{self.q})-Schaetzung fehlgeschlagen "
                f"({type(exc).__name__}: {exc})."
            ) from exc

        fit = self._extract(res)
        self._memo = (memo_key, fit)
        return fit

    def _extract(self, res: Any) -> GARCHFit:
        params = np.asarray(res.params, dtype=float)
        if not np.all(np.isfinite(params)):
            raise RegimeFitFailed(
                f"GARCH-Schaetzung lieferte nicht-endliche Parameter: {params}."
            )
        if int(getattr(res, "convergence_flag", 0)) != 0:
            raise RegimeFitFailed(
                f"GARCH-Optimierer nicht konvergiert (Code "
                f"{res.convergence_flag}) nach {self.max_iter} Iterationen. "
                "Typische Ursachen: zu kurze oder zu wenig variable Historie."
            )

        n_dist = int(res.model.distribution.num_params)
        mu = float(params[0])
        omega = float(params[1])
        alpha = params[2 : 2 + self.p].copy()
        beta = params[2 + self.p : 2 + self.p + self.q].copy()
        dist_params = (
            params[-n_dist:].copy() if n_dist else np.zeros(0, dtype=float)
        )

        if omega <= 0:
            raise RegimeFitFailed(
                f"GARCH-Schaetzung lieferte omega={omega:g}. Eine "
                "nicht-positive Grundvarianz heisst, dass die Reihe fuer das "
                "Modell keine Streuung hat."
            )
        persistence = float(alpha.sum() + beta.sum())
        if persistence >= 1.0:
            raise RegimeFitFailed(
                f"GARCH-Prozess nicht stationaer (alpha+beta={persistence:.4f} "
                ">= 1). Simulierte Varianz waechst dann unbegrenzt."
            )

        resid = np.asarray(res.resid, dtype=float)
        cvol = np.asarray(res.conditional_volatility, dtype=float)
        # Neueste zuerst: eps2_init[0] ist Lag 1, passend zu alpha[0].
        eps2_init = (resid[::-1][: self.p] ** 2).copy()
        sigma2_init = (cvol[::-1][: max(self.q, 1)] ** 2).copy()
        if not np.all(np.isfinite(eps2_init)) or not np.all(np.isfinite(sigma2_init)):
            raise RegimeFitFailed(
                "GARCH-Endzustand (Residuen/bedingte Vola) ist nicht endlich."
            )

        return GARCHFit(
            mu=mu,
            omega=omega,
            alpha=alpha,
            beta=beta,
            dist=self.dist,
            dist_params=dist_params,
            eps2_init=eps2_init,
            sigma2_init=sigma2_init,
            loglikelihood=float(res.loglikelihood),
        )

    def _memo_key(self, r: np.ndarray) -> Any:
        # Der Fit ist der teure Teil, das Sampling ist billig. Ein Aufrufer,
        # der dasselbe Symbol mit zehn verschiedenen Seeds simuliert, soll
        # nicht zehnmal schaetzen. Ein einziger Eintrag genuegt: der uebliche
        # Ablauf ist "ein Symbol, viele Seeds", nicht "viele Symbole
        # abwechselnd".
        return (r.shape, self.p, self.q, self.dist, r.tobytes())

    # -- Simulation ---------------------------------------------------------

    def generate(
        self, returns: np.ndarray, horizon: int, n_paths: int, seed: int
    ) -> PathEnsemble:
        _check_shape(horizon, n_paths)
        fit = self.fit(returns)
        rng = np.random.default_rng(seed)

        # (horizon, n_paths): Speicherlayout, siehe `_garch_recursion`.
        z = _draw_innovations(rng, fit.dist, fit.dist_params, (horizon, n_paths))
        paths_pct = _garch_recursion(fit, z, include_mean=self.include_mean)

        if not np.all(np.isfinite(paths_pct)):
            raise RegimeFitFailed(
                "GARCH-Simulation divergiert (nicht-endliche Renditen). Das "
                "passiert bei Persistenz sehr nahe 1 in Kombination mit sehr "
                "wenigen Freiheitsgraden."
            )

        # Rueckskalierung, in-place und vor dem Transponieren: das Gegenstueck
        # zum `* PERCENT` im Fit -- der Faktor, dessen Vergessen nirgends
        # fehlschlaegt und alle Risikozahlen um zwei Groessenordnungen
        # verschiebt.
        paths_pct /= PERCENT
        paths = np.ascontiguousarray(paths_pct.T)

        return PathEnsemble(
            paths=paths,
            meta={
                "generator": self.name,
                "model": f"GARCH({self.p},{self.q})",
                "dist": fit.dist,
                "dist_params": fit.dist_params.tolist(),
                "mu": fit.mu / PERCENT,
                "persistence": fit.persistence,
                "uncond_vol": fit.uncond_vol_pct / PERCENT,
                "start_vol": float(np.sqrt(fit.sigma2_init[0])) / PERCENT,
                "include_mean": self.include_mean,
                "seed": int(seed),
            },
        )


def _garch_recursion(
    fit: GARCHFit, z: np.ndarray, include_mean: bool
) -> np.ndarray:
    """Vektorisierte GARCH-Rekursion ueber alle Pfade gleichzeitig.

    Die Schleife laeuft ueber den Horizont, nicht ueber die Pfade -- die
    Rekursion ist in der Zeit sequentiell (sigma2_t haengt an sigma2_{t-1}),
    ueber Pfade hinweg aber vollstaendig unabhaengig. Damit sind es bei
    10.000 x 720 genau 720 Numpy-Operationen auf Vektoren der Laenge 10.000
    statt 7,2 Millionen Skalarschritten.

    `z`, `out` und der Rueckgabewert liegen deshalb als (horizon, n_paths)
    und nicht als (n_paths, horizon): der Zeitschritt ist die aeussere Achse,
    also greift jede Iteration auf eine zusammenhaengende Zeile zu. Mit der
    anderen Anordnung waere jeder Schritt ein Spaltenzugriff mit 80 KB
    Schrittweite -- gemessen kostet das bei 10.000 x 720 rund 14 statt 0,4
    Sekunden, bei identischem Ergebnis. Transponiert wird einmal beim
    Aufrufer, zusammen mit der Rueckskalierung.
    """
    horizon, n_paths = z.shape
    p, q = len(fit.alpha), len(fit.beta)

    eps2_lags = np.tile(fit.eps2_init, (n_paths, 1))
    sig2_lags = np.tile(fit.sigma2_init[: max(q, 1)], (n_paths, 1))
    out = np.empty((horizon, n_paths), dtype=float)

    for t in range(horizon):
        sigma2 = fit.omega + eps2_lags @ fit.alpha
        if q:
            sigma2 = sigma2 + sig2_lags[:, :q] @ fit.beta
        eps = np.sqrt(sigma2) * z[t]
        out[t] = eps

        if p > 1:
            eps2_lags = np.roll(eps2_lags, 1, axis=1)
        eps2_lags[:, 0] = eps * eps
        if q:
            if q > 1:
                sig2_lags = np.roll(sig2_lags, 1, axis=1)
            sig2_lags[:, 0] = sigma2

    if include_mean:
        out += fit.mu
    return out


def _draw_innovations(
    rng: np.random.Generator,
    dist: str,
    params: np.ndarray,
    size: tuple[int, int],
) -> np.ndarray:
    """Standardisierte Innovationen (Mittelwert 0, Varianz 1).

    Bewusst nicht ueber `arch`s eigene `Distribution.ppf`: die geht bei t und
    Skew-t ueber `scipy.stats.t.ppf`, und das kostet bei 7,2 Mio. Werten
    (10.000 x 720) rund 30-50 Sekunden gegenueber unter zwei Sekunden hier.
    Ausserdem wuerde der Zufall dann an `arch`s interner `RandomState`
    haengen statt am uebergebenen `seed`.
    """
    if dist == "normal":
        return rng.standard_normal(size)

    nu = float(params[0])
    if nu <= 2.0:
        raise RegimeFitFailed(
            f"Geschaetzte Freiheitsgrade nu={nu:.3f} <= 2. Die Verteilung hat "
            "dann keine endliche Varianz, die Skalierung auf Varianz 1 ist "
            "nicht definiert."
        )

    if dist == "t":
        return _standard_t(rng, nu, size)

    # Skew-t nach Hansen (1994), so wie `arch` sie parametrisiert. Aus dem
    # Quellcode von `SkewStudent.ppf` gelesen: die Inverse ist stueckweise
    # eine t-Quantilfunktion, links mit (1-lam), rechts mit (1+lam) skaliert,
    # anschliessend um a verschoben und durch b geteilt. Das ist genau eine
    # zweiteilige Verteilung -- und die laesst sich direkt ziehen, ohne die
    # teure Quantilfunktion: Vorzeichen mit P(+) = (1+lam)/2, Betrag aus einer
    # standardisierten t. Numerisch gegen `SkewStudent.ppf` geprueft, die
    # Quantile stimmen auf drei Nachkommastellen ueberein.
    lam = float(params[1])
    log_c = gammaln((nu + 1) / 2) - gammaln(nu / 2) - np.log(np.pi * (nu - 2)) / 2
    a = 4 * lam * np.exp(log_c) * (nu - 2) / (nu - 1)
    b = np.sqrt(1 + 3 * lam**2 - a**2)

    magnitude = np.abs(_standard_t(rng, nu, size))
    sign = np.where(rng.random(size) < (1 + lam) / 2, 1.0, -1.0)
    return (sign * (1 + sign * lam) * magnitude - a) / b


def _standard_t(
    rng: np.random.Generator, nu: float, size: tuple[int, int]
) -> np.ndarray:
    """Student-t mit nu Freiheitsgraden, auf Varianz 1 standardisiert.

    Zwei Punkte, die beide leicht falsch gemacht werden:

    Erstens die Standardisierung. Eine rohe t_nu hat Varianz nu/(nu-2) -- bei
    nu=4 also 2. Wer sie ungeteilt in `sigma_t * z_t` einsetzt, bekommt eine
    um 41% zu hohe Volatilitaet, und zwar ohne dass irgendetwas fehlschlaegt.

    Zweitens der Weg dorthin. `rng.standard_t` ist fuer 7,2 Mio. Werte
    (10.000 x 720) rund zehnmal langsamer als die aequivalente Darstellung
    ueber eine Normal- und eine Chi-Quadrat-Groesse (gemessen 3,5 s gegen
    0,3 s). t = Z / sqrt(G/nu) mit G ~ chi2_nu; mit dem
    Standardisierungsfaktor sqrt((nu-2)/nu) kuerzt sich nu heraus und uebrig
    bleibt Z * sqrt((nu-2)/G). Chi2_nu wird als 2*Gamma(nu/2) gezogen, weil
    `standard_gamma` reelle Freiheitsgrade zulaesst -- nu ist ein
    geschaetzter Parameter und praktisch nie ganzzahlig.
    """
    # In-place gerechnet: bei 10.000 x 720 ist jede dieser Matrizen 58 MB
    # gross. Die naive Schreibweise haelt vier davon gleichzeitig, und der
    # erste Aufruf zahlt das in Seitenfehlern -- gemessen mehrere Sekunden,
    # bevor ueberhaupt gerechnet wird.
    scale = rng.standard_gamma(nu / 2.0, size)
    scale *= 2.0 / (nu - 2.0)  # G / (nu - 2), der Nenner der standardisierten t
    np.sqrt(scale, out=scale)
    z = rng.standard_normal(size)
    z /= scale
    return z


# --------------------------------------------------------------------------
# HMM
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HMMFit:
    """Geschaetztes Regimemodell, **nach Varianz aufsteigend sortiert**.

    Die Sortierung ist kein Kosmetikschritt. `hmmlearn` nummeriert Zustaende
    danach, wo die k-Means-Initialisierung sie hingelegt hat -- Zustand 0 ist
    mal das ruhige, mal das turbulente Regime, und das kann sich schon bei
    einem Bar mehr Historie umdrehen. Jede Aussage der Form "Zustand 0 hat
    Wahrscheinlichkeit 0,8" waere ohne feste Ordnung nicht reproduzierbar
    interpretierbar -- nicht falsch, sondern bedeutungslos.

    Konvention hier: **Index 0 ist immer das ruhigste Regime**, der hoechste
    Index immer das turbulenteste. Alles -- `means`, `variances`, `transmat`
    (Zeilen *und* Spalten) und `start` -- ist konsistent umsortiert.

    Alle Werte in Prozent-Einheiten, wie bei `GARCHFit`.
    """

    means: np.ndarray
    variances: np.ndarray
    transmat: np.ndarray
    start: np.ndarray
    loglikelihood: float
    converged: bool
    n_iter: int

    @property
    def n_states(self) -> int:
        return int(len(self.means))

    def expected_durations(self) -> np.ndarray:
        """Erwartete Verweildauer je Zustand in Bars: 1 / (1 - p_ii).

        Die aussagekraeftigste Einzelzahl eines Regimemodells. Ein Zustand mit
        erwarteter Dauer 1,2 Bars ist kein Regime, sondern ein Ausreisser-Topf
        -- ein Hinweis, dass `n_states` zu hoch gewaehlt ist.
        """
        stay = np.clip(np.diag(self.transmat), 0.0, 1.0 - 1e-12)
        return 1.0 / (1.0 - stay)


class HMMRegimePaths(PathGenerator):
    """Pfade aus einem Gauss-HMM mit diskreten Regimewechseln.

    Ein HMM unterstellt einen verborgenen Zustand (ruhig / turbulent / ...),
    der einer Markovkette folgt, und je Zustand eine eigene Normalverteilung
    fuer die Rendite. Was es dem Bootstrap und dem GARCH voraus hat: es
    erzeugt **Wechsel**. Ein Pfad kann 200 Bars ruhig laufen und dann in ein
    turbulentes Regime springen -- kein gemittelter Dauerzustand und kein
    glatter Vol-Verlauf, sondern ein Bruch.

    ## Startzustand

    Ein simulierter Pfad beginnt im *aktuellen* Regime, nicht in der
    stationaeren Verteilung des Modells. Die Frage lautet "was passiert als
    Naechstes", nicht "was passiert im langfristigen Mittel" -- und wer heute
    mitten in einem Vol-Spike steht, dessen naechste 30 Bars sehen anders aus
    als der Durchschnitt der letzten drei Jahre.

    Umgesetzt ueber die Posterior-Wahrscheinlichkeit des **letzten**
    Beobachtungspunkts (`predict_proba(X)[-1]`): kein harter Zustand, sondern
    die Unsicherheit darueber, in welchem Regime wir gerade sind. Aus diesem
    Zustand heraus wird dann regulaer ein Uebergang gezogen, bevor der erste
    Bar emittiert wird -- der erste simulierte Bar ist t+1, nicht t.

    Das von `hmmlearn` mitgeschaetzte `startprob_` wird dabei bewusst
    verworfen: es beschreibt den Zustand am *Anfang* der Historie und ist
    fuer eine Fortsetzung an ihrem Ende ohne jeden Bezug.

    ## Grenze, die man kennen muss

    Innerhalb eines Zustands ist die Rendite normalverteilt. Die dicken Tails
    eines HMM-Ensembles entstehen ausschliesslich aus der Mischung der
    Regime. Fuer einen einzelnen extremen Bar *innerhalb* eines ruhigen
    Regimes hat das Modell keine Vorstellung -- dafuer ist `GARCHPaths` mit
    t-Innovationen zustaendig. Die beiden gehoeren zusammen ins Ensemble.
    """

    name = "hmm"

    def __init__(
        self,
        n_states: int = 2,
        min_history: int = DEFAULT_MIN_HISTORY,
        n_iter: int = 500,
        tol: float = 1e-4,
        fit_seed: int = 0,
    ) -> None:
        if n_states < 2:
            raise ValueError(
                "Ein HMM mit einem Zustand ist eine Normalverteilung -- "
                "n_states muss mindestens 2 sein."
            )
        if min_history < 32:
            raise ValueError("min_history unter 32 ist fuer ein HMM nicht sinnvoll.")

        self.n_states = int(n_states)
        self.min_history = int(min_history)
        self.n_iter = int(n_iter)
        self.tol = float(tol)
        # Fest und ausdruecklich nicht am Sampling-`seed`: siehe
        # Modul-Docstring. Die geschaetzten Regime sollen ueber Laeufe hinweg
        # dieselben sein, sonst ist "Zustand 0" keine stabile Aussage.
        self.fit_seed = int(fit_seed)
        self._memo: tuple[Any, HMMFit] | None = None

    def describe(self) -> str:
        return f"hmm({self.n_states} Zustaende)"

    # -- Schaetzung ---------------------------------------------------------

    def fit(self, returns: np.ndarray) -> HMMFit:
        r = validate_history(
            returns, min_len=self.min_history, allow_gaps=getattr(self, "allow_gaps", False)
        )
        memo_key = (r.shape, self.n_states, self.fit_seed, r.tobytes())
        if self._memo is not None and self._memo[0] == memo_key:
            return self._memo[1]

        # Faktor 100 hier aus einem anderen Grund als bei GARCH: `hmmlearn`
        # hat einen Kovarianz-Prior in Dateneinheiten (`covars_prior=0.01`).
        # Auf Rohrenditen ist dieser Prior groesser als die tatsaechliche
        # Varianz und blaeht die Regime-Varianzen sichtbar auf (gemessen:
        # Faktor ~1,6 auf einer Testreihe). In Prozent ist er
        # vernachlaessigbar.
        scaled = (r * PERCENT).reshape(-1, 1)
        _reject_degenerate(scaled.ravel(), self.name)

        GaussianHMM = _load_hmmlearn()
        try:
            with warnings.catch_warnings(), np.errstate(all="ignore"):
                # `hmmlearn` und das darunterliegende sklearn melden bei
                # entarteten Reihen ueber Warnungen ("Number of distinct
                # clusters found smaller than n_clusters"), nicht ueber
                # Exceptions. Sie werden hier geschluckt und weiter unten
                # durch eigene Pruefungen ersetzt -- eine Warnung, die der
                # Aufrufer nicht abfangen kann, ist keine brauchbare
                # Fehlerbehandlung.
                warnings.simplefilter("ignore")
                model = GaussianHMM(
                    n_components=self.n_states,
                    # 1D-Emission: 'diag' und 'full' sind hier identisch,
                    # 'diag' ist der billigere Weg dorthin.
                    covariance_type="diag",
                    n_iter=self.n_iter,
                    tol=self.tol,
                    random_state=self.fit_seed,
                )
                model.fit(scaled)
                posterior = np.asarray(model.predict_proba(scaled)[-1], dtype=float)
        except Exception as exc:  # noqa: BLE001 -- Paketfehler bewusst breit
            raise RegimeFitFailed(
                f"HMM-Schaetzung mit {self.n_states} Zustaenden fehlgeschlagen "
                f"({type(exc).__name__}: {exc})."
            ) from exc

        fit = self._extract(model, posterior)
        self._memo = (memo_key, fit)
        return fit

    def _extract(self, model: Any, posterior: np.ndarray) -> HMMFit:
        means = np.asarray(model.means_, dtype=float).ravel()
        variances = np.asarray(model.covars_, dtype=float).reshape(self.n_states, -1)[
            :, 0
        ]
        transmat = np.asarray(model.transmat_, dtype=float)

        for label, arr in (
            ("Mittelwerte", means),
            ("Varianzen", variances),
            ("Uebergangsmatrix", transmat),
            ("Posterior", posterior),
        ):
            if not np.all(np.isfinite(arr)):
                raise RegimeFitFailed(
                    f"HMM-Schaetzung lieferte nicht-endliche {label}."
                )
        if np.any(variances <= 0):
            raise RegimeFitFailed(
                f"HMM-Zustand mit nicht-positiver Varianz: {variances}. Aus "
                "einem solchen Zustand laesst sich nicht ziehen."
            )
        if not np.allclose(transmat.sum(axis=1), 1.0, atol=1e-6):
            raise RegimeFitFailed(
                f"Zeilen der Uebergangsmatrix summieren sich nicht auf 1: "
                f"{transmat.sum(axis=1)}."
            )
        if not getattr(model.monitor_, "converged", False):
            raise RegimeFitFailed(
                f"HMM-EM nicht konvergiert nach {self.n_iter} Iterationen "
                f"(tol={self.tol:g}). Ein abgebrochener EM-Lauf liefert "
                "Zustaende, die nicht als Regime interpretierbar sind."
            )

        # Der Kern: feste Ordnung nach Varianz. Zeilen UND Spalten der
        # Uebergangsmatrix muessen mit -- eine nur zeilenweise permutierte
        # Matrix waere still falsch und wuerde sich als plausible Zahlen
        # tarnen.
        order = np.argsort(variances, kind="stable")

        return HMMFit(
            means=means[order],
            variances=variances[order],
            transmat=transmat[np.ix_(order, order)],
            start=posterior[order],
            loglikelihood=float(model.monitor_.history[-1]),
            converged=True,
            n_iter=int(model.monitor_.iter),
        )

    # -- Simulation ---------------------------------------------------------

    def generate(
        self, returns: np.ndarray, horizon: int, n_paths: int, seed: int
    ) -> PathEnsemble:
        _check_shape(horizon, n_paths)
        fit = self.fit(returns)
        rng = np.random.default_rng(seed)

        sds = np.sqrt(fit.variances)
        cum_trans = np.cumsum(fit.transmat, axis=1)

        # Startzustand aus der Posterior des letzten beobachteten Bars.
        start_cum = np.cumsum(fit.start / fit.start.sum())
        states = np.searchsorted(start_cum, rng.random(n_paths), side="right")
        states = np.clip(states, 0, fit.n_states - 1)

        # Alle Zufallszahlen im Voraus: ein einziger Aufruf je Groesse ist
        # deutlich schneller als 720 kleine, und die Reihenfolge der Ziehungen
        # liegt damit fest -- Voraussetzung fuer bitgleiche Wiederholbarkeit.
        # Zeitachse aussen (horizon, n_paths), damit jede Iteration auf eine
        # zusammenhaengende Zeile zugreift -- dieselbe Ueberlegung wie in
        # `_garch_recursion`. Transponiert wird einmal am Ende.
        u = rng.random((horizon, n_paths))
        z = rng.standard_normal((horizon, n_paths))

        # In der Schleife wird nur die Zustandskette fortgeschrieben. Die
        # Emission -- zwei Nachschlagevorgaenge in `means`/`sds` -- passiert
        # danach in einem Rutsch ueber die ganze Matrix. In der Schleife
        # gemacht kostete sie bei 10.000 x 720 rund 2,5 der 3 Sekunden, weil
        # 720 winzige Nachschlagevorgaenge teurer sind als zwei grosse.
        state_path = np.empty((horizon, n_paths), dtype=np.int16)
        for t in range(horizon):
            # Erst Uebergang, dann Emission: der erste simulierte Bar ist t+1.
            # Vektorisierte Inverse-CDF ueber alle Pfade: zaehlt je Pfad, wie
            # viele Zustaende links der gezogenen Uniformen liegen.
            states = (cum_trans[states] < u[t, :, None]).sum(axis=1)
            np.clip(states, 0, fit.n_states - 1, out=states)
            state_path[t] = states

        out = fit.means[state_path] + sds[state_path] * z

        paths = out.T / PERCENT  # Gegenstueck zur Skalierung im Fit.

        return PathEnsemble(
            paths=paths,
            meta={
                "generator": self.name,
                "model": f"GaussianHMM({fit.n_states})",
                "state_vols": (sds / PERCENT).tolist(),
                "state_means": (fit.means / PERCENT).tolist(),
                "transmat": fit.transmat.tolist(),
                "start_posterior": fit.start.tolist(),
                "expected_durations": fit.expected_durations().tolist(),
                "fit_seed": self.fit_seed,
                "seed": int(seed),
            },
        )


# --------------------------------------------------------------------------
# Gemeinsames
# --------------------------------------------------------------------------


def _check_shape(horizon: int, n_paths: int) -> None:
    if horizon < 1:
        raise ValueError("horizon muss mindestens 1 sein.")
    if n_paths < 1:
        raise ValueError("n_paths muss mindestens 1 sein.")


def _reject_degenerate(scaled: np.ndarray, model: str) -> None:
    """Entartete Reihen abfangen, bevor ein Optimierer sie sieht.

    Beide Pakete verhalten sich bei konstanten Reihen unschoen, aber
    unterschiedlich unschoen: `arch` produziert eine Kaskade von
    Division-durch-null-Warnungen und gibt am Ende die Startwerte zurueck,
    `hmmlearn` meldet ueberhaupt keinen Fehler und liefert einen Zustand mit
    einer aus dem Kovarianz-Prior erfundenen Varianz. Beides ist schlimmer
    als ein Fehlschlag, weil es wie ein Ergebnis aussieht.
    """
    std = float(np.std(scaled))
    if not np.isfinite(std) or std < MIN_STD_PCT:
        raise RegimeFitFailed(
            f"Reihe fuer {model} entartet: Standardabweichung {std:g}% je Bar. "
            "Eine (nahezu) konstante Renditereihe enthaelt keine Volatilitaet, "
            "die sich modellieren liesse."
        )


def _load_arch() -> Any:
    """`arch` erst beim Schaetzen importieren.

    Regulaere Abhaengigkeit, aber ein schwergewichtiger Import (zieht
    `statsmodels`, `scipy.optimize` und `pandas` nach, gut eine Sekunde).
    Wer `qt.sim.regimes` nur importiert, um `RegimeFitFailed` abzufangen oder
    einen Generator zu konstruieren, soll das nicht bezahlen -- dieselbe
    Haltung wie beim Lazy-Import in der TimesFM-Strategie, dort aus dem
    dringenderen Grund einer fehlenden optionalen Abhaengigkeit.
    """
    try:
        from arch import arch_model
    except ImportError as exc:  # pragma: no cover -- regulaere Abhaengigkeit
        raise RegimeFitFailed(
            "Das Paket `arch` fehlt. Installieren mit: uv sync."
        ) from exc
    return arch_model


def _load_hmmlearn() -> Any:
    try:
        from hmmlearn.hmm import GaussianHMM
    except ImportError as exc:  # pragma: no cover -- regulaere Abhaengigkeit
        raise RegimeFitFailed(
            "Das Paket `hmmlearn` fehlt. Installieren mit: uv sync."
        ) from exc
    return GaussianHMM
