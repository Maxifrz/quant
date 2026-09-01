"""Tests der Sandbox.

Die Datei ist bewusst laenger als das, was sie prueft. Hier laeuft fremd
erzeugter Code: ein Fehler in `qt.research.sandbox` ist keine falsche Zahl im
Report, sondern ein Angreifer im Prozess. Deshalb steht jeder Ausbruchsversuch
einzeln benannt in der Liste unten -- eine gesammelte Behauptung "boeser Code
wird abgelehnt" sagt nach dem ersten Refactor niemandem mehr, welcher Fall
verlorenging.
"""

from __future__ import annotations

import ast
import pathlib
from types import ModuleType

import numpy as np
import pytest

from qt.research import sandbox
from qt.strategy.base import Strategy

# Ein minimaler, echter Kandidat im Sandbox-Stil: keine Importe, kein
# __init__ (die Basisklasse bringt es mit), warmup_bars als Klassenattribut
# statt als @property -- `property` gehoert nicht zu den sicheren Builtins.
KANDIDAT = '''
class Kandidat(Strategy):
    """Gegen die Abweichung vom Mittel, sonst flat."""

    name = "kandidat"
    warmup_bars = 40

    def on_bar(self, symbol: str, store) -> float:
        window = store.window(symbol, self.timeframe)
        if len(window) < self.warmup_bars:
            return np.nan
        closes = window.closes()
        z = ta.zscore(closes, 20)
        if not math.isfinite(z):
            return np.nan
        state = self._state
        weight = state.get(symbol, 0.0)
        if z <= -2.0:
            weight = 1.0
        elif z >= 2.0:
            weight = -1.0
        elif abs(z) < 0.5:
            weight = 0.0
        state[symbol] = weight
        return clip_weight(weight)
'''


# ---------------------------------------------------------------------------
# Muss ablehnen
# ---------------------------------------------------------------------------

VERBOTEN: dict[str, str] = {
    "import os": "import os\n",
    "from os import system": "from os import system\n",
    "__import__('os')": "modul = __import__('os')\n",
    "open('/etc/passwd')": "geheim = open('/etc/passwd')\n",
    "eval": "wert = eval('1 + 1')\n",
    "exec": "exec('wert = 1')\n",
    "compile": "code = compile('1', '<s>', 'eval')\n",
    "subclasses": "klassen = ().__class__.__base__.__subclasses__()\n",
    # Die vollstaendige Restricted-Exec-Eskapade: von einem harmlosen Objekt
    # ueber die Klassenhierarchie zu einer beliebigen geladenen Klasse und von
    # deren __init__ in die echten Modul-Globals.
    "restricted exec eskapade": (
        "sys = [].__class__.__base__.__subclasses__()[59]"
        ".__init__.__globals__['sys']\n"
    ),
    # Umgehung der dunder-Sperre per String-Konkatenation: im AST steht kein
    # einziges dunder. Deshalb muss getattr selbst gesperrt sein.
    "getattr mit gebautem dunder": "k = getattr(np, '__' + 'class__')\n",
    "globals()": "b = globals()['__builtins__']\n",
    "while True": "while True:\n    pass\n",
    "for-Schleife": "for i in range(10 ** 9):\n    pass\n",
    "Namensreferenz socket": "s = socket\n",
    "Namensreferenz urllib": "s = urllib.request\n",
    # Ohne die Namensregel genuegte das Umbenennen, um an der
    # Aufruf-Blockliste vorbeizukommen.
    "eval unter anderem Namen": "f = eval\nwert = f('1')\n",
    "setattr": "setattr(np, 'mean', None)\n",
    "f-String mit dunder": 'text = f"{np.__class__}"\n',
    # str.format kommt ueber Formatfelder an Attribute, ohne dass im AST ein
    # dunder auftaucht.
    "str.format mit dunder": 'text = "{0.__class__}".format(np)\n',
    "ndarray.tofile": "np.asarray([1.0]).tofile('/tmp/leak')\n",
    "Zuweisung auf Strategy": "Strategy.on_bar = None\n",
    "lambda": "f = lambda: 1\n",
    "try/except": "try:\n    x = 1\nexcept Exception:\n    x = 2\n",
    "list comprehension": "werte = [i for i in (1, 2, 3)]\n",
    "generator expression": "ok = all(i > 0 for i in (1, 2))\n",
    "with": "with np as n:\n    pass\n",
    "Dekorator": (
        "class A(Strategy):\n"
        "    @property\n"
        "    def warmup_bars(self):\n"
        "        return 5\n"
    ),
    "dunder-Methode definieren": (
        "class A(Strategy):\n"
        "    def __getattr__(self, name):\n"
        "        return None\n"
    ),
    "Syntaxfehler": "def (\n",
}


