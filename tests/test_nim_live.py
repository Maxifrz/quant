"""Ein echter Aufruf gegen NVIDIA NIM. Laeuft nur mit Schluessel.

Der Rest der Testsuite prueft die Uebersetzung gegen Attrappen -- und
Attrappen nehmen alles an, was man ihnen gibt. Genau daran ist die erste
Fassung dieses Providers vorbeigelaufen: sie schickte `nvext.guided_json`,
weil die NIM-Dokumentation das empfiehlt, und der gehostete Endpunkt lehnte
es mit HTTP 400 ab. 34 gruene Tests haben das nicht gesehen. Ein einziger
echter Aufruf schon (ADR-040).

**Uebersprungen, wenn kein Schluessel gesetzt ist** -- eine Testsuite, die
Netzwerkzugang und Geld braucht, wird sonst irgendwann uebersprungen, und dann
faellt auch der Rest nicht mehr auf. Ausfuehren mit:

    NVIDIA_API_KEY=nvapi-... uv run pytest tests/test_nim_live.py -m slow -v

Kostet zwei Aufrufe. Gemessene Laufzeit: 90 bis 155 Sekunden **pro Aufruf** --
der Endpunkt ist geteilt und schwankt stark. Das ist keine Eigenheit dieses
Tests, sondern die Zahl, an der die Planung eines Gate-Laufs haengt.
"""

from __future__ import annotations

import os

import pytest

from qt.llm.providers import NIM_KEY_VARS, NimProvider
from qt.llm.schemas import CandidateCritique

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not any(os.environ.get(name) for name in NIM_KEY_VARS),
        reason=f"Kein NIM-Schluessel in {', '.join(NIM_KEY_VARS)}",
    ),
]

# Absichtlich ein Kandidat mit magischer Preiskonstante. Ein formal gueltiges
# JSON beweist nur die Uebertragung; erst eine inhaltlich richtige Antwort
# zeigt, dass der Kritiker als Vorfilter ueberhaupt etwas leistet.
SYSTEM = (
    "Du pruefst Strategie-Kandidaten. Finde Gruende, ihn nicht zu testen. "
    "Eine Zahl, die an ein Kursniveau gebunden ist statt an eine Bar-Zahl "
    "oder einen z-Score, ist der haerteste Befund."
)
PROMPT = """Kandidat:

    class Kaufen(Strategy):
        name = "kaufen"
        warmup_bars = 2

        def on_bar(self, symbol, store):
            window = store.window(symbol, self.timeframe)
            closes = window.closes()
            if closes[-1] > 42000:
                return 1.0
            return 0.0

Begruendung des Autors: "Ueber 42000 ist der Markt im Aufwaertstrend."
"""


@pytest.mark.parametrize("effort", ["low", "medium"])
def test_echter_aufruf_liefert_ein_gueltiges_schema(effort):
    """Die vier Dinge, die sich nur am echten Endpunkt entscheiden.

    1. Wird `response_format` angenommen, oder steigt der Provider ab?
    2. Kommt bei ausgeschaltetem **und** eingeschaltetem Denken Inhalt zurueck,
       oder verbraucht das Modell sein Budget im Gedankengang?
    3. Passt die Antwort auf ein pydantic-Schema dieses Projekts?
    4. Erkennt das Modell die Preiskonstante -- taugt der Vorfilter also?
    """
    provider = NimProvider()

    antwort = provider.parse(
        system=SYSTEM,
        prompt=PROMPT,
        schema=CandidateCritique,
        model=provider.default_model,
        max_tokens=1500,
        effort=effort,
    )

    assert not provider._schema_refused, (
        "Der Endpunkt hat `response_format` abgelehnt. Der Provider laeuft "
        "damit ungefuehrt weiter -- das funktioniert, ist aber schwaecher, "
        "und die Ursache gehoert untersucht statt uebersehen (ADR-040)."
    )
    assert antwort.recommendation in {"proceed", "reject"}
    assert antwort.reasoning.strip(), "Begruendung leer"
    assert "42000" in antwort.reasoning or "Preis" in antwort.reasoning, (
        "Der Kritiker hat die magische Preiskonstante nicht benannt. Ein "
        "Vorfilter, der den offensichtlichsten Befund uebersieht, filtert "
        "nichts."
    )
