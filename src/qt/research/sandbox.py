"""Die Sandbox -- die einzige Stelle, an der fremd erzeugter Code laeuft.

Ab Phase 5 schreibt ein Sprachmodell Strategiekandidaten, und irgendwer muss
sie ausfuehren. Das ist die gefaehrlichste Operation im ganzen System: der
Code kommt aus einem Generator, der nicht garantieren kann, was er erzeugt,
und er laeuft im selben Prozess wie die Datenpipeline und die API-Keys.

Deshalb ist die Pruefung eine **Whitelist von AST-Knotentypen** und keine
Blocklist. Eine Blocklist ("verbiete eval, exec, open, ...") ist ein Rennen,
das man verliert, weil man immer etwas vergisst -- und man merkt es genau
einmal. Eine Whitelist ist geschlossen: was nicht ausdruecklich erlaubt ist,
wird abgelehnt. Neue Sprachfeatures, exotische Knoten und Tricks, an die hier
niemand gedacht hat, fallen automatisch durch, ohne dass jemand die Datei
anfassen muss.

Die Verteidigung hat vier Ebenen, weil jede einzelne fuer sich Luecken hat:

    1. AST-Whitelist         -- welche Syntax ueberhaupt vorkommen darf
    2. Namensregeln          -- keine dunder, keine unbekannten freien Namen,
                                keine Zuweisung auf geteilte Objekte
    3. kuratierte Globals    -- `np` und `ta` sind Fassaden, keine Module;
                                `__builtins__` ist eine kurze, explizite Liste
    4. Probe mit Timeout     -- ein Lauf auf synthetischen Daten, bevor der
                                Kandidat je echte Marktdaten sieht

Ebene 1 sieht nur Syntax, nicht Semantik: `np.save(...)` ist syntaktisch ein
voellig normales Attribute+Call. Ebene 3 faengt genau das ab, indem die
gefaehrliche Methode auf dem uebergebenen Objekt schlicht nicht existiert --
egal unter welchem lokalen Namen der Kandidat es weiterreicht.
"""

from __future__ import annotations

import ast
import builtins
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import numpy as np

from qt.core.clock import BacktestClock
from qt.core.events import merge_bar_streams
from qt.core.types import Bar, bars_per_year, timeframe_seconds
from qt.features import ta as _ta_module
from qt.features.registry import FeatureStore
from qt.strategy.base import Strategy, clip_weight

__all__ = [
    "ALLOWED_NODE_TYPES",
    "FORBIDDEN_ATTR_NAMES",
    "FORBIDDEN_CALL_NAMES",
    "INJECTED_NAMES",
    "LITERAL_ABS_THRESHOLD",
    "LiteralFlag",
    "MAX_WARMUP_BARS",
    "NP_FACADE",
    "PRICE_TOKENS",
    "ProbeReport",
    "SAFE_BUILTINS",
    "SandboxRejected",
    "SandboxReport",
    "TA_FACADE",
    "check",
    "load_strategy_class",
    "probe",
    "scan_literals",
]


# ---------------------------------------------------------------------------
# Ebene 1: die Whitelist der Knotentypen
# ---------------------------------------------------------------------------

# Genau die Knoten, die man braucht, um Code im Stil von
# `qt.strategy.library.trend` und `.meanrev` zu schreiben. Das Set wurde gegen
# den tatsaechlichen AST beider Dateien geprueft, nicht geraten.
ALLOWED_NODE_TYPES: frozenset[type] = frozenset(
    {
        ast.Module,
        ast.ClassDef,
        ast.FunctionDef,
        ast.arguments,
        ast.arg,
        ast.Return,
        ast.Assign,
        ast.AugAssign,
        ast.AnnAssign,
        ast.If,
        ast.IfExp,
        ast.Compare,
        ast.BoolOp,
        ast.BinOp,
        ast.UnaryOp,
        ast.Call,
        ast.Attribute,
        ast.Name,
        ast.Load,
        ast.Store,
        ast.Constant,
        ast.List,
        ast.Tuple,
        ast.Dict,
        ast.Subscript,
        ast.Slice,
        ast.Pass,
        ast.Expr,
        ast.keyword,
        # Operatoren
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.USub,
        ast.UAdd,
        ast.Not,
        ast.And,
        ast.Or,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
    }
)

# Die vorgebundenen Namen. Sie stehen hier oben, weil sowohl die
# Ablehnungsmeldung fuer `import` als auch die Bindung selbst (`_INJECTED`)
# davon abhaengen -- eine Quelle, nicht zwei, die auseinanderlaufen.
INJECTED_NAMES: frozenset[str] = frozenset(
    {"np", "math", "ta", "Strategy", "clip_weight", "bars_per_year"}
)

_INJECTED_HINT = (
    "np, math, ta, Strategy, clip_weight und bars_per_year sind bereits gebunden"
)


