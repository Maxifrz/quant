"""Zwei Wege zu einem Modell: Anthropic und NVIDIA NIM.

Bis hierher war "das Modell" gleichbedeutend mit "Anthropic": vier Clients
(Allokator, Szenario, Generator, Kritiker) trugen jeder eine eigene Kopie von
`_ensure_client` und jeder denselben `messages.parse`-Aufruf. Ein zweiter
Anbieter haette diese Kopien verdoppelt -- acht Stellen, an denen dieselbe
Entscheidung getroffen wird, und acht Stellen, an denen sie auseinanderlaufen
kann.

Deshalb liegt hier eine **Naht** statt einer zweiten Client-Familie: ein
Provider uebersetzt (Systemprompt, Prompt, Schema, Modell, Token-Budget,
Effort) in einen Aufruf und die Antwort zurueck in ein validiertes
pydantic-Modell. Was ein Client fachlich tut -- welchen Prompt er stellt, was
er cacht, wie er den Key bildet -- bleibt dort, wo es hingehoert.

Drei Dinge, die an dieser Naht bewusst so entschieden sind:

**Der Provider gehoert in den Cache-Key.** Dieselbe Frage an Claude und an
Nemotron sind zwei Antworten. Stuende der Provider nicht im Key, lieferte ein
NIM-Lauf stillschweigend die gecachte Claude-Antwort -- kein Fehlschlag, nur
ein falsches Ergebnis, und zwar genau die Sorte, die man in einem Backtest
nicht mehr findet. Dieselbe Ueberlegung wie bei Modell und Effort (ADR-028).

**Die Effort-Stufen bleiben eine Sprache, aber keine Aequivalenz.** Die CLI
kennt weiterhin `low` bis `max`. NIM hat aber nicht fuenf Denkstufen, sondern
drei Zustaende (aus, mittel, voll). Die Abbildung in `NimProvider.EFFORT_MAP`
ist deshalb eine Naeherung und wird als solche benannt: `xhigh` und `max`
denken auf NIM **nicht** tiefer als `high`, sie bekommen nur mehr Token. Wer
das nicht weiss, glaubt, er habe etwas eingestellt, das es nicht gibt.

**Anthropic bleibt Default.** Der gesamte bisherige Cache, alle ADRs und alle
gemessenen Zahlen haengen daran. Ein zweiter Anbieter ist eine Option, kein
Umzug.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from qt.core.config import DEFAULT_LLM_MODEL, DEFAULT_NIM_MODEL

# Effort-Stufen der Kommandozeile. Quelle ist das Anthropic-SDK (siehe
# `tests/test_cli_effort.py`); NIM erbt die Sprache, nicht die Semantik.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


class LLMUnavailable(RuntimeError):
    """Kein API-Zugang, fehlendes SDK, oder eine unbrauchbare Antwort.

    Bewusst eine Ausnahme fuer beide Anbieter: der Aufrufer soll auf "das
    Modell steht nicht zur Verfuegung" reagieren koennen, ohne zu wissen,
    welcher Anbieter dahinterliegt.
    """


class LLMProvider(Protocol):
    """Was ein Anbieter koennen muss. Mehr braucht kein Client von ihm."""

    name: str
    default_model: str

    @property
    def cache_tag(self) -> str:
        """Was diesen Anbieter im Cache-Key von jedem anderen trennt.

        Nicht einfach `name`: zwei Provider desselben Anbieters koennen
        verschieden antworten, wenn sie verschieden aufrufen. Genau das ist
        einmal passiert -- nach dem Abschalten der erzwungenen Ausgabeform
        lieferte der Cache die zehn kaputten Antworten des vorigen Laufs
        zurueck, und der Lauf sah aus, als haette die Korrektur nichts
        bewirkt (ADR-041).
        """
        ...

    def parse(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[BaseModel],
        model: str,
        max_tokens: int,
        effort: str,
    ) -> BaseModel:
        """Einen Aufruf machen und die Antwort gegen `schema` validieren."""
        ...


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


class AnthropicProvider:
    """Der bisherige Weg, unveraendert im Verhalten.

    `messages.parse` mit `output_format` legt das Modell auf das Schema fest;
    die Antwort muss nicht aus freiem Text geparst werden. Der Systemprompt
    wird als gecachter Praefix uebergeben -- er ist eingefroren, das Briefing
    variiert.
    """

    name = "anthropic"
    default_model = DEFAULT_LLM_MODEL

    @property
    def cache_tag(self) -> str:
        """Eine Aufrufform, also nichts zu unterscheiden."""
        return self.name

    def __init__(self, client: Any = None) -> None:
        # `client` injizierbar, damit Tests den Aufruf pruefen koennen, ohne
        # einen Schluessel zu haben. Im Ernstfall bleibt er None und wird beim
        # ersten Aufruf gebaut.
        self._client = client

    def parse(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[BaseModel],
        model: str,
        max_tokens: int,
        effort: str,
    ) -> BaseModel:
        client = self._ensure_client()
        try:
            response = client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
            )
        except LLMUnavailable:
            raise
        except Exception as exc:
            # Das SDK entscheidet erst beim Aufruf ueber die Authentifizierung,
            # nicht beim Bau des Clients. Ein fehlender Schluessel taucht
            # deshalb hier auf -- und zwar als TypeError, was ohne diese
            # Uebersetzung wie ein Programmierfehler aussaehe statt wie eine
            # fehlende Konfiguration.
            raise LLMUnavailable(
                f"Aufruf fehlgeschlagen ({type(exc).__name__}): {exc}. "
                "Ohne API-Zugang laeuft nur ein Lauf gegen gefuellten Cache "
                "oder gegen einen Stub-Client."
            ) from exc

        if getattr(response, "stop_reason", None) == "refusal":
            kategorie = getattr(
                getattr(response, "stop_details", None), "category", "ohne Kategorie"
            )
            raise LLMUnavailable(f"Das Modell hat die Anfrage abgelehnt ({kategorie}).")

        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            raise LLMUnavailable("Antwort enthielt kein auswertbares Schema.")
        return parsed

    def _ensure_client(self) -> Any:
        """SDK-Client bauen, mit sprechendem Fehler statt Absturz.

        Import und Schluesselpruefung passieren erst hier, nicht beim
        Modulimport: sonst braeuchte jeder Testlauf und jeder Backtest gegen
        Cache einen API-Schluessel.
        """
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as exc:
            raise LLMUnavailable(
                "Das Paket `anthropic` fehlt. Installieren mit: uv add anthropic"
            ) from exc
        try:
            self._client = anthropic.Anthropic()
        except Exception as exc:
            raise LLMUnavailable(
                "Kein API-Zugang. Setze ANTHROPIC_API_KEY oder melde dich mit "
                "`ant auth login` an. Ein Lauf gegen einen gefuellten Cache "
                "laeuft auch ohne."
            ) from exc
        return self._client


# ---------------------------------------------------------------------------
# NVIDIA NIM
# ---------------------------------------------------------------------------

# Der gehostete Endpunkt. Eine selbst betriebene NIM-Instanz hoert
# typischerweise auf http://localhost:8000/v1 -- deshalb ist die URL ein
# Konstruktor-Argument und keine Konstante im Aufruf.
NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"

# Umgebungsvariablen, in denen der Schluessel liegen darf, in dieser
# Reihenfolge. Mehrere, weil die NVIDIA-Beispiele uneinheitlich sind und ein
# Schluessel, der im "falschen" Namen steht, sonst wie ein fehlender aussieht.
NIM_KEY_VARS = ("NVIDIA_API_KEY", "NIM_API_KEY", "NVIDIA_NIM_API_KEY")

# Anweisung, die dem Systemprompt fuer NIM angehaengt wird. Anthropic bekommt
# das Schema ueber `output_format` und braucht sie nicht; ein
# OpenAI-kompatibler Endpunkt muss dem Modell dagegen im Text sagen, was es
# produzieren soll -- `response_format` erzwingt nur die *Form*, nicht den
# Inhalt.
#
# Wichtig: der eingefrorene Systemprompt in `qt.llm.client` bleibt davon
# unberuehrt. Diese Ergaenzung passiert hier, im Provider, und der Provider
# steht im Cache-Key. Damit sind die beiden Anbieter sauber getrennt, ohne dass
# ein Prompt zweimal gepflegt werden muss.
_JSON_INSTRUCTION = """