@pytest.mark.parametrize("code", list(VERBOTEN.values()), ids=list(VERBOTEN))
def test_whitelist_lehnt_ab(code: str) -> None:
    report = sandbox.check(code)
    assert not report.ok
    assert report.reasons
    # Ohne Zeilennummer muss der Empfaenger der Meldung suchen -- und der
    # Empfaenger ist ein Sprachmodell, das dann raet.
    assert all(grund.startswith("Zeile ") for grund in report.reasons)


@pytest.mark.parametrize("code", list(VERBOTEN.values()), ids=list(VERBOTEN))
def test_load_strategy_class_lehnt_dasselbe_ab(code: str) -> None:
    """Kein oeffentlicher Pfad darf milder sein als `check` selbst."""
    with pytest.raises(sandbox.SandboxRejected):
        sandbox.load_strategy_class(code, "Kandidat")


# ---------------------------------------------------------------------------
# Muss akzeptieren -- sonst wird die Whitelist unbemerkt zu eng
# ---------------------------------------------------------------------------


def test_gueltiger_kandidat_wird_akzeptiert() -> None:
    """Das Gegengewicht zur Liste oben.

    Ohne diesen Test wird die Whitelist bei jedem Sicherheitsvorfall ein
    Stueck enger, bis sie irgendwann den Happy Path mitblockiert -- und das
    faellt erst auf, wenn kein einziger Kandidat mehr durchkommt.
    """
    report = sandbox.check(KANDIDAT)
    assert report.ok, report.reasons


def test_gueltiger_kandidat_laeuft() -> None:
    cls = sandbox.load_strategy_class(KANDIDAT, "Kandidat")
    assert issubclass(cls, Strategy)
    bericht = sandbox.probe(cls, ["BTC/USD", "ETH/USD"], "1h")
    assert bericht.ok, bericht.reason
    assert bericht.warmup_bars == 40
    assert bericht.n_calls > 0


def test_whitelist_deckt_die_echte_meanrev_ab() -> None:
    """Die Referenzstrategie ist der Massstab fuer die Breite der Whitelist.

    Geprueft wird `on_bar` von `qt.strategy.library.meanrev`: das ist der
    Code, den ein Kandidat nachbilden koennen muss. (Der Rest der Datei
    enthaelt Importe und `super().__init__` -- beides gehoert nicht in einen
    Sandbox-Kandidaten und ist deshalb ausgenommen.)
    """
    quelle = pathlib.Path("src/qt/strategy/library/meanrev.py").read_text()
    baum = ast.parse(quelle)
    on_bar = [
        node
        for node in ast.walk(baum)
        if isinstance(node, ast.FunctionDef) and node.name == "on_bar"
    ]
    assert on_bar, "on_bar in meanrev.py nicht gefunden"
    verwendet = {type(node) for node in ast.walk(on_bar[0])}
    fehlend = sorted(t.__name__ for t in verwendet - sandbox.ALLOWED_NODE_TYPES)
    assert not fehlend, f"Whitelist zu eng, fehlt: {fehlend}"


# ---------------------------------------------------------------------------
# Der wichtigste Test: check() steht vor exec(), nachweislich
# ---------------------------------------------------------------------------

# Ein Payload mit beobachtbarem Seiteneffekt. Ein Test, der nur die Exception
# prueft, belegt nicht, dass exec nie erreicht wurde -- er belegt nur, dass
# hinterher geworfen wurde.
_PAYLOAD = "import pathlib\npathlib.Path({pfad!r}).write_text('pwned')\n"