# Warum ein Knoten fehlt, in einem Satz. Steht in der Ablehnungsmeldung, damit
# die naechste Runde des Generators weiss, was sie anders machen soll -- eine
# Ablehnung ohne Begruendung erzeugt nur denselben Kandidaten noch einmal.
_WHY_FORBIDDEN: dict[str, str] = {
    # Ausnahmslos kein Import. Die Namen aus `INJECTED_NAMES` sind bereits
    # gebunden; der Kandidat *benutzt* sie, er importiert sie nie selbst.
    # Damit gibt es keinen Weg zu einem Modul, das wir nicht selbst
    # ausgesucht haben.
    "Import": _INJECTED_HINT,
    "ImportFrom": _INJECTED_HINT,
    # Keine Schleifen. Weder trend.py noch meanrev.py enthalten eine einzige,
    # weil alles ueber ta.*/numpy vektorisiert laeuft. Das Verbot loescht die
    # Klasse "Endlosschleife/DoS" strukturell, statt sie per Timeout nur zu
    # begrenzen -- ein Timeout meldet den Schaden, ein Verbot verhindert ihn.
    "While": "Schleifen sind unnoetig, alles rechnet vektorisiert ueber ta.*/np",
    "For": "Schleifen sind unnoetig, alles rechnet vektorisiert ueber ta.*/np",
    "ListComp": "Comprehensions sind unnoetig, vektorisiert ueber ta.*/np rechnen",
    "SetComp": "Comprehensions sind unnoetig, vektorisiert ueber ta.*/np rechnen",
    "DictComp": "Comprehensions sind unnoetig, vektorisiert ueber ta.*/np rechnen",
    "GeneratorExp": "Bedingungen stattdessen mit and/or verketten",
    "comprehension": "Comprehensions sind unnoetig, vektorisiert ueber ta.*/np rechnen",
    # Lambda verschiebt Code in einen Ausdruck, den man leicht uebersieht;
    # Global/Nonlocal reichen Zustand ueber Grenzen hinweg weiter.
    "Lambda": "benannte Methoden schreiben",
    "Global": "Zustand gehoert nach self.*",
    "Nonlocal": "Zustand gehoert nach self.*",
    # try/except schluckt genau die Fehler, wegen derer ein Kandidat verworfen
    # gehoert. Ein Kandidat, der sich selbst repariert, versteckt seinen Bug.
    "Try": "Fehler sollen sichtbar bleiben, nicht geschluckt werden",
    "TryStar": "Fehler sollen sichtbar bleiben, nicht geschluckt werden",
    "ExceptHandler": "Fehler sollen sichtbar bleiben, nicht geschluckt werden",
    "With": "es gibt nichts zu oeffnen und nichts zu schliessen",
    "Raise": "eine Strategie meldet fehlende Meinung mit nan, nicht mit raise",
    "Yield": "on_bar gibt einen Wert zurueck, keinen Generator",
    "YieldFrom": "on_bar gibt einen Wert zurueck, keinen Generator",
    "Delete": "nichts, was hier entsteht, muss geloescht werden",
    "Starred": "Argumente ausschreiben",
    "Assert": "Pruefungen macht die Sandbox, nicht der Kandidat",
    "JoinedStr": "f-Strings kommen ueber Formatfelder an dunder-Attribute",
    "FormattedValue": "f-Strings kommen ueber Formatfelder an dunder-Attribute",
    "AsyncFunctionDef": "die Engine ist synchron",
    "Await": "die Engine ist synchron",
    "AsyncFor": "die Engine ist synchron",
    "AsyncWith": "die Engine ist synchron",
    "Match": "mit if/elif ausschreiben",
    "NamedExpr": "normale Zuweisung schreiben",
}

# Aufrufe auf einen dieser Namen sind verboten. getattr/setattr sind zwingend
# dabei: ohne sie liesse sich die dunder-Sperre unten per
# `getattr(x, '__' + 'class__')` umgehen, weil in einem String kein AST steckt,
# den man pruefen koennte.
FORBIDDEN_CALL_NAMES: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "dir",
        "__import__",
        "open",
        "input",
        "breakpoint",
        "help",
        "type",
        "object",
        "super",
        "memoryview",
        "id",
    }
)

# Zweite Reihe, ausdruecklich eine Blocklist und deshalb ausdruecklich **nicht**
# die tragende Ebene: Methodennamen, die auf einem sonst harmlosen Objekt
# Dateisystem, Speicher oder Klassenhierarchie beruehren. Ein ndarray hat
# `tofile` und `dump`; ein str hat `format`, das ueber Formatfelder
# (`"{0.__class__}".format(x)`) an dunder-Attribute kommt, ohne dass je ein
# dunder im AST auftaucht. Getragen wird die Sicherheit von den Fassaden
# unten -- das hier faengt nur ab, was auf Objekten sitzt, die der Kandidat
# legitim in der Hand hat.
FORBIDDEN_ATTR_NAMES: frozenset[str] = frozenset(
    {
        "tofile",
        "dump",
        "dumps",
        "save",
        "savez",
        "savetxt",
        "load",
        "loads",
        "fromfile",
        "genfromtxt",
        "memmap",
        "ctypes",
        "mro",
        "format",
        "format_map",
        "open",
        "read",
        "write",
        "system",
        "popen",
    }
)


