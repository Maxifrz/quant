"""Ein benanntes Vokabular, aus dem Signale zusammengesetzt werden.

Die Idee stammt aus NVIDIAs `quantitative-signal-discovery-agent`
(Apache-2.0, Stand 9a89d24), wo 66 Operatoren in einer JSON-Datei liegen und
das Modell Formeln daraus komponiert. Uebernommen ist der **Gedanke**, nicht
der Code: die Operatoren hier sind neu geschrieben, arbeiten auf dem
Panel-Format dieses Projekts und tragen die Konventionen, die dort sonst auch
gelten.

**Warum ein Vokabular neben der Sandbox.** Die Sandbox (ADR-029) beantwortet
"darf dieser Code laufen" -- eine Whitelist von AST-Knoten, geschlossen gegen
alles, woran beim Schreiben niemand gedacht hat. Sie beantwortet nicht, ob der
Code etwas Sinnvolles *meint*. Ein Vokabular tut das:

* Zwei Kandidaten, die beide `TS_Rank(TS_Momentum(close, 20), 60)` schreiben,
  sind erkennbar derselbe Versuch. Zwei handgeschriebene Schleifen mit
  demselben Inhalt sind es nicht -- und fuer die Deflated Sharpe Ratio zaehlen
  sie trotzdem doppelt (ADR-032).
* Die Aritaeten sind maschinell pruefbar, **bevor** irgendetwas ausgefuehrt
  wird. Ein Aufruf mit falscher Argumentzahl faellt in Millisekunden durch
  statt in einem Walk-Forward.

**Panel-Konvention.** Alle Operatoren nehmen und liefern ein `DataFrame` mit
Zeitstempeln als Index und Symbolen als Spalten. `TS_*` rechnet je Spalte
ueber die Zeit, `CS_*` je Zeile ueber den Querschnitt. Diese Trennung ist der
ganze Grund fuer die Praefixe: ein Operator, bei dem man raten muss, ueber
welche Achse er laeuft, ist eine Fehlerquelle mit Ansage.

**Kein Blick nach vorn.** Jeder `TS_*`-Operator benutzt ausschliesslich
`rolling`/`shift` mit positiven Verzoegerungen. `shift(-1)` kommt hier nicht
vor und darf auch nicht dazukommen -- `tests/test_operators.py` prueft das am
Quelltext, nicht am Vertrauen.
"""

from __future__ import annotations

import ast
import inspect
from typing import Callable

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Zeitreihen-Operatoren: je Spalte, ueber die Zeit
# ---------------------------------------------------------------------------


def TS_Delay(x: pd.DataFrame, d: int = 1) -> pd.DataFrame:
    """Wert von vor `d` Perioden."""
    return x.shift(d)


def TS_Delta(x: pd.DataFrame, d: int = 1) -> pd.DataFrame:
    """Absolute Aenderung gegenueber vor `d` Perioden."""
    return x - x.shift(d)


def TS_Return(x: pd.DataFrame, d: int = 1) -> pd.DataFrame:
    """Relative Aenderung. Division durch 0 wird `nan`, nicht `inf`."""
    vorher = x.shift(d)
    return (x / vorher.where(vorher != 0) - 1.0)


def TS_Mean(x: pd.DataFrame, d: int = 20) -> pd.DataFrame:
    return x.rolling(d, min_periods=d).mean()


def TS_Std(x: pd.DataFrame, d: int = 20) -> pd.DataFrame:
    return x.rolling(d, min_periods=d).std()


def TS_Min(x: pd.DataFrame, d: int = 20) -> pd.DataFrame:
    return x.rolling(d, min_periods=d).min()


def TS_Max(x: pd.DataFrame, d: int = 20) -> pd.DataFrame:
    return x.rolling(d, min_periods=d).max()


def TS_Sum(x: pd.DataFrame, d: int = 20) -> pd.DataFrame:
    return x.rolling(d, min_periods=d).sum()


def TS_Zscore(x: pd.DataFrame, d: int = 20) -> pd.DataFrame:
    """Abstand vom eigenen Mittel in eigenen Standardabweichungen."""
    mittel = x.rolling(d, min_periods=d).mean()
    streuung = x.rolling(d, min_periods=d).std()
    return (x - mittel) / streuung.where(streuung > 0)


def TS_Rank(x: pd.DataFrame, d: int = 20) -> pd.DataFrame:
    """Rang des aktuellen Werts im eigenen Fenster, skaliert auf [0, 1]."""
    return x.rolling(d, min_periods=d).rank(pct=True)