def test_abgelehnter_code_erreicht_exec_nie(tmp_path: pathlib.Path) -> None:
    kontrolle = tmp_path / "kontrolle.txt"
    opfer = tmp_path / "opfer.txt"

    # Kontrolllauf: der Payload ist wirklich scharf. Ohne diesen Schritt
    # koennte die Behauptung unten auch dann halten, wenn der Payload gar
    # nichts tut.
    exec(compile(_PAYLOAD.format(pfad=str(kontrolle)), "<kontrolle>", "exec"), {})
    assert kontrolle.exists()

    with pytest.raises(sandbox.SandboxRejected):
        sandbox.load_strategy_class(_PAYLOAD.format(pfad=str(opfer)), "Egal")

    assert not opfer.exists(), "exec wurde trotz Ablehnung erreicht"


def test_exec_steht_nur_in_load_strategy_class() -> None:
    """Strukturelle Zusicherung gegen kuenftige Refactorings.

    Solange `exec` genau einmal vorkommt und zwar im Koerper von
    `load_strategy_class` -- also hinter dem `check()`-Aufruf --, kann kein
    zweiter Pfad entstehen, der die Pruefung versehentlich auslaesst.
    """
    quelle = pathlib.Path(sandbox.__file__).read_text()
    baum = ast.parse(quelle)

    def aufrufe(knoten: ast.AST) -> int:
        return sum(
            1
            for n in ast.walk(knoten)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id in {"exec", "eval"}
        )

    lader = [
        n
        for n in ast.walk(baum)
        if isinstance(n, ast.FunctionDef) and n.name == "load_strategy_class"
    ]
    assert len(lader) == 1
    assert aufrufe(baum) == 1
    assert aufrufe(lader[0]) == 1


def test_load_strategy_class_prueft_die_basisklasse() -> None:
    code = "class Fremd:\n    name = 'fremd'\n"
    with pytest.raises(sandbox.SandboxRejected, match="Strategy"):
        sandbox.load_strategy_class(code, "Fremd")


def test_load_strategy_class_meldet_fehlende_klasse() -> None:
    with pytest.raises(sandbox.SandboxRejected, match="nicht definiert"):
        sandbox.load_strategy_class(KANDIDAT, "GibtsNicht")


def test_fehler_auf_modulebene_ist_eine_ablehnung() -> None:
    """Ein Kandidat, der beim Laden knallt, ist ein schlechter Kandidat --
    kein Systemfehler."""
    with pytest.raises(sandbox.SandboxRejected, match="ZeroDivisionError"):
        sandbox.load_strategy_class("wert = 1.0 / 0.0\n", "Egal")


# ---------------------------------------------------------------------------
# Ebene 3: die Fassaden
# ---------------------------------------------------------------------------


def test_np_fassade_kennt_save_und_load_nicht() -> None:
    """Sonst ist die Fassade beim naechsten Refactor still wieder numpy.

    Genau das ist der Punkt der Fassade: die AST-Pruefung sieht in
    `np.save(pfad, x)` nur ein voellig normales Attribute+Call. Verhindert
    wird der Aufruf nicht dadurch, dass ihn jemand erkennt, sondern dadurch,
    dass die Methode auf dem uebergebenen Objekt nicht existiert.
    """
    assert not isinstance(sandbox.NP_FACADE, ModuleType)
    for name in (
        "save",
        "load",
        "savez",
        "savetxt",
        "fromfile",
        "genfromtxt",
        "memmap",
        "lib",
        "testing",
    ):
        assert not hasattr(sandbox.NP_FACADE, name), f"np-Fassade hat {name}"
    # ... und trotzdem alles, was Rechnen braucht.
    for name in ("mean", "std", "sqrt", "where", "clip", "cumsum", "nan"):
        assert hasattr(sandbox.NP_FACADE, name)


def test_ta_fassade_reicht_numpy_nicht_weiter() -> None:
    """`qt.features.ta` ist frei von I/O -- als Modulobjekt aber nicht harmlos.

    Ein Modul reicht seine eigenen Importe weiter: `ta.np` waere das echte
    numpy und damit die np-Fassade in einem Schritt umgangen.
    """
    assert not isinstance(sandbox.TA_FACADE, ModuleType)
    assert not hasattr(sandbox.TA_FACADE, "np")
    assert sandbox.TA_FACADE.zscore is not None
    assert hasattr(sandbox.TA_FACADE, "atr")


def test_safe_builtins_bleiben_klein() -> None:
    for name in ("type", "object", "super", "property", "__import__", "eval", "open"):
        assert name not in sandbox.SAFE_BUILTINS


