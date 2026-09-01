"""Forecast-Strategie auf Basis von Googles TimesFM.

TimesFM (github.com/google-research/timesfm) ist ein vortrainiertes
Zeitreihen-Foundation-Model: es sagt Fortsetzungen einer Zahlenreihe voraus,
ohne fuer diese Reihe trainiert worden zu sein (Zero-Shot-Forecasting). Diese
Datei macht daraus eine gewoehnliche Strategie im Sinne von ADR-002 -- sie
gibt ein Zielgewicht aus, nichts weiter. Positionsgroesse, Kosten und
Risikogrenzen bleiben unveraendert Sache des Allokators und der Risk-Engine.

## Was ins Modell geht

Log-Renditen, keine Preise. Ein Kurs von 43.211,50 waere ein Fingerabdruck,
an dem sich Symbol und ungefaehre Periode raten liessen -- eine Renditereihe
ist zumindest massstabsfrei.

## Das ungeloeste Problem: Lookahead durch Pretraining

Bei der LLM-Allokation (ADR-003, ADR-017) laesst sich Lookahead durch
Anonymisierung entschaerfen: das Modell sieht Kennzahlen statt Rohdaten, und
ein Label wie "STRAT_A" verraet nichts. Das funktioniert hier nicht.

TimesFM wurde auf einem grossen, oeffentlichen und nicht vollstaendig
dokumentierten Korpus vortrainiert. Ob und in welchem Umfang historische
Krypto-Kursreihen darin enthalten waren, ist von aussen nicht feststellbar.
Die Eingabe hier *ist* die Rohreihe -- es gibt keine Kennzahl, hinter der
man den tatsaechlichen Zahlenverlauf verstecken koennte, ohne dem Modell
genau die Information zu nehmen, die es fuer eine Vorhersage braucht.

Ein Backtest dieser Strategie auf Zeitraeumen, die im Pretraining gewesen sein
koennten, ist damit **strukturell nicht vertrauenswuerdig** -- nicht wegen
eines Bugs, sondern wegen der Natur eines Foundation Models. Es gibt dagegen
keine Abhilfe in diesem Modul. Was es gibt:

- Diese Warnung, gut sichtbar, statt eines beruhigenden Kommentars.
- Dieselbe Konsequenz wie beim LLM-Allokator: **belastbar ist nur
  Forward-Paper-Trading**, nicht der Backtest. Ein guter Backtest-Sharpe
  dieser Strategie beweist nichts.
- Wer will, kann versuchen, den Trainings-Cutoff des verwendeten Checkpoints
  zu ermitteln und ausschliesslich danach zu testen -- das reduziert das
  Risiko, beseitigt es aber nicht (der Cutoff ist nicht verlaesslich
  dokumentiert, und Kursverlaeufe wiederholen sich strukturell auch ohne
  wortwoertliches Auswendiglernen).

## Warum nicht bei jedem Bar neu vorhersagen

Ein 200M-Parameter-Transformer braucht pro Aufruf spuerbare Zeit -- Sekunden,
nicht Millisekunden, selbst auf CPU. Bei zehntausenden Bars in einem Backtest
ist ein Aufruf pro Bar nicht praktikabel. `forecast_every` legt den Takt fest,
der Rest der Zeit wird das zuletzt berechnete Gewicht gehalten -- dieselbe
Idee wie `allocate_every` in der Portfolio-Engine.

## Ausfall ist ein langweiliges Ereignis

Fehlt das Paket, das Netzwerk oder das Modellgewicht, faellt die Strategie auf
das zuletzt bekannte Gewicht zurueck (0.0 vor dem ersten erfolgreichen
Forecast) und meldet das genau einmal per `warnings.warn`, statt bei jedem
Bar erneut zu scheitern oder den Lauf abzubrechen -- dieselbe Haltung wie
beim LLM-Allokator (ADR-018).
"""

from __future__ import annotations

import math
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

