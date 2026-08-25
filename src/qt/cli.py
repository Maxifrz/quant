"""Kommandozeile.

Jede Phase der Roadmap bekommt hier genau einen Befehl, der sie vorfuehrt.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    help="qt -- Krypto-Quant-System", no_args_is_help=True, add_completion=False
)
data_app = typer.Typer(help="Marktdaten ziehen und pruefen", no_args_is_help=True)
app.add_typer(data_app, name="data")

DEFAULT_SYMBOLS = "BTC/USD,ETH/USD"
DEFAULT_TIMEFRAMES = "1h,4h,1d"

# Default-LLM-Modell fuer die Kommandozeile. Bewusst ein Literal und kein
# Import von `qt.core.config.DEFAULT_LLM_MODEL`: der Import zieht pydantic
# nach und kostet jeden `qt`-Aufruf rund 100 ms, auch die, die nie ein Modell
# anfassen. Dass das Literal nicht davonlaeuft, sichert ein Test ab
# (tests/test_cli_effort.py) -- die Warnung in `qt.core.config` gilt.
DEFAULT_MODEL = "claude-opus-5"

# Effort-Stufen, die `output_config={"effort": ...}` annimmt. Quelle ist die
# installierte Bibliothek, nicht das Gedaechtnis: `anthropic.types.
# output_config_param.OutputConfigParam` deklariert das Feld als
# Literal["low", "medium", "high", "xhigh", "max"]. Der Test haelt die Liste
# gegen die Bibliothek, damit ein SDK-Update hier nicht stillschweigend
# vorbeigeht.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# `medium` als Default, nicht `high`: der Effort ist der groesste einzelne
# Hebel auf Laufzeit und Ausgabe-Token, und ein Backtest ruft das Modell
# hundertfach. Wer eine schwere Frage stellt, hebt ihn fuer diesen Lauf.
DEFAULT_EFFORT = "medium"


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _effort(value: str) -> str:
    """Effort-Stufe pruefen, solange der Lauf noch nichts gekostet hat.

    Ungeprueft faellt ein Tippfehler erst beim ersten echten API-Aufruf auf --
    im `alloc`-Pfad also nach Minuten Datenaufbereitung, im `--stub`-Pfad
    ueberhaupt nicht. Ein Wert, den erst die Gegenseite ablehnt, ist an der
    Kommandozeile kein Wert.
    """
    if value not in EFFORT_LEVELS:
        raise typer.BadParameter(
            f"Unbekannte Effort-Stufe {value!r}. Verfuegbar: {list(EFFORT_LEVELS)}"
        )
    return value


def _fill_model(name: str):
    """Fill-Modell nach Namen. Siehe ADR-015 zur Wahl des Defaults."""
    from qt.backtest.costs import FlatFillModel, SizeAwareFillModel

    models = {"flat": FlatFillModel, "size_aware": SizeAwareFillModel}
    try:
        return models[name]()
    except KeyError:
        raise typer.BadParameter(
            f"Unbekanntes Fill-Modell {name!r}. Verfuegbar: {sorted(models)}"
        ) from None


@data_app.command("pull")
def data_pull(
    symbols: Annotated[str, typer.Option(help="Kommagetrennt")] = DEFAULT_SYMBOLS,
    tf: Annotated[str, typer.Option(help="Timeframes, kommagetrennt")] = DEFAULT_TIMEFRAMES,
    since: Annotated[str, typer.Option(help="Startdatum YYYY-MM-DD")] = "2019-01-01",
    until: Annotated[str | None, typer.Option(help="Enddatum YYYY-MM-DD")] = None,
) -> None:
    """OHLCV von der Exchange holen und in den Parquet-Store schreiben."""
    from qt.data.ingest import pull

    start = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(until).replace(tzinfo=timezone.utc) if until else None

    typer.echo(f"Ziehe {symbols} @ {tf} ab {since} ...")
    written = pull(_split(symbols), _split(tf), start, end)

    if not written:
        typer.echo("Nichts geschrieben -- Symbol oder Zeitraum pruefen.")
        raise typer.Exit(code=1)
    for (symbol, timeframe), n in sorted(written.items()):
        typer.echo(f"  {symbol:>10} {timeframe:>3}  {n:>7,} Bars")
    typer.echo("\nJetzt pruefen: qt data report")


@data_app.command("report")
def data_report() -> None:
    """Bestand und Integritaet aller gespeicherten Daten anzeigen."""
    from qt.data.integrity import check
    from qt.data.store import available, read_bars

    entries = available()
    if not entries:
        typer.echo("Store ist leer. Erst ziehen: qt data pull")
        raise typer.Exit(code=1)

    typer.echo(f"{'':3}{'Symbol':>10} {'TF':>3}  {'Bars':>12}  Zeitraum / Abdeckung / Luecken")
    typer.echo("-" * 100)
    problems = 0
    for symbol, timeframe in entries:
        report = check(symbol, timeframe, read_bars(symbol, timeframe))
        typer.echo(report.summary())
        if not report.ok:
            problems += 1
        for gap in report.gaps[:3]:
            typer.echo(f"      Luecke: {gap}")
        if len(report.gaps) > 3:
            typer.echo(f"      ... und {len(report.gaps) - 3} weitere")

    if problems:
        typer.echo(f"\n{problems} Datensatz/Datensaetze mit kaputten Bars (!!).")
        raise typer.Exit(code=1)


@app.command("backtest")
def backtest(
    strategy: Annotated[str, typer.Option(help="Strategiename, siehe qt strategies")] = "trend",
    symbol: Annotated[str, typer.Option(help="Kommagetrennt")] = "BTC/USD",
    tf: Annotated[str, typer.Option(help="Timeframe")] = "4h",
    since: Annotated[str | None, typer.Option()] = None,
    until: Annotated[str | None, typer.Option()] = None,
    cash: Annotated[float, typer.Option(help="Startkapital")] = 100_000.0,
    fills: Annotated[str, typer.Option(help="Fill-Modell: flat oder size_aware")] = "flat",
    out: Annotated[Path | None, typer.Option(help="Pfad fuer das Tearsheet-PNG")] = None,
) -> None:
    """Eine Strategie ueber gespeicherte Daten laufen lassen."""
    from qt.backtest.engine import run_backtest
    from qt.core.config import BacktestConfig
    from qt.data.store import read_bars, to_bars
    from qt.report.tearsheet import print_summary, render
    from qt.strategy.registry import get, load_library

    load_library()
    symbols = _split(symbol)
    strategy_obj = get(strategy)(symbols, tf)

    bars = {}
    for sym in symbols:
        df = read_bars(sym, tf, start=since, end=until)
        if len(df) <= strategy_obj.warmup_bars:
            typer.echo(
                f"{sym} {tf}: nur {len(df)} Bars, "
                f"{strategy_obj.warmup_bars} sind Warmup. Zeitraum erweitern."
            )
            raise typer.Exit(code=1)
        bars[sym] = to_bars(sym, tf, df)

    result = run_backtest(
        strategy_obj, bars, BacktestConfig(initial_cash=cash), _fill_model(fills)
    )
    print_summary(result)
    path = render(result, out)
    typer.echo(f"\nTearsheet: {path}")


@app.command("wf")
def walkforward(
    strategy: Annotated[str, typer.Option(help="Strategiename")] = "trend",
    symbol: Annotated[str, typer.Option(help="Kommagetrennt")] = "BTC/USD",
    tf: Annotated[str, typer.Option(help="Timeframe")] = "4h",
    train: Annotated[int, typer.Option(help="Train-Fenster in Bars")] = 2000,
    test: Annotated[int, typer.Option(help="Test-Fenster in Bars")] = 500,
    step: Annotated[int | None, typer.Option(help="Schrittweite, Default = test")] = None,
    embargo: Annotated[int, typer.Option(help="Embargo-Bars zwischen Train und Test")] = 0,
    since: Annotated[str | None, typer.Option()] = None,
    until: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Walk-Forward laufen lassen -- die einzigen belastbaren Zahlen im System.

    Alles, was `qt backtest` ausgibt, ist In-Sample und damit optimistisch.
    Erst getrennte Out-of-Sample-Fenster sagen etwas ueber die Zukunft.
    """
    from qt.backtest.walkforward import InsufficientDataError, walk_forward
    from qt.data.store import read_bars, to_bars
    from qt.strategy.registry import get, load_library

    load_library()
    symbols = _split(symbol)
    cls = get(strategy)

    bars = {
        sym: to_bars(sym, tf, read_bars(sym, tf, start=since, end=until))
        for sym in symbols
    }

    try:
        result = walk_forward(
            lambda: cls(symbols, tf),
            bars,
            train_bars=train,
            test_bars=test,
            step_bars=step,
            embargo_bars=embargo,
        )
    except InsufficientDataError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from None

    typer.echo(f"\n{result.strategy}  |  {', '.join(result.symbols)}  |  {result.timeframe}")
    typer.echo(
        f"Train {result.train_bars} / Test {result.test_bars} / "
        f"Step {result.step_bars} / Embargo {result.embargo_bars} Bars"
    )
    typer.echo("=" * 78)

    frame = result.window_frame()
    typer.echo(
        f"  {'#':>2} {'Test-Beginn':<12} {'Rendite':>10} {'Sharpe':>8} "
        f"{'MaxDD':>9} {'Trades':>7}"
    )
    for row in frame.itertuples(index=False):
        typer.echo(
            f"  {row.window:>2} {row.test_start:%Y-%m-%d}   {row._3:>9.2%} "
            f"{row.sharpe:>8.2f} {row.max_dd:>9.2%} {row.trades:>7,}"
        )

    typer.echo("-" * 78)
    typer.echo("  Verkettete Out-of-Sample-Kurve:")
    typer.echo(result.metrics.table())

    positive = int((frame["sharpe"] > 0).sum())
    typer.echo(
        f"\n  Fenster mit positivem Sharpe: {positive} von {result.n_windows}"
    )
    typer.echo(
        "  Streuung ueber die Fenster sagt mehr als der Gesamtwert -- eine "
        "Strategie,\n  die in einem Fenster alles verdient, ist eine Stichprobe "
        "und keine Kante."
    )