def test_getattr_und_setattr_sind_gesperrt() -> None:
    """Ohne sie waere die dunder-Sperre per Stringbau umgehbar."""
    assert "getattr" in sandbox.FORBIDDEN_CALL_NAMES
    assert "setattr" in sandbox.FORBIDDEN_CALL_NAMES


def test_kandidaten_teilen_keine_globals() -> None:
    """Zwei Laeufe duerfen sich nicht sehen -- sonst ist keiner reproduzierbar."""
    erst = sandbox.load_strategy_class(KANDIDAT, "Kandidat")
    zweit = sandbox.load_strategy_class(KANDIDAT, "Kandidat")
    assert erst is not zweit


# ---------------------------------------------------------------------------
# probe()
# ---------------------------------------------------------------------------

# Besteht die Whitelist vollstaendig, rechnet aber pro Bar auf einem
# 1500x1500-Gitter. Genau der Fall, den ein Schleifenverbot nicht abdeckt:
# eine einzelne, technisch erlaubte numpy-Operation.
TEUER = '''
class Teuer(Strategy):
    """Erlaubt, aber unbezahlbar."""

    name = "teuer"
    warmup_bars = 5

    def on_bar(self, symbol, store):
        basis = np.asarray(range(1500))
        gitter = basis[:, None] * basis
        summe = np.sum(np.exp(np.log(np.sqrt(np.abs(gitter) + 1.0))))
        summe = summe + np.sum(np.cumsum(np.sqrt(np.abs(gitter) + 2.0)))
        return clip_weight(np.sign(summe) * 0.0)
'''


def test_probe_timeout_greift() -> None:
    assert sandbox.check(TEUER).ok, sandbox.check(TEUER).reasons
    cls = sandbox.load_strategy_class(TEUER, "Teuer")
    # n_bars knapp ueber warmup: der Aufruf, der den Timeout ausloest, laeuft
    # im Hintergrund zu Ende, und er soll dabei nicht die halbe Testsuite
    # blockieren.
    bericht = sandbox.probe(cls, ["BTC/USD"], "1h", n_bars=6, timeout_s=0.02)
    assert not bericht.ok
    assert "Timeout" in bericht.reason


def test_probe_meldet_fehler_in_on_bar() -> None:
    code = '''
class Kaputt(Strategy):
    """Rechnet durch null."""

    name = "kaputt"
    warmup_bars = 10

    def on_bar(self, symbol, store):
        return 1.0 / 0.0
'''
    cls = sandbox.load_strategy_class(code, "Kaputt")
    bericht = sandbox.probe(cls, ["BTC/USD"], "1h", n_bars=30)
    assert not bericht.ok
    assert "ZeroDivisionError" in bericht.reason


def test_probe_lehnt_nicht_numerisches_gewicht_ab() -> None:
    code = '''
class Redselig(Strategy):
    """Gibt Text statt Gewicht."""

    name = "redselig"
    warmup_bars = 10

    def on_bar(self, symbol, store):
        return "long"
'''
    cls = sandbox.load_strategy_class(code, "Redselig")
    bericht = sandbox.probe(cls, ["BTC/USD"], "1h", n_bars=30)
    assert not bericht.ok
    assert "erwartet float" in bericht.reason


@pytest.mark.parametrize(
    ("warmup", "erwartet"),
    [
        ("0", "muss > 0"),
        ("-5", "muss > 0"),
        ("99999", "ueberschreitet"),
        ("500", "nie zum Zug"),
    ],
    ids=["null", "negativ", "absurd gross", "groesser als der Probelauf"],
)
def test_probe_prueft_warmup_plausibilitaet(warmup: str, erwartet: str) -> None:
    code = (
        "class W(Strategy):\n"
        '    name = "w"\n'
        f"    warmup_bars = {warmup}\n"
        "    def on_bar(self, symbol, store):\n"
        "        return 0.0\n"
    )
    cls = sandbox.load_strategy_class(code, "W")
    bericht = sandbox.probe(cls, ["BTC/USD"], "1h", n_bars=400)
    assert not bericht.ok
    assert erwartet in bericht.reason