from qt.features.registry import FeatureStore
from qt.strategy.base import Strategy
from qt.strategy.registry import register

DEFAULT_CONTEXT_LEN = 512
DEFAULT_HORIZON = 16
DEFAULT_CHECKPOINT = "google/timesfm-2.5-200m-pytorch"

# Round-Trip-Kosten laut ADR-009 (Coinbase Taker, BTC/USD 4h) liegen bei rund
# 90bps. Der Default-Schwellenwert ist bewusst das Doppelte: eine Vorhersage
# ist unsicherer als eine gemessene Gebuehr, und ein Modell, das nur knapp
# ueber den Kosten liegt, verdient keinen Trade.
DEFAULT_MIN_EDGE_BPS = 180.0


class ForecasterUnavailable(RuntimeError):
    """Das Paket `timesfm` fehlt, oder das Modell/der Aufruf ist gescheitert."""


@dataclass(frozen=True, slots=True)
class ForecastResult:
    """Vorhersage ueber den Horizont, in Log-Renditen je Schritt.

    `median` ist die Punktprognose (bei TimesFM der 0,5-Quantil-Pfad, nicht
    der Mittelwert -- robuster gegen einzelne Ausreisser im Kontext).
    `q10`/`q90` sind optional: nicht jeder Forecaster liefert Quantile, und
    ohne sie wird konservativ mit halber Staerke gehandelt (`_confidence`).
    """

    median: np.ndarray
    q10: np.ndarray | None = None
    q90: np.ndarray | None = None


class Forecaster(ABC):
    """Austauschbare Vorhersagequelle.

    Als Abstraktion, damit die Strategie ohne das `timesfm`-Paket und ohne
    heruntergeladene Modellgewichte test- und importierbar bleibt -- derselbe
    Schnitt wie `qt.llm.client.AllocatorClient`/`StubClient`.
    """

    @abstractmethod
    def forecast(self, context: np.ndarray, horizon: int) -> ForecastResult:
        """Vorhersage aus einer Reihe von Log-Renditen (keine Preise)."""


class NaiveForecaster(Forecaster):
    """Random-Walk-Vorhersage: 0 erwartete Rendite, keine Aussage zur Unsicherheit.

    Ein Random Walk hat per Definition keine erwartete Richtung -- das ist
    die neutrale Grundannahme, gegen die sich jede Vorhersage behaupten
    muesste. Dient als Default in Tests und als ehrlicher Platzhalter, wo
    kein Modellzugang besteht.
    """

    def forecast(self, context: np.ndarray, horizon: int) -> ForecastResult:
        return ForecastResult(median=np.zeros(horizon))