def TS_Momentum(x: pd.DataFrame, d: int = 20, skip: int = 0) -> pd.DataFrame:
    """Rendite ueber `d` Perioden, optional ohne die letzten `skip`.

    `skip` ist nicht Zierde: die klassische Momentum-Anomalie laesst den
    juengsten Monat aus, weil er kurzfristig umkehrt (Jegadeesh/Titman 1993).
    Wer ihn mitnimmt, mischt zwei gegenlaeufige Effekte.
    """
    if d <= skip:
        raise ValueError(f"d={d} muss groesser als skip={skip} sein.")
    spaet = x.shift(skip)
    frueh = x.shift(d)
    return spaet / frueh.where(frueh != 0) - 1.0


def TS_Vol(x: pd.DataFrame, d: int = 20) -> pd.DataFrame:
    """Standardabweichung der Log-Renditen ueber `d` Perioden."""
    return _log_returns(x).rolling(d, min_periods=d).std()


def TS_Drawdown(x: pd.DataFrame, d: int = 252) -> pd.DataFrame:
    """Abstand zum Hoechststand des Fensters, als negativer Anteil."""
    hoch = x.rolling(d, min_periods=d).max()
    return x / hoch.where(hoch > 0) - 1.0


def TS_Corr(x: pd.DataFrame, y: pd.DataFrame, d: int = 20) -> pd.DataFrame:
    """Rollende Korrelation zweier Panels, je Spalte."""
    return x.rolling(d, min_periods=d).corr(y)


def EMA(x: pd.DataFrame, d: int = 20) -> pd.DataFrame:
    return x.ewm(span=d, min_periods=d, adjust=False).mean()


# ---------------------------------------------------------------------------
# Querschnitts-Operatoren: je Zeile, ueber die Symbole
# ---------------------------------------------------------------------------


def CS_Rank(x: pd.DataFrame) -> pd.DataFrame:
    """Rang innerhalb des Zeitpunkts, skaliert auf [0, 1].

    Der wichtigste Operator dieses Moduls. Er macht aus Werten, die zwischen
    Maerkten nicht vergleichbar sind -- eine Krypto-Rendite und eine
    Anleihen-Rendite haben nichts gemein --, eine Rangfolge, die es ist.
    """
    return x.rank(axis=1, pct=True)


def CS_Demean(x: pd.DataFrame) -> pd.DataFrame:
    """Querschnittsmittel abziehen: was bleibt, ist relative Staerke.

    Damit faellt heraus, was alle Maerkte gemeinsam tun. Genau das ist der
    Grund, warum ein Querschnittssignal mit korrelierten Maerkten arbeiten
    kann, wo 26 Einzel-Backtests daran scheitern (ADR-058).
    """
    return x.sub(x.mean(axis=1), axis=0)


def CS_Zscore(x: pd.DataFrame) -> pd.DataFrame:
    streuung = x.std(axis=1)
    return x.sub(x.mean(axis=1), axis=0).div(streuung.where(streuung > 0), axis=0)


def CS_Scale(x: pd.DataFrame) -> pd.DataFrame:
    """Auf Bruttoexposure 1 normieren; eine Zeile aus lauter Nullen bleibt null."""
    summe = x.abs().sum(axis=1)
    return x.div(summe.where(summe > 0), axis=0).fillna(0.0)


# ---------------------------------------------------------------------------
# Elementweise
# ---------------------------------------------------------------------------


def Neg(x: pd.DataFrame) -> pd.DataFrame:
    return -x


def Abs(x: pd.DataFrame) -> pd.DataFrame:
    return x.abs()


def Sign(x: pd.DataFrame) -> pd.DataFrame:
    return np.sign(x)


def Log(x: pd.DataFrame) -> pd.DataFrame:
    """Natuerlicher Logarithmus; nicht-positive Werte werden `nan`, nicht `-inf`."""
    return np.log(x.where(x > 0))


def Clip(x: pd.DataFrame, unten: float = -1.0, oben: float = 1.0) -> pd.DataFrame:
    return x.clip(lower=unten, upper=oben)


def Winsorize(x: pd.DataFrame, quantil: float = 0.02) -> pd.DataFrame:
    """Extreme je Zeitpunkt auf Quantilsgrenzen stutzen.

    Nicht verwerfen, sondern stutzen: ein Ausreisser ist eine Beobachtung mit
    unsicherer Groesse, keine fehlende Beobachtung.
    """
    if not 0.0 <= quantil < 0.5:
        raise ValueError(f"quantil={quantil} muss in [0, 0.5) liegen.")
    if quantil == 0.0:
        return x
    unten = x.quantile(quantil, axis=1)
    oben = x.quantile(1.0 - quantil, axis=1)
    return x.clip(lower=unten, upper=oben, axis=0)