# ---------------------------------------------------------------------------
# Ebene 3: kuratierte Globals
# ---------------------------------------------------------------------------

# `np` ist bewusst **nicht** das echte numpy-Modul, sondern eine Fassade mit
# ausschliesslich reinen Rechenfunktionen. Der Punkt ist nicht, dass die
# AST-Pruefung `np.save` nicht erkennen wuerde -- der Punkt ist, dass sie es
# gar nicht erkennen *muss*: die Methode existiert auf diesem Objekt nicht,
# egal unter welchem lokalen Namen der Kandidat es weiterreicht.
_NP_EXPORTS: tuple[str, ...] = (
    "array",
    "asarray",
    "mean",
    "std",
    "sum",
    "min",
    "max",
    "abs",
    "sqrt",
    "exp",
    "log",
    "where",
    "clip",
    "diff",
    "cumsum",
    "cumprod",
    "isnan",
    "isfinite",
    "maximum",
    "minimum",
    "sign",
    "nan",
    "inf",
)

NP_FACADE = SimpleNamespace(**{name: getattr(np, name) for name in _NP_EXPORTS})

# `qt.features.ta` enthaelt ausschliesslich reine Funktionen ohne I/O -- aber
# als **Modulobjekt** reicht es seine eigenen Importe mit weiter: `ta.np` ist
# das echte numpy, und damit waere die Fassade oben in einem Schritt umgangen
# (`ta.np.load(...)`). Deshalb auch hier eine Fassade. Sie wird aus dem Modul
# abgeleitet statt aufgezaehlt, damit eine neue ta-Funktion automatisch
# verfuegbar ist, ein neuer Import in ta aber nicht.
TA_FACADE = SimpleNamespace(
    **{
        name: obj
        for name, obj in vars(_ta_module).items()
        if not name.startswith("_")
        and callable(obj)
        and getattr(obj, "__module__", None) == _ta_module.__name__
    }
)

# `math` darf unveraendert durch: es ist ein C-Modul, dessen saemtliche
# oeffentlichen Attribute reine Funktionen und Konstanten sind (e, inf, nan,
# pi, tau). Es reicht keine Importe weiter, weil es keine hat.
SAFE_BUILTINS: dict[str, Any] = {
    "len": len,
    "abs": abs,
    "min": min,
    "max": max,
    "sum": sum,
    "round": round,
    "float": float,
    "int": int,
    "bool": bool,
    "sorted": sorted,
    "enumerate": enumerate,
    "range": range,
    "True": True,
    "False": False,
    "None": None,
    # CPython uebersetzt jedes `class`-Statement in einen Aufruf von
    # `__build_class__` aus den Builtins. Ohne diesen Eintrag kann der
    # Kandidat keine Klasse definieren -- und um eine Strategieklasse geht es
    # hier gerade. Neue Faehigkeiten entstehen dadurch nicht: gebaut werden
    # kann nur aus Objekten, die der Kandidat ohnehin schon hat.
    "__build_class__": builtins.__build_class__,
}

# Vorgebundene Namen -- deshalb braucht (und darf) der generierte Code keinen
# Import.
#
# `bars_per_year` ist nachtraeglich dazugekommen: `ta.realised_vol` verlangt
# es als drittes Argument, und ohne die Funktion war der Indikator aus der
# Sandbox heraus schlicht nicht korrekt aufrufbar. Ein Kandidat haette die
# Zahl nur als Konstante hinschreiben koennen -- und das waere genau die an
# einen Timeframe gebundene Magic Number, die der Kritiker ablehnen soll.
# Die Funktion ist rein: ein Timeframe-String rein, eine Zahl raus.
_INJECTED: dict[str, Any] = {
    "np": NP_FACADE,
    "math": math,
    "ta": TA_FACADE,
    "Strategy": Strategy,
    "clip_weight": clip_weight,
    "bars_per_year": bars_per_year,
}

# Die Liste oben und die Bindung hier muessen deckungsgleich bleiben, sonst
# nennt die Ablehnungsmeldung einen Namen, den es nicht gibt (oder verschweigt
# einen, den es gibt).
assert frozenset(_INJECTED) == INJECTED_NAMES

SANDBOX_FILENAME = "<llm-kandidat>"
_SANDBOX_MODULE_NAME = "qt_sandbox_candidate"