def test_probe_erkennt_warmup_als_methode() -> None:
    """`warmup_bars` als Methode statt als Attribut ist der haeufigste
    Fehler, wenn @property nicht zur Verfuegung steht."""
    code = (
        "class M(Strategy):\n"
        '    name = "m"\n'
        "    def warmup_bars(self):\n"
        "        return 10\n"
        "    def on_bar(self, symbol, store):\n"
        "        return 0.0\n"
    )
    cls = sandbox.load_strategy_class(code, "M")
    bericht = sandbox.probe(cls, ["BTC/USD"], "1h", n_bars=50)
    assert not bericht.ok
    assert "erwartet int" in bericht.reason


def test_probe_ist_reproduzierbar() -> None:
    """Gleiche Eingabe, gleicher Bericht -- sonst ist ein Fehlschlag nicht
    nachstellbar."""
    cls = sandbox.load_strategy_class(KANDIDAT, "Kandidat")
    erst = sandbox.probe(cls, ["BTC/USD"], "1h", n_bars=200)
    zweit = sandbox.probe(cls, ["BTC/USD"], "1h", n_bars=200)
    assert erst == zweit


# ---------------------------------------------------------------------------
# scan_literals()
# ---------------------------------------------------------------------------

MIT_PREISNIVEAU = '''
class Fix(Strategy):
    """Hartkodiertes Preisniveau."""

    name = "fix"
    warmup_bars = 1440

    def on_bar(self, symbol, store):
        window = store.window(symbol, self.timeframe)
        if len(window) < 1440:
            return np.nan
        closes = window.closes()
        if closes[-1] > 42000.0:
            return -1.0
        return 0.0
'''


def test_scan_literals_findet_hartkodiertes_preisniveau() -> None:
    flags = sandbox.scan_literals(MIT_PREISNIVEAU)
    assert [flag.value for flag in flags] == [42000.0]
    zeile = MIT_PREISNIVEAU.splitlines().index("        if closes[-1] > 42000.0:") + 1
    assert flags[0].line == zeile
    assert "42000" in flags[0].context


def test_scan_literals_blockiert_nicht() -> None:
    """Der Hinweis ist ein Hinweis: derselbe Code besteht die Whitelist.

    Ob 42000 ein ueberangepasstes Preisniveau oder eine sinnvolle Konstante
    ist, kann nur beurteilen, wer die Bedeutung kennt -- also ein Mensch oder
    ein Modell, nicht ein Parser.
    """
    assert sandbox.check(MIT_PREISNIVEAU).ok
    assert sandbox.scan_literals(MIT_PREISNIVEAU)


def test_scan_literals_meldet_lookback_nicht() -> None:
    """1440 Bars Lookback sind legitim -- nur der Vergleich mit einem Preis
    ist verdaechtig. Der 1440er-Vergleich oben steht gegen `len(window)`."""
    flags = sandbox.scan_literals(MIT_PREISNIVEAU)
    assert 1440 not in [flag.value for flag in flags]


def test_scan_literals_ignoriert_normale_parameter() -> None:
    code = (
        "class P(Strategy):\n"
        '    name = "p"\n'
        "    warmup_bars = 50\n"
        "    def on_bar(self, symbol, store):\n"
        "        closes = store.window(symbol, self.timeframe).closes()\n"
        "        z = ta.zscore(closes, 20)\n"
        "        if z > 2.0:\n"
        "            return -1.0\n"
        "        return 0.0\n"
    )
    assert sandbox.scan_literals(code) == []


def test_scan_literals_faellt_bei_syntaxfehler_nicht_um() -> None:
    assert sandbox.scan_literals("def (\n") == []


def test_scan_literals_verwechselt_allow_short_nicht_mit_low() -> None:
    """Ein Teilstring-Vergleich wuerde in `allow_short` ein "low" finden."""
    code = "class Q(Strategy):\n    allow_short = 5000\n"
    code += "    def on_bar(self, symbol, store):\n"
    code += "        if self.allow_short > 5000:\n            return 0.0\n"
    code += "        return 1.0\n"
    assert sandbox.scan_literals(code) == []


def test_numpy_bleibt_unberuehrt() -> None:
    """Die Fassade darf das echte numpy nicht veraendert haben."""
    assert hasattr(np, "save")
    assert np.mean is sandbox.NP_FACADE.mean