class TimesFMForecaster(Forecaster):
    """Wrapper um TimesFM 2.5 (google/timesfm-2.5-200m-pytorch, PyTorch-Backend).

    Weder `timesfm` noch `torch` noch das Checkpoint (mehrere hundert MB,
    Download von huggingface.co beim ersten Lauf) werden beim Bau dieses
    Objekts oder beim Modulimport angefasst -- ausschliesslich beim ersten
    `forecast()`-Aufruf. Ein Testlauf oder `qt strategies` braucht damit
    weder das Paket noch einen Netzwerkzugang.

    Die Zuordnung der zehn Quantil-Spalten, die TimesFM zurueckgibt, steht in
    keiner mir vorliegenden Dokumentation explizit -- ermittelt durch Lesen
    des Quellcodes (`timesfm_2p5_torch.py`, `_compiled_decode`): Spalte 0 ist
    ein interner Rest ohne definierte Bedeutung fuer Aufrufer, Spalten 1-9
    sind die Quantile 0,1 bis 0,9 in dieser Reihenfolge, Spalte 5 ist damit
    der Median und identisch mit dem separat zurueckgegebenen Punkt-Forecast.
    Ohne diese Pruefung waere die naheliegende Annahme "Spalte 0 = 10.
    Perzentil" falsch gewesen.
    """

    def __init__(
        self,
        checkpoint: str = DEFAULT_CHECKPOINT,
        max_context: int = 1024,
        max_horizon: int = 256,
    ) -> None:
        self.checkpoint = checkpoint
        self.max_context = max_context
        self.max_horizon = max_horizon
        self._model = None

    def forecast(self, context: np.ndarray, horizon: int) -> ForecastResult:
        model = self._ensure_model()
        try:
            _point, quantiles = model.forecast(horizon=horizon, inputs=[context])
        except Exception as exc:
            raise ForecasterUnavailable(
                f"TimesFM-Aufruf fehlgeschlagen ({type(exc).__name__}): {exc}"
            ) from exc

        q = np.asarray(quantiles[0], dtype=float)
        if q.ndim != 2 or q.shape[1] < 10:
            # Unerwartete Form -- z.B. eine zukuenftige Paketversion mit
            # anderer Spaltenzahl. Lieber ohne Quantile weiterrechnen als
            # stillschweigend falsche Spalten zu lesen.
            return ForecastResult(median=np.asarray(_point[0], dtype=float))

        return ForecastResult(median=q[:, 5], q10=q[:, 1], q90=q[:, 9])

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        try:
            import timesfm
        except ImportError as exc:
            raise ForecasterUnavailable(
                "Das Paket `timesfm` fehlt. Installieren mit: "
                "uv sync --extra timesfm (schwergewichtig, zieht torch nach -- "
                "deshalb keine Standardabhaengigkeit, siehe pyproject.toml)."
            ) from exc
        try:
            model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(self.checkpoint)
            model.compile(
                timesfm.ForecastConfig(
                    max_context=self.max_context,
                    max_horizon=self.max_horizon,
                    normalize_inputs=True,
                    use_continuous_quantile_head=True,
                    force_flip_invariance=True,
                    # Log-Renditen sind vorzeichenbehaftet -- anders als bei
                    # Preisen oder Volumen darf das Modell sie nicht auf
                    # Nichtnegativitaet zwingen.
                    infer_is_positive=False,
                    fix_quantile_crossing=True,
                )
            )
        except Exception as exc:
            raise ForecasterUnavailable(
                f"TimesFM-Checkpoint {self.checkpoint!r} konnte nicht geladen "
                f"werden ({type(exc).__name__}: {exc}). Braucht Netzwerkzugang "
                "beim ersten Lauf (Download von huggingface.co)."
            ) from exc
        self._model = model
        return model


@dataclass(slots=True)
class ForecastTelemetry:
    """Wieviele Bars tatsaechlich neu vorhergesagt statt aus dem Cache bedient
    wurden -- und wie oft der Forecaster ausgefallen ist.

    Ohne diese Zaehlung faellt nicht auf, wenn `forecast_every` so niedrig
    gewaehlt ist, dass ein Backtest Stunden statt Minuten braucht, oder wenn
    der Forecaster durchgehend ausfaellt und die Strategie in Wirklichkeit
    die ganze Zeit flach war.
    """

    calls: int = 0
    cached: int = 0
    failures: int = 0

    def summary(self) -> str:
        return (
            f"Forecasts {self.calls}, aus Cache bedient {self.cached}, "
            f"fehlgeschlagen {self.failures}"
        )