# PEP 563: Annotationen werden nie ausgewertet, sondern als String abgelegt.
# Sonst muesste `def on_bar(self, symbol: str, store: FeatureStore)` an einem
# fehlenden `str` scheitern -- die Whitelist wuerde dann nicht wegen einer
# Gefahr ablehnen, sondern wegen einer Typannotation. Mit dem Flag ist der
# Ausdruck in der Annotation toter Text.
_FUTURE_ANNOTATIONS = 0x1000000  # __future__.annotations.compiler_flag

# Ein Warmup jenseits davon ist kein Parameter mehr, sondern ein Kandidat, der
# nie ein Signal geben wuerde.
MAX_WARMUP_BARS = 5_000

# Betrag, ab dem eine Zahl im Vergleich mit einem Preis auffaellig wird.
LITERAL_ABS_THRESHOLD = 1_000.0

# Variablennamen, hinter denen ein Preisniveau steckt.
PRICE_TOKENS: frozenset[str] = frozenset({"close", "price", "high", "low", "open"})

PROBE_SEED = 20200101
_PROBE_START = datetime(2020, 1, 1, tzinfo=timezone.utc)

# Mehr als das sagt keinem Menschen und keinem Modell mehr etwas.
_MAX_REASONS = 25


class SandboxRejected(Exception):
    """Ein Kandidat hat die Pruefung nicht bestanden.

    Traegt die vollstaendige Begruendungsliste mit, jede Zeile mit
    Zeilennummer: der naechste Generierungsversuch bekommt sie als Briefing,
    und "abgelehnt" ohne Grund ist als Briefing wertlos.
    """

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = list(reasons)
        body = "\n  ".join(self.reasons) if self.reasons else "(kein Grund angegeben)"
        super().__init__(f"Kandidat abgelehnt:\n  {body}")


@dataclass(frozen=True, slots=True)
class SandboxReport:
    """Ergebnis der statischen Pruefung. `reasons` ist leer genau dann, wenn ok."""

    ok: bool
    reasons: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.ok:
            return "Whitelist bestanden."
        return f"{len(self.reasons)} Verstoesse: " + "; ".join(self.reasons)


@dataclass(frozen=True, slots=True)
class ProbeReport:
    """Ergebnis des Probelaufs. `reason` ist leer genau dann, wenn ok."""

    ok: bool
    reason: str = ""
    warmup_bars: int = 0
    n_calls: int = 0


@dataclass(frozen=True, slots=True)
class LiteralFlag:
    """Eine auffaellige Zahl im Vergleich mit einer preisverdaechtigen Groesse.

    Ausdruecklich ein Hinweis, keine Ablehnung -- siehe `scan_literals`.
    """

    line: int
    value: float
    context: str


# ---------------------------------------------------------------------------
# check()
# ---------------------------------------------------------------------------


def check(code: str) -> SandboxReport:
    """Statische Pruefung des Kandidaten. Fuehrt nichts aus.

    Sammelt **alle** Verstoesse statt beim ersten abzubrechen: der Bericht
    geht zurueck an den Generator, und der lernt aus einer vollstaendigen
    Liste in einer Runde, was er sonst in fuenf Runden einzeln erfaehrt.
    """
    try:
        tree = ast.parse(code, filename=SANDBOX_FILENAME)
    except SyntaxError as exc:
        return SandboxReport(ok=False, reasons=[f"Zeile {exc.lineno or 0}: Syntaxfehler: {exc.msg}"])

    reasons: list[str] = []
    known = _bound_names(tree) | INJECTED_NAMES | frozenset(SAFE_BUILTINS)
    _check_node(tree, 0, known, reasons, _annotation_nodes(tree))

    # Reihenfolge erhalten, Dubletten raus: derselbe Verstoss in zwanzig
    # Zeilen ist eine Erkenntnis, nicht zwanzig.
    unique: list[str] = list(dict.fromkeys(reasons))
    if len(unique) > _MAX_REASONS:
        rest = len(unique) - _MAX_REASONS
        unique = unique[:_MAX_REASONS] + [f"... und {rest} weitere Verstoesse"]
    return SandboxReport(ok=not unique, reasons=unique)