@app.command("portfolio")
def portfolio(
    strategies: Annotated[str, typer.Option(help="Strategienamen, kommagetrennt")] = "trend,meanrev",
    symbols: Annotated[str, typer.Option(help="Kommagetrennt")] = "BTC/USD,ETH/USD",
    tf: Annotated[str, typer.Option(help="Timeframe")] = "4h",
    allocator: Annotated[str, typer.Option(help="Siehe qt allocators")] = "equal_weight",
    since: Annotated[str | None, typer.Option()] = None,
    until: Annotated[str | None, typer.Option()] = None,
    cash: Annotated[float, typer.Option(help="Startkapital")] = 100_000.0,
    allocate_every: Annotated[int, typer.Option(help="Allokations-Takt in Bars")] = 1,
    fills: Annotated[str, typer.Option(help="Fill-Modell: flat oder size_aware")] = "flat",
    no_risk: Annotated[bool, typer.Option("--no-risk", help="Risk-Engine abschalten")] = False,
    out: Annotated[Path | None, typer.Option(help="Pfad fuer das Tearsheet-PNG")] = None,
) -> None:
    """Mehrere Strategien unter einem Allokator laufen lassen."""
    from qt.backtest.portfolio_engine import run_portfolio_backtest
    from qt.core.config import BacktestConfig
    from qt.data.store import read_bars, to_bars
    from qt.portfolio import baselines
    from qt.report.tearsheet import print_summary, render
    from qt.strategy.registry import get as get_strategy
    from qt.strategy.registry import load_library

    load_library()
    symbol_list = _split(symbols)
    strategy_map = {
        name: get_strategy(name)(symbol_list, tf) for name in _split(strategies)
    }

    bars = {}
    for sym in symbol_list:
        df = read_bars(sym, tf, start=since, end=until)
        bars[(sym, tf)] = to_bars(sym, tf, df)

    risk = None if no_risk else _default_risk()
    result = run_portfolio_backtest(
        strategy_map,
        bars,
        baselines.get(allocator)(),
        risk=risk,
        cfg=BacktestConfig(initial_cash=cash),
        allocate_every=allocate_every,
        fill_model=_fill_model(fills),
    )

    print_summary(result)
    if result.risk_events:
        typer.echo(f"\n  Risikoeingriffe: {len(result.risk_events)}")
        for _, reason in result.risk_events[:3]:
            typer.echo(f"    {reason}")
    typer.echo(f"\nTearsheet: {render(result, out)}")


