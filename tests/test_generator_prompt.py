"""Der Generator-Prompt muss zur echten Sandbox-API passen.

Diese Datei existiert wegen zwei Kandidaten, die ein echter NIM-Lauf
produziert hat und die beide im Probelauf starben -- nicht, weil das Modell
schlecht war, sondern weil der Prompt die Wahrheit ueber die API nicht
vollstaendig sagte:

* `ta.realised_vol(closes, n, bars_per_year)` stand als Signatur im Prompt,
  aber `bars_per_year` war in der Sandbox gar nicht gebunden. Der Indikator
  war aus dem Kandidaten heraus nicht korrekt aufrufbar -- der einzige
  verbleibende Weg waere eine hartkodierte Zahl gewesen, also genau die
  Magic Constant, die der Kritiker ablehnen soll. Ein Aufruf mit zwei
  Argumenten war die logische Folge.
* `ta.*` gibt Skalare zurueck. Der Prompt schrieb `-> float`, sagte aber
  nirgends den Satz "da ist nichts zu indizieren". Ein `rsi[-1]` auf einem
  bereits fertigen float ist die naheliegende Gewohnheit aus pandas/TA-Lib.

Beides sind Abweichungen zwischen Prompt und Code, und beide kosteten einen
bezahlten Aufruf, um sichtbar zu werden. Die Tests hier vergleichen den
Prompt-Text mit `inspect.signature` und mit `INJECTED_NAMES` -- sie fangen
dieselbe Klasse Fehler beim Commit statt im Lauf.
"""

from __future__ import annotations

import inspect
import re

import pytest

from qt.features import ta
from qt.llm.client import GENERATOR_SYSTEM_PROMPT
from qt.research import sandbox

# Zeilen der Bauart "- `ta.sma(values, n)` -> float" aus dem Prompt.
_INDIKATOR_ZEILE = re.compile(r"^- `ta\.(\w+)\(([^`]*)\)`", re.MULTILINE)


def _dokumentierte_indikatoren() -> dict[str, list[str]]:
    """Name -> Argumentliste, so wie der Prompt sie behauptet."""
    gefunden: dict[str, list[str]] = {}
    for name, args in _INDIKATOR_ZEILE.findall(GENERATOR_SYSTEM_PROMPT):
        gefunden[name] = [a.strip() for a in args.split(",") if a.strip()]
    return gefunden


def test_der_prompt_listet_ueberhaupt_indikatoren():
    # Ohne diesen Test wuerde eine umformatierte Liste die beiden Tests
    # unten still gruen lassen: ein leeres dict erfuellt jede Behauptung.
    doku = _dokumentierte_indikatoren()
    assert len(doku) >= 6, f"Regex findet die Indikatorliste nicht mehr: {doku}"


def test_jeder_dokumentierte_indikator_existiert_wirklich():
    for name in _dokumentierte_indikatoren():
        assert hasattr(sandbox.TA_FACADE, name), (
            f"Der Prompt bewirbt `ta.{name}`, aber die Sandbox bindet es nicht. "
            "Ein Kandidat, der darauf hoert, stirbt im Probelauf."
        )


def test_die_dokumentierten_signaturen_stimmen_mit_dem_code_ueberein():
    # Der eigentliche Anlass: die Stelligkeit muss passen. Namen von
    # Positionsargumenten duerfen im Prompt sprechender sein als im Code
    # (`bars_per_year(self.timeframe)` statt `bars_per_year`), die *Anzahl*
    # nicht -- genau daran ist vol_regime_trend gescheitert.
    for name, args in _dokumentierte_indikatoren().items():
        echt = inspect.signature(getattr(ta, name))
        assert len(args) == len(echt.parameters), (
            f"`ta.{name}` nimmt {len(echt.parameters)} Argumente "
            f"{tuple(echt.parameters)}, der Prompt zeigt {len(args)}: {args}"
        )


def test_der_prompt_nennt_genau_die_gebundenen_namen():
    # Ein Name zu wenig -> der Kandidat benutzt ihn nie, obwohl er koennte.
    # Ein Name zu viel -> der Kandidat benutzt ihn und stirbt am NameError.
    for name in sandbox.INJECTED_NAMES:
        assert f"`{name}`" in GENERATOR_SYSTEM_PROMPT, (
            f"`{name}` ist in der Sandbox gebunden, steht aber nicht im Prompt."
        )


def test_der_prompt_warnt_vor_dem_indizieren_von_skalaren():
    # Nicht der Wortlaut ist wichtig, sondern dass die Warnung ueberhaupt
    # dasteht. Sie ist der Grund, warum rsi_reversion neu geschrieben werden
    # muesste -- nicht der Code des Modells, sondern die fehlende Ansage.
    assert "SKALAR" in GENERATOR_SYSTEM_PROMPT
    assert "rsi_last = rsi[-1]" in GENERATOR_SYSTEM_PROMPT


# Platzhalter aus der Prompt-Liste -> ein in der Sandbox gueltiger Ausdruck.
# `bars_per_year(self.timeframe)` steht schon als fertiger Ausdruck im Prompt
# und geht deshalb unveraendert durch.
_PLATZHALTER = {
    "values": "closes",
    "closes": "closes",
    "highs": "window.highs()",
    "lows": "window.lows()",
    "n": "self.LOOKBACK",
}


def _kandidat_der_den_prompt_befolgt(name: str, args: list[str]) -> str:
    """Kleinster Kandidat, der `ta.<name>` genau wie dokumentiert aufruft."""
    ausdruck = ", ".join(_PLATZHALTER.get(a, a) for a in args)
    return (
        "class PromptKandidat(Strategy):\n"
        '    name = "prompt_kandidat"\n'
        "    LOOKBACK = 20\n"
        "    warmup_bars = 30\n"
        "\n"
        "    def on_bar(self, symbol, store):\n"
        "        window = store.window(symbol, self.timeframe)\n"
        "        if len(window) < self.warmup_bars:\n"
        "            return math.nan\n"
        "        closes = window.closes()\n"
        f"        wert = ta.{name}({ausdruck})\n"
        "        return 0.0\n"
    )


@pytest.mark.parametrize("name", sorted(_dokumentierte_indikatoren()))
def test_jeder_indikator_ist_genau_wie_dokumentiert_aufrufbar(name):
    """Der eigentliche Waechter.

    Die Signatur- und Namenstests oben haetten den urspruenglichen Fehler
    *nicht* gefangen: der Prompt nannte drei Argumente, die echte Funktion
    hatte drei -- die Stelligkeit stimmte. Falsch war, dass der Kandidat an
    das dritte gar nicht herankam. Erst der Probelauf zeigt das, weil er den
    Aufruf wirklich ausfuehrt. Genau so ist der Fehler auch im echten Lauf
    aufgefallen, nur eben nach einem bezahlten Generator-Aufruf.
    """
    args = _dokumentierte_indikatoren()[name]
    quelle = _kandidat_der_den_prompt_befolgt(name, args)

    bericht = sandbox.check(quelle)
    assert bericht.ok, f"AST lehnt den dokumentierten Aufruf ab: {bericht.reasons}"

    klasse = sandbox.load_strategy_class(quelle, "PromptKandidat")
    probe = sandbox.probe(klasse, ["BTC/USD"], "4h", n_bars=120)
    assert probe.ok, (
        f"`ta.{name}` laesst sich nicht so aufrufen, wie der Prompt es zeigt: "
        f"{probe.error}"
    )