def Add(x: pd.DataFrame, y: pd.DataFrame) -> pd.DataFrame:
    return x + y


def Sub(x: pd.DataFrame, y: pd.DataFrame) -> pd.DataFrame:
    return x - y


def Mul(x: pd.DataFrame, y: pd.DataFrame) -> pd.DataFrame:
    return x * y


def Div(x: pd.DataFrame, y: pd.DataFrame) -> pd.DataFrame:
    """Division; durch 0 wird `nan`, nicht `inf`."""
    return x / y.where(y != 0)


def _log_returns(x: pd.DataFrame) -> pd.DataFrame:
    vorher = x.shift(1)
    return np.log(x.where(x > 0) / vorher.where(vorher > 0))


# ---------------------------------------------------------------------------
# Registry und Aritaetspruefung
# ---------------------------------------------------------------------------

OPERATORS: dict[str, Callable[..., pd.DataFrame]] = {
    name: obj
    for name, obj in list(globals().items())
    if callable(obj) and not name.startswith("_") and getattr(obj, "__module__", "") == __name__
}


def arities() -> dict[str, tuple[int, int]]:
    """`{Name: (mindestens noetig, hoechstens erlaubt)}` je Operator.

    Der billige Vorfilter: ein Aufruf mit falscher Argumentzahl faellt hier
    in Millisekunden durch, statt in einem Walk-Forward. Abgeleitet aus den
    Signaturen und nicht von Hand gepflegt -- zwei Listen, die zueinander
    passen muessen, laufen auseinander.
    """
    ergebnis: dict[str, tuple[int, int]] = {}
    for name, fn in OPERATORS.items():
        sig = inspect.signature(fn)
        positional = [
            p
            for p in sig.parameters.values()
            if p.kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        noetig = sum(1 for p in positional if p.default is inspect.Parameter.empty)
        ergebnis[name] = (noetig, len(positional))
    return ergebnis


def describe() -> str:
    """Das Vokabular als Text, fuer das Generator-Briefing."""
    zeilen = []
    for name, (noetig, hoechstens) in sorted(arities().items()):
        doc = (OPERATORS[name].__doc__ or "").strip().split("\n")[0]
        args = f"{noetig}" if noetig == hoechstens else f"{noetig}-{hoechstens}"
        zeilen.append(f"  {name}({args} Arg.)  {doc}")
    return "\n".join(zeilen)


def pruefe_aufrufe(code: str) -> list[str]:
    """Operatoraufrufe im Quelltext gegen ihre Signaturen pruefen.

    Der billige Vorfilter aus dem NVIDIA-Blueprint, hier ohne Ausfuehrung:
    geparst wird der AST, nicht `exec`. Ein Aufruf mit falscher Argumentzahl
    faellt damit in Millisekunden durch statt in einem Walk-Forward -- und vor
    allem, ohne dass fremder Code je laeuft.

    Gibt die Beanstandungen zurueck, leere Liste heisst sauber. Unbekannte
    Namen werden **nicht** beanstandet: hier geht es um Aritaeten, ob ein Name
    ueberhaupt erlaubt ist, entscheidet die Sandbox (ADR-029).
    """
    try:
        baum = ast.parse(code)
    except SyntaxError as exc:
        return [f"Zeile {exc.lineno}: Syntaxfehler ({exc.msg})"]

    bekannt = arities()
    befunde: list[str] = []
    for knoten in ast.walk(baum):
        if not isinstance(knoten, ast.Call):
            continue
        ziel = knoten.func
        name = (
            ziel.id if isinstance(ziel, ast.Name)
            else ziel.attr if isinstance(ziel, ast.Attribute)
            else None
        )
        if name is None or name not in bekannt:
            continue
        # Ein *args-Aufruf laesst sich statisch nicht zaehlen.
        if any(isinstance(a, ast.Starred) for a in knoten.args):
            continue

        noetig, hoechstens = bekannt[name]
        gegeben = len(knoten.args) + len(knoten.keywords)
        if gegeben < noetig or gegeben > hoechstens:
            spanne = f"{noetig}" if noetig == hoechstens else f"{noetig} bis {hoechstens}"
            befunde.append(
                f"Zeile {knoten.lineno}: {name} erwartet {spanne} Argumente, "
                f"bekommt {gegeben}"
            )
    return befunde