def _bound_names(tree: ast.AST) -> frozenset[str]:
    """Alle Namen, die der Kandidat irgendwo selbst bindet.

    Bewusst **ohne** Scope-Analyse: eine in Methode A gebundene Variable gilt
    auch in Methode B als bekannt. Ein zu grosszuegiges Ergebnis kostet hier
    nichts -- der schlimmste Fall ist ein NameError im exec, und der ist
    harmlos. Ein zu strenges Ergebnis wuerde dagegen gueltige Kandidaten
    ablehnen, und das faellt niemandem auf.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
    return frozenset(names)


def _annotation_nodes(tree: ast.AST) -> set[int]:
    """Alle Knoten innerhalb von Typannotationen.

    Annotationen werden wegen `_FUTURE_ANNOTATIONS` nie ausgewertet -- sie
    sind toter Text. Ein Name darin auf Existenz zu pruefen waere also nicht
    nur unnoetig, sondern falsch: `store: FeatureStore` wuerde abgelehnt,
    obwohl `FeatureStore` zur Laufzeit niemand nachschlaegt.
    """
    inside: set[int] = set()
    for node in ast.walk(tree):
        annotations: list[ast.AST] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                annotations.append(node.returns)
        elif isinstance(node, ast.arg) and node.annotation is not None:
            annotations.append(node.annotation)
        elif isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)
        for annotation in annotations:
            inside.update(id(sub) for sub in ast.walk(annotation))
    return inside


def _check_node(
    node: ast.AST,
    parent_line: int,
    known: frozenset[str],
    reasons: list[str],
    skip_names: set[int],
) -> None:
    """Rekursive Pruefung eines Knotens.

    `parent_line` wird durchgereicht, weil Operator-Knoten (`ast.Add` &c.)
    keine eigene Zeilennummer haben -- eine Ablehnung ohne Zeile zwingt zum
    Suchen. In `skip_names` stehen Knoten, fuer die die Namensregeln nicht
    gelten: Annotationen (toter Text) und Aufrufziele, die schon eine
    praezisere Meldung bekommen haben, damit `eval(...)` nicht zweimal
    auftaucht.
    """
    line = getattr(node, "lineno", parent_line)
    node_type = type(node)

    if node_type not in ALLOWED_NODE_TYPES:
        name = node_type.__name__
        why = _WHY_FORBIDDEN.get(name, "nicht in der Whitelist")
        reasons.append(f"Zeile {line}: {name} ist nicht erlaubt -- {why}")
        # Nicht weiter absteigen: die Kinder eines verbotenen Knotens erzeugen
        # nur Folgemeldungen, die nichts Neues sagen.
        return

    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
        _check_identifier(node.name, "Definition", line, reasons)
        if node.decorator_list:
            # `property` und `register` stehen dem Kandidaten nicht zur
            # Verfuegung, ein Dekorator wuerde also im exec an einem
            # NameError scheitern. Eine klare Ablehnung ist besser als ein
            # verwirrender Laufzeitfehler: warmup_bars gehoert im
            # Sandbox-Stil als Klassenattribut hin, nicht als @property.
            reasons.append(
                f"Zeile {line}: Dekoratoren sind nicht erlaubt -- "
                "warmup_bars als Klassenattribut setzen, nicht als @property"
            )

    elif isinstance(node, ast.arg):
        _check_identifier(node.arg, "Parameter", line, reasons)

    elif isinstance(node, ast.keyword) and node.arg is not None:
        _check_identifier(node.arg, "Schluesselwort", line, reasons)

    elif isinstance(node, ast.Attribute):
        # Die dunder-Sperre. Sie blockiert an der Wurzel
        # `().__class__.__base__.__subclasses__()` und die ganze
        # Restricted-Exec-Familie: ohne dunder-Attribut kommt man von einem
        # harmlosen Objekt nicht an seine Klasse und damit nicht an den
        # Rest des Interpreters.
        if node.attr.startswith("__"):
            reasons.append(
                f"Zeile {line}: Zugriff auf {node.attr!r} ist nicht erlaubt -- "
                "dunder-Attribute sind der Standardweg aus jeder Sandbox heraus"
            )
        elif node.attr in FORBIDDEN_ATTR_NAMES:
            reasons.append(
                f"Zeile {line}: Methode {node.attr!r} ist nicht erlaubt -- "
                "beruehrt Dateisystem, Speicher oder Klassenhierarchie"
            )
        if isinstance(node.ctx, ast.Store):
            # Die injizierten Objekte sind prozessweit geteilt. `Strategy.on_bar = ...`
            # oder `np.mean = ...` wuerde nicht diesen Kandidaten veraendern,
            # sondern die echte Basisklasse fuer alles Folgende -- inklusive
            # der Backtests, die danach in diesem Prozess laufen.
            if not (isinstance(node.value, ast.Name) and node.value.id == "self"):
                reasons.append(
                    f"Zeile {line}: Zuweisung auf ein fremdes Attribut ist nicht "
                    "erlaubt -- nur self.* darf beschrieben werden"
                )

    elif isinstance(node, ast.Name):
        if id(node) in skip_names:
            pass  # Annotation oder schon als Aufruf gemeldet
        elif node.id.startswith("__"):
            reasons.append(
                f"Zeile {line}: Name {node.id!r} ist nicht erlaubt -- "
                "dunder-Namen sind der Standardweg aus jeder Sandbox heraus"
            )
        elif node.id in FORBIDDEN_CALL_NAMES:
            # Nicht nur der Aufruf, auch die blosse Referenz: sonst genuegt
            # `f = eval` gefolgt von `f(...)`, und die Regel greift ins Leere.
            reasons.append(
                f"Zeile {line}: Name {node.id!r} ist gesperrt -- "
                "auch als Variablenname (Umbenennen wuerde die Sperre umgehen)"
            )
        elif isinstance(node.ctx, ast.Load) and node.id not in known:
            # Faengt die reine Namensreferenz auf `socket`, `urllib`, `os`:
            # ohne diese Regel waere das ein NameError erst im exec -- also
            # erst, nachdem der Rest des Moduls schon gelaufen ist.
            reasons.append(
                f"Zeile {line}: unbekannter Name {node.id!r} -- verfuegbar sind "
                f"{', '.join(sorted(INJECTED_NAMES))} und die sicheren Builtins"
            )

    elif isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALL_NAMES:
            reasons.append(f"Zeile {line}: Aufruf von {func.id}() ist nicht erlaubt")
            skip_names.add(id(func))

    for child in ast.iter_child_nodes(node):
        _check_node(child, line, known, reasons, skip_names)


def _check_identifier(name: str, kind: str, line: int, reasons: list[str]) -> None:
    """Definierte Namen duerfen keine dunder sein.

    Ein `def __getattr__` oder `class __Meta` ist zwar fuer sich kein
    Ausbruch, aber es hebelt die Lesbarkeit der dunder-Sperre aus: wer sie
    liest, soll davon ausgehen duerfen, dass im Kandidaten ueberhaupt kein
    dunder vorkommt.
    """
    if name.startswith("__"):
        reasons.append(f"Zeile {line}: {kind} {name!r} ist nicht erlaubt -- kein dunder")
    elif name in FORBIDDEN_CALL_NAMES:
        reasons.append(f"Zeile {line}: {kind} {name!r} ist ein gesperrter Name")


# ---------------------------------------------------------------------------
# load_strategy_class()
# ---------------------------------------------------------------------------


def _exec_globals() -> dict[str, Any]:
    """Frische Globals fuer genau einen Kandidaten.

    Frisch, weil zwei Kandidaten sich sonst ueber ihr Modul-Namespace sehen
    koennten -- und weil ein Lauf, dessen Ergebnis vom Vorgaenger abhaengt,
    nicht reproduzierbar ist.
    """
    return {
        "__builtins__": dict(SAFE_BUILTINS),
        # `__build_class__` liest `__name__` fuer `cls.__module__`; ohne den
        # Eintrag scheitert jede Klassendefinition an einem NameError.
        "__name__": _SANDBOX_MODULE_NAME,
        **_INJECTED,
    }


def load_strategy_class(code: str, class_name: str) -> type[Strategy]:
    """Kandidatencode pruefen und die genannte Strategieklasse zurueckgeben.

    **Die strukturell wichtigste Eigenschaft dieses Moduls:** es gibt in der
    oeffentlichen API keine Funktion, die Code ausfuehrt, ohne vorher selbst
    `check()` aufgerufen zu haben. Diese Funktion hier ist die einzige mit
    einem `exec`, und der Aufruf von `check()` steht unmittelbar davor im
    selben Funktionskoerper, mit hartem Abbruch bei Fehlschlag. Es gibt
    keinen Parameter, der die Pruefung abschaltet, und keine tiefere
    Hilfsfunktion, die man versehentlich direkt aufrufen koennte.

    Das ist wichtiger als jede einzelne Whitelist-Regel: eine Regel kann
    lueckenhaft sein, aber "jemand hat vergessen zu pruefen" ist hier
    strukturell unmoeglich statt nur unwahrscheinlich. Wer diese Reihenfolge
    aufbricht, muss die Funktion umschreiben -- ein Review sieht das, ein
    vergessener Aufruf an einer dritten Stelle nicht.
    """
    report = check(code)
    if not report.ok:
        raise SandboxRejected(report.reasons)

    tree = ast.parse(code, filename=SANDBOX_FILENAME)
    compiled = compile(
        tree,
        SANDBOX_FILENAME,
        "exec",
        flags=_FUTURE_ANNOTATIONS,
        dont_inherit=True,
    )
    namespace = _exec_globals()
    try:
        exec(compiled, namespace, namespace)
    except Exception as exc:
        # Modulebene des Kandidaten ist gelaufen und hat geworfen. Das ist
        # kein Absturz des Systems, sondern ein schlechter Kandidat.
        raise SandboxRejected(
            [f"Zeile {_sandbox_line(exc)}: Ausfuehrung fehlgeschlagen: "
             f"{type(exc).__name__}: {exc}"]
        ) from exc

    obj = namespace.get(class_name)
    if obj is None:
        raise SandboxRejected([f"Zeile 0: Klasse {class_name!r} ist nicht definiert"])
    if not isinstance(obj, type) or not issubclass(obj, Strategy):
        raise SandboxRejected(
            [f"Zeile 0: {class_name!r} ist keine Unterklasse von Strategy"]
        )
    return obj


def _sandbox_line(exc: BaseException) -> int:
    """Zeile im Kandidaten, in der es geknallt hat -- 0, wenn unbekannt."""
    tb = exc.__traceback__
    line = 0
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == SANDBOX_FILENAME:
            line = tb.tb_lineno
        tb = tb.tb_next
    return line


# ---------------------------------------------------------------------------
# probe()
# ---------------------------------------------------------------------------


def probe(
    cls: type[Strategy],
    symbols: list[str],
    timeframe: str,
    n_bars: int = 400,
    timeout_s: float = 5.0,
) -> ProbeReport:
    """Kandidat einmal laufen lassen, bevor er echte Daten sieht.

    Ausschliesslich auf **synthetischen Zufallsdaten**: ein Probelauf darf
    nichts ueber den echten Datensatz verraten. Ein Kandidat, der aus der
    Probe Preise ablesen koennte, koennte sie in der naechsten Runde
    hartkodieren -- das waere Overfitting, das wie Genie aussieht.

    Store und Uhr werden genau so verdrahtet wie in `qt.backtest.engine`,
    damit die Probe dasselbe Interface sieht wie der spaetere Backtest. Ein
    eigener, einfacherer Aufbau wuerde genau die Verdrahtung ungeprueft
    lassen, um die es geht.

    Der Timeout laeuft ueber einen **Daemon-Thread**, nicht ueber
    `signal.setitimer`/SIGALRM. Zwei Gruende:

      1. SIGALRM funktioniert nur im Hauptthread. Unter pytest ist das heute
         gegeben, unter pytest-xdist, in einem Worker oder in einem spaeteren
         Server-Prozess nicht mehr -- und ein Schutz, der unbemerkt zum
         No-op wird, ist schlechter als keiner.
      2. Der Python-Signalhandler laeuft erst an der naechsten
         Bytecode-Grenze. Ein einzelner langer numpy-Aufruf haelt genau dort
         nicht an, also wuerde der Timer erst *nach* der Operation feuern --
         also genau in dem Fall nicht greifen, fuer den er da ist.

    Preis dieser Wahl: der Thread laesst sich nicht toeten, die Rechnung
    laeuft im Hintergrund zu Ende. Als Daemon blockiert er das
    Interpreter-Ende nicht, und weil Schleifen verboten sind, ist das, was
    weiterlaeuft, immer eine endliche Rechnung.
    """
    state: dict[str, int] = {"warmup": 0, "calls": 0}
    box: dict[str, ProbeReport] = {}

    def _run() -> None:
        try:
            box["report"] = _probe_body(cls, symbols, timeframe, n_bars, state)
        except Exception as exc:  # Kandidatenfehler, kein Systemfehler
            box["report"] = ProbeReport(
                ok=False,
                reason=f"Zeile {_sandbox_line(exc)}: {type(exc).__name__}: {exc}",
                warmup_bars=state["warmup"],
                n_calls=state["calls"],
            )

    worker = threading.Thread(target=_run, name="qt-sandbox-probe", daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        return ProbeReport(
            ok=False,
            reason=f"Timeout: on_bar war nach {timeout_s}s nicht fertig",
            warmup_bars=state["warmup"],
            n_calls=state["calls"],
        )
    return box["report"]


def _probe_body(
    cls: type[Strategy],
    symbols: list[str],
    timeframe: str,
    n_bars: int,
    state: dict[str, int],
) -> ProbeReport:
    """Der eigentliche Probelauf. Laeuft im Worker-Thread von `probe`."""
    if not symbols:
        return ProbeReport(ok=False, reason="Keine Symbole uebergeben")

    strategy = cls(list(symbols), timeframe)

    warmup = strategy.warmup_bars
    if isinstance(warmup, bool) or not isinstance(warmup, int):
        return ProbeReport(
            ok=False,
            reason=f"warmup_bars ist {type(warmup).__name__}, erwartet int "
            "(als Klassenattribut setzen, nicht als Methode)",
        )
    state["warmup"] = warmup
    if warmup <= 0:
        return ProbeReport(ok=False, reason=f"warmup_bars={warmup}, muss > 0 sein", warmup_bars=warmup)
    if warmup > MAX_WARMUP_BARS:
        return ProbeReport(
            ok=False,
            reason=f"warmup_bars={warmup} ueberschreitet {MAX_WARMUP_BARS}",
            warmup_bars=warmup,
        )
    if warmup >= n_bars:
        return ProbeReport(
            ok=False,
            reason=f"warmup_bars={warmup} bei nur {n_bars} Probe-Bars -- "
            "on_bar kaeme nie zum Zug",
            warmup_bars=warmup,
        )

    bars = {
        symbol: _synthetic_bars(symbol, timeframe, n_bars, PROBE_SEED + i)
        for i, symbol in enumerate(symbols)
    }
    events = merge_bar_streams(list(bars.values()))
    clock = BacktestClock(events[0].ts)
    store = FeatureStore(clock, maxlen=max(1000, warmup * 3))

    seen: dict[str, int] = {symbol: 0 for symbol in bars}
    for event in events:
        bar = event.bar
        clock.advance(event.ts)
        store.on_bar(bar)
        seen[bar.symbol] += 1
        if seen[bar.symbol] < warmup:
            continue
        weight = strategy.on_bar(bar.symbol, store)
        state["calls"] += 1
        bad = _bad_weight(weight)
        if bad is not None:
            return ProbeReport(
                ok=False, reason=bad, warmup_bars=warmup, n_calls=state["calls"]
            )

    if state["calls"] == 0:
        return ProbeReport(
            ok=False, reason="on_bar wurde nie aufgerufen", warmup_bars=warmup
        )
    return ProbeReport(ok=True, warmup_bars=warmup, n_calls=state["calls"])


def _bad_weight(weight: Any) -> str | None:
    """Begruendung, warum dieses Zielgewicht unbrauchbar ist -- oder None."""
    if isinstance(weight, bool) or not isinstance(weight, (int, float, np.floating)):
        return f"on_bar lieferte {type(weight).__name__}, erwartet float"
    value = float(weight)
    if value != value:  # nan heisst "keine Meinung" und ist erlaubt
        return None
    if not math.isfinite(value):
        # clip_weight wuerde inf still zu 1.0 machen -- ein Vollausschlag aus
        # einem Rechenfehler saehe dann aus wie eine Entscheidung.
        return "on_bar lieferte inf statt eines endlichen Gewichts"
    return None


def _synthetic_bars(symbol: str, timeframe: str, n_bars: int, seed: int) -> list[Bar]:
    """Zufallspfad mit festem Seed -- reproduzierbar, aber ohne Marktbezug."""
    rng = np.random.default_rng(seed)
    prices = 100.0 * np.cumprod(1.0 + rng.normal(0.0002, 0.01, n_bars))
    step = timedelta(seconds=timeframe_seconds(timeframe))
    bars: list[Bar] = []
    for i, raw in enumerate(prices):
        price = float(raw)
        bars.append(
            Bar(
                symbol=symbol,
                timeframe=timeframe,
                ts=_PROBE_START + i * step,
                open=price,
                high=price * 1.005,
                low=price * 0.995,
                close=price,
                volume=1.0,
            )
        )
    return bars


# ---------------------------------------------------------------------------
# scan_literals()
# ---------------------------------------------------------------------------


def scan_literals(code: str) -> list[LiteralFlag]:
    """Auffaellige Zahlen im Vergleich mit Preisgroessen finden.

    Ausdruecklich **kein Blocker**, sondern ein Hinweis fuers Kritik-Briefing.
    Ein Lookback von 1440 Bars ist voellig legitim, ein hartkodiertes
    `close > 42000` ist eine an den Datensatz angepasste Konstante, die live
    nie wieder ausloest. Syntaktisch sind beide identisch -- den Unterschied
    kann nur beurteilen, wer die Bedeutung kennt. Deshalb meldet diese
    Funktion, statt zu entscheiden.
    """
    try:
        tree = ast.parse(code, filename=SANDBOX_FILENAME)
    except SyntaxError:
        return []

    flags: list[LiteralFlag] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        if not any(_is_price_like(op) for op in operands):
            continue
        context = _short(ast.unparse(node))
        for operand in operands:
            value = _numeric_value(operand)
            if value is not None and abs(value) > LITERAL_ABS_THRESHOLD:
                flags.append(
                    LiteralFlag(
                        line=getattr(operand, "lineno", node.lineno),
                        value=value,
                        context=context,
                    )
                )
    return flags


def _is_price_like(node: ast.AST) -> bool:
    """Steckt hinter diesem Ausdruck ein Preisniveau?

    Zerlegt den Bezeichner an `_` und schneidet ein Plural-s ab, statt nur
    nach Teilstrings zu suchen: `allow_short` enthaelt "low", meint aber
    keinen Tiefstkurs.
    """
    while True:
        if isinstance(node, ast.Subscript):
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        else:
            break

    if isinstance(node, ast.Attribute):
        identifier = node.attr
    elif isinstance(node, ast.Name):
        identifier = node.id
    else:
        return False

    for part in identifier.lower().split("_"):
        if part in PRICE_TOKENS or part.rstrip("s") in PRICE_TOKENS:
            return True
    return False


def _numeric_value(node: ast.AST) -> float | None:
    """Zahlenwert eines Literals, Vorzeichen eingerechnet -- sonst None."""
    sign = 1.0
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        sign = -1.0 if isinstance(node.op, ast.USub) else 1.0
        node = node.operand
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        if isinstance(node.value, bool):
            return None
        return sign * float(node.value)
    return None


def _short(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."