@register
class TimesFMStrategy(Strategy):
    """Zielgewicht aus einer TimesFM-Vorhersage der kumulativen Rendite.

    Long, wenn die vorhergesagte kumulative Log-Rendite ueber dem Horizont
    die Kostenschwelle uebersteigt, short spiegelbildlich (falls erlaubt),
    sonst flach. Die Positionsgroesse skaliert mit der Konfidenz der
    Vorhersage (Betrag relativ zur Quantil-Spannweite), nicht binaer.

    Siehe Modul-Docstring fuer die Grenzen dieser Strategie -- insbesondere
    das ungeloeste Lookahead-Risiko durch das Pretraining des Modells.
    """

    name = "timesfm"

    def __init__(
        self,
        symbols: list[str],
        timeframe: str,
        forecaster: Forecaster | None = None,
        context_len: int = DEFAULT_CONTEXT_LEN,
        horizon: int = DEFAULT_HORIZON,
        forecast_every: int = 24,
        min_edge_bps: float = DEFAULT_MIN_EDGE_BPS,
        max_weight: float = 1.0,
        allow_short: bool = True,
    ) -> None:
        if context_len < 32:
            raise ValueError("context_len sollte mindestens 32 Bars sein.")
        if horizon < 1:
            raise ValueError("horizon muss mindestens 1 sein.")
        if forecast_every < 1:
            raise ValueError("forecast_every muss mindestens 1 sein.")

        super().__init__(
            symbols,
            timeframe,
            context_len=context_len,
            horizon=horizon,
            forecast_every=forecast_every,
            min_edge_bps=min_edge_bps,
            max_weight=max_weight,
            allow_short=allow_short,
        )
        # Default-Forecaster grosszuegig kompiliert (>= die hier gewaehlten
        # Werte), damit ein spaeterer Aufruf nicht an einer zu knapp
        # bemessenen Kompilierung scheitert.
        self.forecaster = forecaster or TimesFMForecaster(
            max_context=max(1024, context_len),
            max_horizon=max(256, horizon),
        )
        self.telemetry = ForecastTelemetry()
        self._warned = False

    @property
    def warmup_bars(self) -> int:
        # +1: aus context_len Preisen entstehen nur context_len - 1 Renditen.
        return self.params["context_len"] + 1

    def on_bar(self, symbol: str, store: FeatureStore) -> float:
        window = store.window(symbol, self.timeframe)
        if len(window) < self.warmup_bars:
            return math.nan

        state = self._state.setdefault(symbol, {"weight": 0.0, "since": None})
        since = state["since"]
        if since is not None and since < self.params["forecast_every"]:
            state["since"] = since + 1
            self.telemetry.cached += 1
            return state["weight"]

        closes = window.closes()[-(self.params["context_len"] + 1) :]
        context = np.diff(np.log(closes))

        self.telemetry.calls += 1
        try:
            result = self.forecaster.forecast(context, self.params["horizon"])
        except ForecasterUnavailable as exc:
            self.telemetry.failures += 1
            if not self._warned:
                self._warned = True
                warnings.warn(
                    f"TimesFM-Strategie faellt auf das letzte Gewicht zurueck: "
                    f"{exc}",
                    RuntimeWarning,
                    stacklevel=2,
                )
            state["since"] = 0
            return state["weight"]

        weight = self._weight_from_forecast(result)
        state["weight"] = weight
        state["since"] = 0
        return weight

    def _weight_from_forecast(self, result: ForecastResult) -> float:
        edge = float(np.sum(result.median))
        threshold = self.params["min_edge_bps"] * 1e-4
        if abs(edge) < threshold:
            return 0.0
        if edge < 0 and not self.params["allow_short"]:
            return 0.0

        magnitude = min(1.0, self._confidence(result)) * self.params["max_weight"]
        return math.copysign(magnitude, edge)

    def _confidence(self, result: ForecastResult) -> float:
        """Vorhersage relativ zur eigenen Unsicherheit -- kein binaeres Signal.

        Ohne Quantile (z.B. `NaiveForecaster`, oder eine unerwartete
        Antwortform) wird konservativ mit halber Staerke gehandelt statt mit
        voller: ein Forecaster ohne Unsicherheitsangabe verdient nicht
        dasselbe Vertrauen wie einer mit.
        """
        if result.q10 is None or result.q90 is None:
            return 0.5
        spread = float(np.sum(result.q90) - np.sum(result.q10))
        if spread <= 0:
            return 0.5
        edge = abs(float(np.sum(result.median)))
        return max(0.0, min(1.0, edge / spread))