ANTWORTFORMAT: Gib ausschliesslich ein JSON-Objekt aus, das genau diesem
JSON-Schema entspricht. Kein Fliesstext davor oder danach, keine Code-Zaeune,
keine Erklaerung ausserhalb des JSON.

{schema}"""

# Reasoning-Modelle geben ihren Gedankengang je nach Bereitstellung entweder in
# einem eigenen Feld zurueck oder inline im Text. Der Inline-Fall muss weg,
# bevor irgendetwas nach JSON sucht.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class NimProvider:
    """NVIDIA NIM ueber die OpenAI-kompatible Chat-Completions-Schnittstelle.

    Fuenf Eigenheiten gegenueber Anthropic, die hier abgefangen werden. Alle
    fuenf sind am gehosteten Endpunkt gemessen, nicht aus der Dokumentation
    abgeschrieben (ADR-040):

    1. **Erzwungene Ausgabeform ist hier aus, und zwar mit Absicht.** Der
       gehostete Endpunkt kennt das dokumentierte `nvext.guided_json` nicht
       (HTTP 400), und das OpenAI-Standardfeld `response_format` nimmt er
       zwar an -- seine grammatikgesteuerte Dekodierung kann aber **keinen
       Zeilenumbruch in einem String erzeugen**. Gemessen: derselbe Prompt
       liefert gefuehrt eine einzeilige Klasse (syntaktisch tot), ungefuehrt
       dieselbe Klasse mit 17 Zeilen (ADR-041).

       Das trifft nicht nur den Generator. Jedes Freitextfeld -- die
       Begruendung des Kritikers, die des Allokators -- wuerde dabei **still**
       verstuemmelt: kein Fehler, nur zerstoerter Text. Ein Mechanismus, der
       Inhalte lautlos beschaedigt, ist schlechter als keiner. Die Form
       traegt deshalb die Anweisung im Systemprompt plus die
       pydantic-Validierung. `guided=True` bleibt als Schalter fuer eine
       Bereitstellung, die es besser kann.
    2. **Denk-Token zaehlen gegen `max_tokens`.** Mit eingeschaltetem Denken
       kann ein Aufruf sein gesamtes Budget im Gedankengang verbrauchen und
       eine abgeschnittene Antwort liefern. Das Budget wird deshalb angehoben,
       und `finish_reason == "length"` wird als das benannt, was es ist.
    3. **Ein Denkbudget gibt es nicht.** `nvext.max_thinking_tokens` steht in
       der Feldliste des Endpunkts, wird vom Runner aber abgelehnt
       ("thinking_token_budget is not yet supported by the V2 model runner").
       Deshalb bleibt die Effort-Abbildung bei den drei Zustaenden aus
       `chat_template_kwargs` -- das ist eine gemessene Grenze, keine
       Auslassung.
    4. **Der Gedankengang kommt getrennt, meistens.** Am gehosteten Endpunkt
       steht er in `reasoning_content`, der Inhalt bleibt sauber. Andere
       Bereitstellungen schreiben ihn inline; `<think>...</think>` wird
       deshalb trotzdem entfernt, bevor das JSON gesucht wird.
    5. **Temperatur 0 ist hier keine gute Idee.** NVIDIA empfiehlt fuer die
       Reasoning-Modi ausdruecklich `temperature=1.0, top_p=0.95`; ein auf 0
       gedrehtes Reasoning-Modell wird nicht determiniert, sondern schlechter.
       Reproduzierbarkeit kommt in diesem Projekt ohnehin nicht vom Sampler,
       sondern vom Antwort-Cache -- ein fester `seed` ist nur die zweite
       Verteidigungslinie.
    """

    name = "nim"
    default_model = DEFAULT_NIM_MODEL

    # Die fuenf Stufen der Kommandozeile auf die drei Zustaende abbilden, die
    # NIM wirklich hat. `xhigh` und `max` denken **nicht** tiefer als `high` --
    # sie heben nur die Token-Decke. Siehe Klassen-Docstring des Moduls.
    EFFORT_MAP = {
        "low": {"enable_thinking": False},
        "medium": {"enable_thinking": True, "medium_effort": True},
        "high": {"enable_thinking": True},
        "xhigh": {"enable_thinking": True},
        "max": {"enable_thinking": True},
    }

    # Faktor auf `max_tokens`, weil der Gedankengang mitzaehlt (Punkt 2 oben).
    TOKEN_FACTOR = {"low": 1, "medium": 3, "high": 4, "xhigh": 6, "max": 8}

    # Wiederholungen bei 429 und 5xx. Hoeher als die zwei des SDK, und
    # ausdruecklich gesetzt statt geerbt: der gehostete Endpunkt hat im ersten
    # echten Testlauf ein "Service temporarily overloaded" (HTTP 503)
    # zurueckgegeben. Ein durchgereichter 503 wird im Allokator zu einem
    # Rueckfall auf Gleichgewichtung -- der Lauf laeuft weiter, sieht gesund
    # aus und misst heimlich eine Baseline (ADR-018). Ein Gate-Lauf mit 145
    # Aufrufen darf daran nicht stillschweigend seine Aussage verlieren.
    MAX_RETRIES = 4

    # Der Default des SDK ist 600 Sekunden Lesezeit. Gemessen dauert ein
    # Aufruf 40 bis 110 Sekunden; ein haengender wuerde einen Lauf zehn
    # Minuten blockieren, bevor irgendjemand etwas merkt.
    TIMEOUT_S = 300.0

    def __init__(
        self,
        client: Any = None,
        base_url: str = NIM_BASE_URL,
        api_key: str | None = None,
        seed: int = 20240101,
        temperature: float = 1.0,
        top_p: float = 0.95,
        guided: bool = False,
    ) -> None:
        self._client = client
        self.base_url = base_url
        self.api_key = api_key
        self.seed = seed
        self.temperature = temperature
        self.top_p = top_p
        # Default aus, siehe Punkt 1 im Klassen-Docstring: die gefuehrte
        # Dekodierung dieses Endpunkts verschluckt Zeilenumbrueche. Der
        # Schalter bleibt, weil eine andere Bereitstellung es koennen kann --
        # aber er ist eine bewusste Entscheidung des Aufrufers, kein Default.
        self.guided = guided
        # Wird auf True gesetzt, sobald der Endpunkt `response_format` einmal
        # abgelehnt hat. Ein Lauf macht hunderte Aufrufe; ohne dieses Merken
        # zahlte er den Fehlschlag jedes Mal erneut.
        self._schema_refused = False

    @property
    def cache_tag(self) -> str:
        """Gefuehrt und ungefuehrt sind zwei Antworten, nicht eine.

        Gemessen, nicht befuerchtet: gefuehrt kam der Strategie-Code einzeilig
        zurueck, ungefuehrt mit 17 Zeilen (ADR-041). Waeren beide unter
        demselben Key gelandet, lieferte der Cache nach der Korrektur weiter
        die kaputte Fassung -- was er in genau diesem Projekt einmal getan
        hat, und der Lauf sah aus, als haette sich nichts geaendert.
        """
        return f"{self.name}+gefuehrt" if self.guided else self.name

    # -- Aufruf --------------------------------------------------------------

    def parse(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[BaseModel],
        model: str,
        max_tokens: int,
        effort: str,
    ) -> BaseModel:
        json_schema = schema.model_json_schema()
        system_text = system + _JSON_INSTRUCTION.format(
            schema=json.dumps(json_schema, indent=2, ensure_ascii=False)
        )
        budget = max_tokens * self.TOKEN_FACTOR.get(effort, 1)

        text = self._complete(
            system=system_text,
            prompt=prompt,
            schema=schema,
            json_schema=json_schema,
            model=model,
            max_tokens=budget,
            effort=effort,
        )
        return _validate(text, schema)

    def _complete(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[BaseModel],
        json_schema: dict,
        model: str,
        max_tokens: int,
        effort: str,
        gefuehrt: bool | None = None,
    ) -> str:
        client = self._ensure_client()
        if gefuehrt is None:
            gefuehrt = self.guided and not self._schema_refused

        weitere: dict[str, Any] = {}
        if gefuehrt:
            weitere["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": json_schema},
            }

        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                temperature=self.temperature,
                top_p=self.top_p,
                seed=self.seed,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                extra_body={"chat_template_kwargs": self._chat_template_kwargs(effort)},
                **weitere,
            )
        except Exception as exc:
            if gefuehrt and _looks_like_rejected_parameter(exc):
                # Genau ein Abstieg, und er wird gemerkt. Danach traegt die
                # Anweisung im Systemprompt die Form, und die
                # pydantic-Validierung faengt, was das Modell trotzdem
                # danebenlegt.
                self._schema_refused = True
                return self._complete(
                    system=system,
                    prompt=prompt,
                    schema=schema,
                    json_schema=json_schema,
                    model=model,
                    max_tokens=max_tokens,
                    effort=effort,
                    gefuehrt=False,
                )
            raise LLMUnavailable(
                f"NIM-Aufruf fehlgeschlagen ({type(exc).__name__}): {exc}. "
                f"Erwartet wird ein Schluessel in einer der Variablen "
                f"{', '.join(NIM_KEY_VARS)} und ein erreichbarer Endpunkt "
                f"unter {self.base_url}."
            ) from exc

        return _text_of(response, max_tokens=max_tokens, effort=effort)

    def _chat_template_kwargs(self, effort: str) -> dict[str, Any]:
        try:
            return dict(self.EFFORT_MAP[effort])
        except KeyError:
            raise LLMUnavailable(
                f"Unbekannte Effort-Stufe {effort!r} fuer NIM. "
                f"Verfuegbar: {list(self.EFFORT_MAP)}"
            ) from None

    def _ensure_client(self) -> Any:
        """OpenAI-kompatiblen Client bauen, erst beim ersten Aufruf.

        `openai` ist bewusst ein optionales Extra und keine Grundabhaengigkeit:
        wer bei Anthropic bleibt -- der Default -- soll dafuer kein zweites SDK
        installieren muessen. Dieselbe Ueberlegung wie beim `timesfm`-Extra
        (ADR-022).
        """
        if self._client is not None:
            return self._client
        try:
            import openai
        except ImportError as exc:
            raise LLMUnavailable(
                "Das Paket `openai` fehlt -- NIM spricht die OpenAI-kompatible "
                "Schnittstelle. Installieren mit: uv sync --extra nim"
            ) from exc

        key = self.api_key or _first_env(NIM_KEY_VARS)
        if not key:
            raise LLMUnavailable(
                "Kein NIM-Schluessel. Setze eine der Variablen "
                f"{', '.join(NIM_KEY_VARS)} (Wert beginnt mit `nvapi-`)."
            )

        try:
            self._client = openai.OpenAI(
                base_url=self.base_url,
                api_key=key,
                max_retries=self.MAX_RETRIES,
                timeout=self.TIMEOUT_S,
            )
        except Exception as exc:
            raise LLMUnavailable(
                f"NIM-Client liess sich nicht bauen ({type(exc).__name__}): {exc}"
            ) from exc
        return self._client


# ---------------------------------------------------------------------------
# Antworten auswerten
# ---------------------------------------------------------------------------


def _first_env(names: tuple[str, ...]) -> str | None:
    import os

    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _get(obj: Any, name: str) -> Any:
    """Feld lesen, egal ob Objekt oder dict.

    Notwendig, nicht bequem: NIM ist OpenAI-*kompatibel*, nicht identisch. Was
    das typisierte Modell des `openai`-SDK nicht kennt, landet in
    `model_extra` statt als Attribut -- und je nach Bereitstellung kommt die
    Antwort auch schlicht als dict an. Drei Zugriffsarten an einer Stelle sind
    besser als drei Sonderfaelle an jeder Lesestelle.
    """
    if isinstance(obj, dict):
        return obj.get(name)
    value = getattr(obj, name, None)
    if value is None:
        extra = getattr(obj, "model_extra", None)
        if isinstance(extra, dict):
            return extra.get(name)
    return value


def _text_of(response: Any, *, max_tokens: int, effort: str) -> str:
    """Den Antworttext aus einer Chat-Completion holen.

    Der Gedankengang wird verworfen, nicht ausgewertet: er ist fuer die
    Diagnose interessant, aber er gehoert nicht in den Cache -- sonst haengt
    der Inhalt eines Cache-Eintrags an einem Feld, das je nach Bereitstellung
    da ist oder nicht.
    """
    choices = _get(response, "choices") or []
    if not choices:
        raise LLMUnavailable("NIM-Antwort enthielt keine Auswahl (`choices` leer).")

    choice = choices[0]
    finish = _get(choice, "finish_reason")
    message = _get(choice, "message")
    text = (_get(message, "content") if message is not None else None) or ""

    if finish == "length":
        raise LLMUnavailable(
            f"NIM-Antwort wurde bei {max_tokens} Token abgeschnitten (Effort "
            f"{effort!r}). Denk-Token zaehlen gegen dieses Budget -- entweder "
            "`--effort` senken oder das Token-Budget des Clients anheben."
        )

    if not text.strip():
        # Kann passieren, wenn das Modell alles in den Gedankengang geschrieben
        # hat. Das ist eine andere Ursache als "Antwort abgeschnitten" und
        # verdient deshalb einen eigenen Satz.
        raise LLMUnavailable(
            "NIM-Antwort hatte einen leeren Inhalt. Bei eingeschaltetem Denken "
            "steht der Text moeglicherweise vollstaendig im Gedankengang; ein "
            "niedrigerer Effort oder ein groesseres Token-Budget hilft."
        )
    return text


def _strip_reasoning(text: str) -> str:
    return _THINK_BLOCK.sub("", text)


def _extract_json(text: str) -> str:
    """Das JSON-Objekt aus einer Antwort schneiden.

    Reihenfolge mit Absicht: erst der Gedankengang raus, dann Code-Zaeune, dann
    die aeusserste geschweifte Klammer. Wer zuerst nach `{` sucht, findet unter
    Umstaenden eine Klammer *im* Gedankengang und parst den Entwurf statt der
    Antwort.
    """
    cleaned = _strip_reasoning(text).strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise LLMUnavailable(
            "NIM-Antwort enthielt kein JSON-Objekt. Anfang der Antwort: "
            f"{cleaned[:200]!r}"
        )
    return cleaned[start : end + 1]


def _validate(text: str, schema: type[BaseModel]) -> BaseModel:
    """Text -> Schema, mit sprechendem Fehler statt einer rohen Ausnahme."""
    blob = _extract_json(text)
    try:
        payload = json.loads(blob)
    except (json.JSONDecodeError, ValueError) as exc:
        raise LLMUnavailable(
            f"NIM-Antwort war kein gueltiges JSON ({exc}). Anfang: {blob[:200]!r}"
        ) from exc
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        raise LLMUnavailable(
            f"NIM-Antwort passte nicht auf {schema.__name__}: {exc}"
        ) from exc


def _looks_like_rejected_parameter(exc: Exception) -> bool:
    """Hat der Endpunkt `response_format` abgelehnt -- oder ist etwas anderes kaputt?

    Bewusst eng gefasst. Ein Abstieg bei *jedem* Fehler wuerde einen
    Netzwerkausfall oder einen falschen Schluessel in einen zweiten,
    genauso aussichtslosen Aufruf verwandeln und die eigentliche Ursache hinter
    der Folgemeldung verstecken.

    Die Wortliste ist am echten Fehlerbild geeicht: der gehostete Endpunkt
    antwortet auf ein unbekanntes Feld mit `unknown field 'X', expected one of
    ...` und HTTP 400.
    """
    text = f"{exc}".lower()
    if "response_format" in text or "json_schema" in text:
        return True
    unbekannt = ("unknown" in text or "unrecognized" in text or "unsupported" in text)
    return unbekannt and ("field" in text or "parameter" in text or "body" in text)


# ---------------------------------------------------------------------------
# Auswahl
# ---------------------------------------------------------------------------

PROVIDERS = {
    "anthropic": AnthropicProvider,
    "nim": NimProvider,
}

DEFAULT_PROVIDER = "anthropic"

# Woran ein Modellname erkennbar zu einem Anbieter gehoert. Nur die
# eindeutigen Praefixe -- die Liste soll Tippfehler und Verwechslungen fangen,
# nicht die Modellauswahl bevormunden.
_MODEL_PREFIXES = {
    "anthropic": ("claude-",),
    "nim": ("nvidia/", "meta/", "mistralai/", "qwen/", "deepseek-ai/"),
}


def get_provider(name: str, **kwargs: Any) -> LLMProvider:
    """Provider nach Namen bauen."""
    try:
        cls = PROVIDERS[name]
    except KeyError:
        raise LLMUnavailable(
            f"Unbekannter Anbieter {name!r}. Verfuegbar: {sorted(PROVIDERS)}"
        ) from None
    return cls(**kwargs)


def resolve_model(provider: str, model: str | None) -> str:
    """Modellnamen aufloesen und offensichtliche Fehlpaarungen abfangen.

    Ohne diese Pruefung endet `--provider nim` ohne `--model` beim
    Anthropic-Default: der Lauf laedt Daten, baut Briefings und scheitert erst
    am ersten bezahlten Aufruf an einem 404 -- oder, schlimmer, an einem
    Endpunkt, der den unbekannten Namen irgendwie annimmt.

    Umgekehrt genauso: ein `nvidia/...`-Modell gegen Anthropic ist ein
    Tippfehler, kein Wunsch.
    """
    if provider not in PROVIDERS:
        raise LLMUnavailable(
            f"Unbekannter Anbieter {provider!r}. Verfuegbar: {sorted(PROVIDERS)}"
        )
    if model is None:
        return PROVIDERS[provider].default_model

    for other, prefixes in _MODEL_PREFIXES.items():
        if other == provider:
            continue
        if any(model.startswith(p) for p in prefixes):
            raise LLMUnavailable(
                f"Modell {model!r} gehoert zu {other!r}, angefragt ist aber "
                f"{provider!r}. Entweder `--provider {other}` setzen oder ein "
                f"Modell von {provider!r} waehlen "
                f"(Default: {PROVIDERS[provider].default_model})."
            )
    return model