def _default_risk():
    """Risk-Engine mit Standardgrenzen, falls das Modul schon existiert."""
    try:
        from qt.portfolio.risk import RiskEngine
    except ImportError:
        return None
    return RiskEngine()


@app.command("alloc")
def alloc(
    strategies: Annotated[str, typer.Option(help="Strategienamen, kommagetrennt")] = "trend,meanrev",
    symbols: Annotated[str, typer.Option(help="Kommagetrennt")] = "BTC/USD,ETH/USD",
    tf: Annotated[str, typer.Option(help="Timeframe")] = "4h",
    candidate: Annotated[str, typer.Option(help="Zu pruefender Allokator: llm, oder ein Baseline-Name")] = "llm",
    compare_baselines: Annotated[
        bool, typer.Option("--compare-baselines", help="Gate-Lauf gegen die Baselines")
    ] = False,
    train: Annotated[int, typer.Option(help="Train-Fenster in Bars")] = 2000,
    test: Annotated[int, typer.Option(help="Test-Fenster in Bars")] = 500,
    embargo: Annotated[int, typer.Option(help="Embargo-Bars")] = 24,
    allocate_every: Annotated[int, typer.Option(help="Allokations-Takt in Bars")] = 24,
    model: Annotated[str, typer.Option(help="LLM-Modell")] = DEFAULT_MODEL,
    effort: Annotated[
        str,
        typer.Option(
            help="Denk-Aufwand des Modells: low, medium, high, xhigh oder max. "
            "Groesster Hebel auf Laufzeit und Ausgabe-Token.",
            callback=_effort,
        ),
    ] = DEFAULT_EFFORT,
    stub: Annotated[bool, typer.Option("--stub", help="Ohne API-Zugang gegen den Stub laufen")] = False,
    since: Annotated[str | None, typer.Option()] = None,
    until: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Das Gate aus ADR-004: schlaegt der Allokator die Baselines out-of-sample?

    Ein Nein ist ein Ergebnis, kein Fehler. Ein Allokator, der Vol-Parity nicht
    schlaegt, gehoert nicht in den Kreislauf.
    """
    from qt.data.store import read_bars, to_bars
    from qt.llm.cache import LLMCache
    from qt.llm.client import AllocatorClient, StubClient
    from qt.portfolio import baselines
    from qt.portfolio.gate import run_gate
    from qt.portfolio.llm_allocator import LLMAllocator
    from qt.portfolio.risk import RiskEngine
    from qt.strategy.registry import get as get_strategy
    from qt.strategy.registry import load_library

    if not compare_baselines:
        typer.echo(
            "Ohne --compare-baselines gibt es nichts zu entscheiden.\n"
            "Der Sinn dieses Befehls ist der Vergleich (ADR-004)."
        )
        raise typer.Exit(code=1)

    load_library()
    symbol_list = _split(symbols)
    strategy_names = _split(strategies)

    bars = {
        (sym, tf): to_bars(sym, tf, read_bars(sym, tf, start=since, end=until))
        for sym in symbol_list
    }

    def make_strategies():
        return {name: get_strategy(name)(symbol_list, tf) for name in strategy_names}

    allocator_telemetry = {}

    def make_candidate():
        if candidate != "llm":
            return baselines.get(candidate)()
        client = (
            StubClient()
            if stub
            else AllocatorClient(model=model, cache=LLMCache(model=model), effort=effort)
        )
        instance = LLMAllocator(client=client)
        allocator_telemetry["last"] = instance.telemetry
        return instance

    if candidate == "llm" and stub:
        typer.echo(
            "Hinweis: --stub gleichgewichtet und ist damit per Konstruktion "
            "identisch zur Equal-Weight-Baseline.\n"
            "Der Lauf prueft die Verdrahtung, nicht das Modell (ADR-019).\n"
        )

    result = run_gate(
        candidate=make_candidate,
        strategies=make_strategies,
        bars=bars,
        train_bars=train,
        test_bars=test,
        embargo_bars=embargo,
        risk=RiskEngine,
        allocate_every=allocate_every,
        candidate_name=candidate,
    )

    typer.echo(result.table())
    typer.echo()
    typer.echo(result.verdict())

    telemetry = allocator_telemetry.get("last")
    if telemetry is not None and telemetry.calls:
        typer.echo(f"\n  {telemetry.summary()}")
        if telemetry.fallback_rate > 0:
            typer.echo(
                "  Achtung: eine Rueckfallquote ueber null heisst, der Allokator "
                "hat teilweise\n  gleichgewichtet -- insoweit ist er heimlich eine "
                "Baseline (ADR-018)."
            )

    raise typer.Exit(code=0 if result.passed() else 2)


@app.command("sim")
def sim(
    symbol: Annotated[str, typer.Option(help="Ein Symbol")] = "BTC/USD",
    tf: Annotated[str, typer.Option(help="Timeframe")] = "4h",
    paths: Annotated[int, typer.Option(help="Anzahl simulierter Pfade")] = 10_000,
    horizon: Annotated[int, typer.Option(help="Horizont in Bars")] = 180,
    generator: Annotated[
        str, typer.Option(help="stationary_bootstrap, iid_bootstrap, garch oder hmm")
    ] = "stationary_bootstrap",
    cvar_limit: Annotated[
        float, typer.Option(help="CVaR-Grenze, negativ (z.B. -0.20 fuer -20%)")
    ] = -0.20,
    seed: Annotated[int, typer.Option(help="Zufallssaat -- gleicher Seed, gleiches Ergebnis")] = 0,
    scenarios: Annotated[
        bool, typer.Option("--scenarios", help="LLM Szenario-Priors setzen lassen")
    ] = False,
    stub: Annotated[bool, typer.Option("--stub", help="Ohne API-Zugang gegen den Stub")] = False,
    model: Annotated[str, typer.Option(help="LLM-Modell fuer die Szenario-Priors")] = DEFAULT_MODEL,
    effort: Annotated[
        str,
        typer.Option(
            help="Denk-Aufwand des Modells: low, medium, high, xhigh oder max. "
            "Groesster Hebel auf Laufzeit und Ausgabe-Token.",
            callback=_effort,
        ),
    ] = DEFAULT_EFFORT,
    lookback: Annotated[int, typer.Option(help="Bars Historie fuer den Fit")] = 2000,
    since: Annotated[str | None, typer.Option()] = None,
    until: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Pfad-Ensemble simulieren und die Allokation dazu bestimmen.

    Beantwortet nicht "wo steht der Kurs", sondern "welches Exposure haelt
    die Verlustgrenze ein und liefert dabei typischerweise am meisten".
    """
    import numpy as np

    from qt.data.store import read_bars
    from qt.sim.objective import optimise_allocation

    df = read_bars(symbol, tf, start=since, end=until)
    closes = df["close"].to_numpy()
    if len(closes) < lookback + 2:
        typer.echo(
            f"Zu wenig Historie: {len(closes)} Bars, {lookback + 2} noetig. "
            "Zeitraum erweitern oder --lookback senken."
        )
        raise typer.Exit(code=1)

    returns = np.diff(closes[-(lookback + 1) :]) / closes[-(lookback + 1) : -1]

    gen = _path_generator(generator)
    typer.echo(f"Simuliere {paths:,} Pfade x {horizon} Bars mit {gen.describe()} ...")
    try:
        ensemble = gen.generate(returns, horizon=horizon, n_paths=paths, seed=seed)
    except Exception as exc:
        typer.echo(f"Simulation fehlgeschlagen ({type(exc).__name__}): {exc}")
        raise typer.Exit(code=1) from None

    typer.echo(f"  {ensemble.describe()}")

    if scenarios:
        ensemble = _apply_scenarios(ensemble, returns, tf, stub, model=model, effort=effort)

    result = optimise_allocation(ensemble, cvar_limit=cvar_limit)

    typer.echo("\n  Exposure   Median    CVaR    P(Verlust)  zulaessig")
    typer.echo("  " + "-" * 52)
    for score in result.scores:
        mark = " <" if score.exposure == result.exposure else ""
        ok = "ja " if score.feasible else "NEIN"
        typer.echo(
            f"  {score.exposure:>7.0%}  {score.median_return:>8.2%} "
            f"{score.cvar:>8.2%}  {score.prob_loss:>9.1%}  {ok:>8}{mark}"
        )

    typer.echo(f"\n  CVaR-Grenze: {result.cvar_limit:.0%}")
    typer.echo(f"  Ergebnis: {result.describe()}")
    typer.echo(
        "\n  Gehandelt wird die Verteilung, nicht ein Pfad: das Ergebnis ist das\n"
        "  Exposure, das ueber alle simulierten Zukuenfte hinweg die Verlustgrenze\n"
        "  haelt -- keine Prognose, wo der Kurs stehen wird."
    )


def _path_generator(name: str):
    """Generator nach Namen. Import erst hier, damit `qt --help` schnell bleibt."""
    from qt.sim.bootstrap import IIDBootstrap, StationaryBootstrap

    builders = {
        "stationary_bootstrap": StationaryBootstrap,
        "iid_bootstrap": IIDBootstrap,
    }
    try:
        from qt.sim.regimes import GARCHPaths, HMMRegimePaths

        builders["garch"] = GARCHPaths
        builders["hmm"] = HMMRegimePaths
    except ImportError:
        pass

    try:
        return builders[name]()
    except KeyError:
        raise typer.BadParameter(
            f"Unbekannter Generator {name!r}. Verfuegbar: {sorted(builders)}"
        ) from None


def _apply_scenarios(
    ensemble,
    returns,
    tf: str,
    stub: bool,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
):
    """LLM Szenario-Priors setzen lassen und das Ensemble umgewichten.

    Faellt bei jedem Problem auf das unveraenderte Ensemble zurueck -- ein
    Ausfall des Modells darf die Simulation nicht wertlos machen, er macht
    sie nur meinungslos.
    """
    from qt.llm.briefing import build_scenario_briefing
    from qt.llm.cache import LLMCache
    from qt.llm.client import LLMUnavailable, ScenarioClient, StubScenarioClient
    from qt.sim.scenarios import apply_priors, from_proposal

    # Modell an Client **und** Cache, obwohl der Cache einen eigenen Default
    # kennt: beide Defaults stammen heute aus `qt.core.config`, aber das ist
    # eine Eigenschaft, die niemand erzwingt. Hier stand vorher `LLMCache()`
    # ohne Modell -- gutgegangen ist das nur, weil der Client sein Modell
    # zusaetzlich in den Key schreibt. Explizit gesetzt haengt der Key nicht
    # mehr davon ab, dass zwei Defaults zufaellig gleich bleiben.
    client = (
        StubScenarioClient()
        if stub
        else ScenarioClient(model=model, cache=LLMCache(model=model), effort=effort)
    )
    briefing = build_scenario_briefing(returns, ensemble, tf)

    try:
        proposal = client.propose(briefing)
    except LLMUnavailable as exc:
        typer.echo(f"\n  Szenario-Priors uebersprungen: {exc}")
        return ensemble

    priors, translation = from_proposal(proposal)
    typer.echo(f"\n  Szenario: {proposal.regime or 'ohne Einordnung'}")
    typer.echo(f"  {translation.summary()}")
    if proposal.reasoning:
        typer.echo(f"  {proposal.reasoning[:200]}")

    tilted, tilt = apply_priors(ensemble, priors)
    typer.echo(f"  {tilt.summary()}")
    if priors:
        typer.echo(f"  umgewichtet: {tilted.describe()}")
    return tilted


@app.command("allocators")
def list_allocators() -> None:
    """Verfuegbare Allokatoren anzeigen."""
    from qt.portfolio import baselines

    for name in baselines.names():
        cls = baselines.get(name)
        doc = (cls.__doc__ or "").strip().splitlines()[0] if cls.__doc__ else ""
        typer.echo(f"  {name:<14} {doc}")


@app.command("strategies")
def list_strategies() -> None:
    """Registrierte Strategien anzeigen."""
    from qt.strategy.registry import get, load_library, names

    load_library()
    for name in names():
        cls = get(name)
        doc = (cls.__doc__ or "").strip().splitlines()[0] if cls.__doc__ else ""
        typer.echo(f"  {name:<12} {doc}")


if __name__ == "__main__":
    app()
