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
paper_app = typer.Typer(help="Paper-Konto: sicher wiederholbare Ticks", no_args_is_help=True)
app.add_typer(paper_app, name="paper")
placebo_app = typer.Typer(
    help="Negativkontrollen: schlaegt eine Strategie ihre gewuerfelte Fassung?",
    no_args_is_help=True,
)
app.add_typer(placebo_app, name="placebo")

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

# Die Anbieter, aus denen `--provider` waehlen darf. Wie `DEFAULT_MODEL` ein
# Literal und kein Import: `qt.llm.providers` zieht pydantic nach. Dass die
# Liste nicht davonlaeuft, sichert ein Test ab (tests/test_llm_providers.py).
PROVIDER_NAMES = ("anthropic", "nim")

# Anthropic bleibt Default. Jede bisher gemessene Zahl, jeder ADR und jeder
# Cache-Eintrag haengt daran; ein zweiter Anbieter ist eine Option und kein
# Umzug.
DEFAULT_PROVIDER = "anthropic"


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


def _provider(value: str) -> str:
    """Anbietername pruefen, solange der Lauf noch nichts gekostet hat.

    Gleicher Grund wie bei `_effort`: ein Tippfehler soll beim Aufruf
    auffallen und nicht nach Minuten Datenaufbereitung am ersten API-Aufruf.
    """
    if value not in PROVIDER_NAMES:
        raise typer.BadParameter(
            f"Unbekannter Anbieter {value!r}. Verfuegbar: {list(PROVIDER_NAMES)}"
        )
    return value


def _resolve_model(provider: str, model: str | None) -> str:
    """Modellnamen aufloesen -- Default des Anbieters, oder Fehlpaarung melden.

    Muss **vor** dem Datenladen laufen. Sonst endet `--provider nim` ohne
    `--model` beim Anthropic-Default und faellt erst am ersten bezahlten
    Aufruf auf.
    """
    from qt.llm.providers import LLMUnavailable, resolve_model

    try:
        return resolve_model(provider, model)
    except LLMUnavailable as exc:
        raise typer.BadParameter(str(exc)) from None


def _build_provider(name: str):
    from qt.llm.providers import get_provider

    return get_provider(name)


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


def _regime(name: str):
    """Kostenregime nach Namen. Die Namen und ihre Quellen: ADR-056."""
    from qt.core.config import regime

    try:
        return regime(name)
    except KeyError as exc:
        raise typer.BadParameter(str(exc).strip("\"'")) from None


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

    # Ein Symbol, das nichts geliefert hat, wurde bis ADR-059 einfach nicht
    # gedruckt. Neun Zeilen sehen aber genauso vollstaendig aus wie vierzehn,
    # solange niemand nachzaehlt -- und der Store ist die Grundlage jeder
    # Zahl in diesem Projekt.
    leer = sorted(
        f"{sym} {tfr}"
        for sym in _split(symbols)
        for tfr in _split(tf)
        if (sym, tfr) not in written
    )
    if leer:
        typer.echo(f"\n  !! ohne Bars: {', '.join(leer)}")

    typer.echo("\nJetzt pruefen: qt data report")


@data_app.command("resample")
def data_resample(
    symbols: Annotated[str, typer.Option(help="Kommagetrennt")] = DEFAULT_SYMBOLS,
    quelle: Annotated[str, typer.Option("--from", help="Quell-Timeframe")] = "1d",
    ziel: Annotated[str, typer.Option("--to", help="Ziel-Timeframes, kommagetrennt")] = "2d,3d,1w",
) -> None:
    """Groebere Bars aus feineren ableiten (fuer Timeframes ueber 1d).

    Die erzeugten Dateien liegen im selben Store wie gezogene Daten und sind
    danach nicht mehr als abgeleitet erkennbar. Das ist Absicht -- jede
    nachgelagerte Stufe soll sie gleich behandeln -- aber es heisst auch:
    ein spaeterer `qt data pull` mit demselben Timeframe ueberschreibt sie.
    """
    from qt.data.ingest import resample_store

    typer.echo(f"Leite {ziel} aus {quelle} ab fuer {symbols} ...")
    written = resample_store(_split(symbols), quelle, _split(ziel))
    for (symbol, timeframe), n in sorted(written.items()):
        typer.echo(f"  {symbol:>10} {timeframe:>3}  {n:>7,} Bars")


@data_app.command("onchain")
def data_onchain(
    series: Annotated[str, typer.Option(help="Reihen, kommagetrennt")] = "hash-rate",
    since: Annotated[str, typer.Option(help="Startdatum YYYY-MM-DD")] = "2019-01-01",
) -> None:
    """On-Chain-Reihen von blockchain.info ziehen.

    Die einzige Datenquelle im Projekt, die etwas liefert, das nur Bitcoin
    haben kann: Miner-Oekonomie. Ethereum ist Proof-of-Stake, dort gibt es
    keine Hashrate (ADR-048).
    """
    from qt.data.onchain import SERIES, describe, pull

    start = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
    for name in _split(series):
        if name not in SERIES:
            raise typer.BadParameter(
                f"Unbekannte Reihe {name!r}. Bekannt: {', '.join(sorted(SERIES))}"
            )

    for name in _split(series):
        typer.echo(f"Ziehe {name} ab {since} ...")
        n = pull(name, start)
        typer.echo(f"  {n:,} Punkte gespeichert")

    typer.echo("")
    for name in _split(series):
        typer.echo(describe(name))


@data_app.command("report")
def data_report() -> None:
    """Bestand und Integritaet aller gespeicherten Daten anzeigen."""
    from qt.data.integrity import check
    from qt.data.store import available, read_bars
    from qt.data.tiingo import calendar_of

    entries = available()
    if not entries:
        typer.echo("Store ist leer. Erst ziehen: qt data pull")
        raise typer.Exit(code=1)

    typer.echo(f"{'':3}{'Symbol':>10} {'TF':>3}  {'Bars':>12}  Zeitraum / Abdeckung / Luecken")
    typer.echo("-" * 100)
    problems = 0
    for symbol, timeframe in entries:
        report = check(
            symbol,
            timeframe,
            read_bars(symbol, timeframe),
            calendar=calendar_of(symbol, timeframe),
        )
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
    costs: Annotated[
        str, typer.Option(help="Kostenregime, siehe qt costs --help")
    ] = "coinbase_taker",
    out: Annotated[Path | None, typer.Option(help="Pfad fuer das Tearsheet-PNG")] = None,
) -> None:
    """Eine Strategie ueber gespeicherte Daten laufen lassen."""
    from qt.backtest.engine import run_backtest
    from qt.core.config import BacktestConfig
    from qt.data.store import read_bars, to_bars
    from qt.backtest.costs import round_trip_bps
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

    cfg = BacktestConfig(initial_cash=cash, costs=_regime(costs))
    result = run_backtest(strategy_obj, bars, cfg, _fill_model(fills))
    typer.echo(f"Kostenregime: {costs} ({round_trip_bps(cfg.costs):.1f}bps Round-Trip)")
    print_summary(result)
    path = render(result, out)
    typer.echo(f"\nTearsheet: {path}")


@app.command("trades")
def trades(
    strategy: Annotated[str, typer.Option(help="Strategiename")] = "macross",
    symbol: Annotated[str, typer.Option(help="Kommagetrennt")] = "BTC/USD",
    tf: Annotated[str, typer.Option(help="Timeframe")] = "1d",
    limit: Annotated[int, typer.Option(help="Wieviele Trades einzeln zeigen")] = 15,
    since: Annotated[str | None, typer.Option()] = None,
    until: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Round-Trips eines Laufs: was wurde wirklich gehandelt.

    Beantwortet die Frage, die weder Equity-Kurve noch `Metrics.hit_rate`
    beantworten: kam ein Verlust aus wenigen grossen Fehlgriffen oder aus
    vielen kleinen Gebuehrenverlusten. `hit_rate` zaehlt Bars, nicht Trades.

    Bewusst **ohne Interpretation** (ADR-049): hier steht, was passiert ist,
    nicht warum. Aus Gewinnern und Verlierern Regeln abzuleiten waere
    ueberwachtes Lernen auf denselben Daten, und zwar an der Deflated Sharpe
    Ratio vorbei.
    """
    from qt.backtest.engine import run_backtest
    from qt.backtest.roundtrips import round_trips, summary_table
    from qt.data.store import read_bars, to_bars
    from qt.strategy.registry import get, load_library

    load_library()
    symbols = _split(symbol)
    strategy_obj = get(strategy)(symbols, tf)
    bars = {
        sym: to_bars(sym, tf, read_bars(sym, tf, start=since, end=until))
        for sym in symbols
    }

    result = run_backtest(strategy_obj, bars)
    trades_list = round_trips(result)

    typer.echo(f"\n{strategy}  |  {', '.join(symbols)}  |  {tf}")
    typer.echo(f"{len(result.fills)} Fills -> {len(trades_list)} Round-Trips\n")

    if trades_list:
        typer.echo(
            f"{'#':>3} {'Einstieg':>12} {'Ri':>3} {'Bars':>5} "
            f"{'Rendite':>9} {'Kosten':>8} {'MAE':>8} {'MFE':>8}"
        )
        typer.echo("-" * 62)
        for i, t in enumerate(trades_list[-limit:], start=max(1, len(trades_list) - limit + 1)):
            typer.echo(
                f"{i:>3} {t.entry_ts.date()!s:>12} {'L' if t.direction > 0 else 'S':>3} "
                f"{t.bars_held:>5} {t.return_pct:>8.1%} {t.cost_share:>7.0%} "
                f"{t.mae:>7.1%} {t.mfe:>7.1%}"
            )
        if len(trades_list) > limit:
            typer.echo(f"    ... {len(trades_list) - limit} weitere davor")
        typer.echo("")

    typer.echo(summary_table(trades_list))


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
    provider: Annotated[
        str,
        typer.Option(
            help="LLM-Anbieter: anthropic oder nim (NVIDIA).",
            callback=_provider,
        ),
    ] = DEFAULT_PROVIDER,
    model: Annotated[
        str | None,
        typer.Option(help=f"LLM-Modell. Ohne Angabe der Default des Anbieters "
                     f"(anthropic: {DEFAULT_MODEL})."),
    ] = None,
    effort: Annotated[
        str,
        typer.Option(
            help="Denk-Aufwand des Modells: low, medium, high, xhigh oder max. "
            "Groesster Hebel auf Laufzeit und Ausgabe-Token. Bei `nim` ist die "
            "Abbildung eine Naeherung -- siehe qt.llm.providers.",
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
    from qt.portfolio.llm_allocator import AllocatorTelemetry, LLMAllocator
    from qt.portfolio.risk import RiskEngine
    from qt.strategy.registry import get as get_strategy
    from qt.strategy.registry import load_library

    if not compare_baselines:
        typer.echo(
            "Ohne --compare-baselines gibt es nichts zu entscheiden.\n"
            "Der Sinn dieses Befehls ist der Vergleich (ADR-004)."
        )
        raise typer.Exit(code=1)

    # Vor dem Datenladen: eine Fehlpaarung aus Anbieter und Modell soll den
    # Lauf hier beenden und nicht nach Minuten am ersten bezahlten Aufruf.
    model = _resolve_model(provider, model)

    load_library()
    symbol_list = _split(symbols)
    strategy_names = _split(strategies)

    bars = {
        (sym, tf): to_bars(sym, tf, read_bars(sym, tf, start=since, end=until))
        for sym in symbol_list
    }

    def make_strategies():
        return {name: get_strategy(name)(symbol_list, tf) for name in strategy_names}

    # Ein Eintrag je Fenster, nicht einer fuer den Lauf: `make_candidate`
    # laeuft pro Walk-Forward-Fenster einmal. Frueher hielt hier ein
    # einzelner Slot nur das letzte Fenster fest -- die Rueckfallquote im
    # Bericht beschrieb dann 5 von 145 Aufrufen (ADR-043).
    allocator_telemetries: list[AllocatorTelemetry] = []

    def make_candidate():
        if candidate != "llm":
            return baselines.get(candidate)()
        client = (
            StubClient()
            if stub
            else AllocatorClient(
                model=model,
                cache=LLMCache(model=model),
                effort=effort,
                provider=_build_provider(provider),
            )
        )
        instance = LLMAllocator(client=client)
        allocator_telemetries.append(instance.telemetry)
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

    telemetry = AllocatorTelemetry()
    for je_fenster in allocator_telemetries:
        telemetry.merge(je_fenster)
    if telemetry.calls:
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
    provider: Annotated[
        str,
        typer.Option(
            help="LLM-Anbieter: anthropic oder nim (NVIDIA).",
            callback=_provider,
        ),
    ] = DEFAULT_PROVIDER,
    model: Annotated[
        str | None,
        typer.Option(help="LLM-Modell fuer die Szenario-Priors. Ohne Angabe der "
                     f"Default des Anbieters (anthropic: {DEFAULT_MODEL})."),
    ] = None,
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

    model = _resolve_model(provider, model)

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
        ensemble = _apply_scenarios(
            ensemble, returns, tf, stub, model=model, effort=effort, provider=provider
        )

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
    provider: str = DEFAULT_PROVIDER,
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
        else ScenarioClient(
            model=model,
            cache=LLMCache(model=model),
            effort=effort,
            provider=_build_provider(provider),
        )
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


@app.command("research")
def research(
    generate: Annotated[int, typer.Option(help="Wie viele Kandidaten erzeugen")] = 5,
    screen: Annotated[
        bool, typer.Option("--screen", help="Ueberlebende durch Walk-Forward und DSR schicken")
    ] = False,
    symbols: Annotated[str, typer.Option(help="Kommagetrennt")] = "BTC/USD",
    tf: Annotated[str, typer.Option(help="Timeframe")] = "4h",
    train: Annotated[int, typer.Option(help="Train-Fenster in Bars")] = 3000,
    test: Annotated[int, typer.Option(help="Test-Fenster in Bars")] = 800,
    embargo: Annotated[int, typer.Option(help="Embargo-Bars")] = 50,
    dsr_threshold: Annotated[
        float, typer.Option(help="Ab welcher DSR ein Kandidat besteht")
    ] = 0.95,
    provider: Annotated[
        str,
        typer.Option(
            help="LLM-Anbieter: anthropic oder nim (NVIDIA).",
            callback=_provider,
        ),
    ] = DEFAULT_PROVIDER,
    model: Annotated[
        str | None,
        typer.Option(help="LLM-Modell. Ohne Angabe der Default des Anbieters "
                     f"(anthropic: {DEFAULT_MODEL})."),
    ] = None,
    generator_effort: Annotated[
        str, typer.Option(help="Denk-Aufwand des Generators", callback=_effort)
    ] = DEFAULT_EFFORT,
    critic_effort: Annotated[
        str,
        typer.Option(
            help="Denk-Aufwand der Kritik. Default niedriger: sie ist der "
            "billige Vorfilter, nicht die Hauptarbeit.",
            callback=_effort,
        ),
    ] = "low",
    no_critic: Annotated[
        bool, typer.Option("--no-critic", help="Kritik-Stufe ueberspringen")
    ] = False,
    stub: Annotated[bool, typer.Option("--stub", help="Ohne API-Zugang gegen die Stubs")] = False,
    show: Annotated[str | None, typer.Option(help="Kandidaten-ID anzeigen (nur lesen)")] = None,
    mark_promoted: Annotated[
        str | None, typer.Option(help="Kandidaten-ID als uebernommen vermerken")
    ] = None,
    since: Annotated[str | None, typer.Option()] = None,
    until: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Der Research-Loop: LLM schreibt Kandidaten, harte Gates entscheiden.

    Die Reihenfolge ist nach Kosten sortiert: Sandbox (gratis), Kritik
    (billig), Walk-Forward samt DSR (teuer). Wer frueher ablehnt, spart alles
    Folgende.

    **Promotion bleibt Handarbeit.** Es gibt bewusst keinen Befehl, der einen
    bestandenen Kandidaten nach `strategy/library/` schreibt -- `--show`
    druckt ihn, kopieren muss ein Mensch. Eine automatische Uebernahme waere
    der schleichende Weg, die Freigabe abzuschaffen.
    """
    from qt.data.store import read_bars, to_bars
    from qt.llm.cache import LLMCache
    from qt.llm.client import (
        CriticClient,
        GeneratorClient,
        StubCriticClient,
        StubGeneratorClient,
    )
    from qt.research.loop import run_research_loop
    from qt.research.registry import STUB_PATH, ResearchRegistry

    model = _resolve_model(provider, model)
    # Ein Lauf gegen die Stubs schreibt in eine eigene Datei. Sonst hebt ein
    # Rauchtest den Versuchszaehler und macht die DSR fuer jeden echten
    # Kandidaten haerter, ohne dass jemand auf die Daten geschaut haette
    # (ADR-057).
    registry = ResearchRegistry.open(STUB_PATH if stub else None)
    if stub:
        typer.echo(f"Stub-Lauf: schreibt nach {STUB_PATH.name}, nicht in die Registry.")

    if show is not None:
        _show_candidate(registry, show)
        registry.close()
        raise typer.Exit(code=0)

    if mark_promoted is not None:
        registry.mark_promoted(mark_promoted, note="manuell freigegeben")
        typer.echo(f"Kandidat {mark_promoted} als uebernommen vermerkt.")
        registry.close()
        raise typer.Exit(code=0)

    symbol_list = _split(symbols)
    bars = {
        sym: to_bars(sym, tf, read_bars(sym, tf, start=since, end=until))
        for sym in symbol_list
    }

    if stub:
        gen_client = StubGeneratorClient()
        crit_client = StubCriticClient()
        typer.echo(
            "Hinweis: der Stub-Generator liefert bewusst schwache Kandidaten und\n"
            "der Stub-Kritiker laesst alles durch. Der Lauf prueft die\n"
            "Verdrahtung, nicht die Idee (ADR-019).\n"
        )
    else:
        # Zwei Provider-Instanzen, nicht eine geteilte: der NIM-Provider merkt
        # sich, ob der Endpunkt `guided_json` abgelehnt hat, und dieses Wissen
        # gehoert zu genau einem Aufrufpfad. Geteilt waere es ein stiller
        # Zustand zwischen Generator und Kritiker.
        gen_client = GeneratorClient(
            model=model,
            cache=LLMCache(model=model),
            effort=generator_effort,
            provider=_build_provider(provider),
        )
        crit_client = CriticClient(
            model=model,
            cache=LLMCache(model=model),
            effort=critic_effort,
            provider=_build_provider(provider),
        )

    typer.echo(f"Versuchszaehler vor diesem Lauf: {registry.trial_count()}")
    typer.echo()

    telemetry = run_research_loop(
        generate,
        bars,
        symbol_list,
        tf,
        gen_client,
        crit_client,
        registry=registry,
        train_bars=train,
        test_bars=test,
        embargo_bars=embargo,
        dsr_threshold=dsr_threshold,
        use_critic=not no_critic,
        echo=typer.echo,
    )

    typer.echo()
    typer.echo(telemetry.table())
    typer.echo()
    typer.echo(f"Versuchszaehler nach diesem Lauf: {registry.trial_count()}")

    for note in telemetry.notes:
        typer.echo(f"  Hinweis: {note}")

    if telemetry.passed:
        typer.echo(
            f"\n{telemetry.passed} Kandidat(en) haben die DSR-Schwelle bestanden.\n"
            "Ansehen mit `qt research --show <id>`, uebernehmen von Hand."
        )
        winners = registry.history(status="passed")
        for row in winners.tail(telemetry.passed).itertuples():
            typer.echo(f"  {row.id}  DSR {row.dsr:.3f}  Sharpe {row.sharpe:+.2f}")
    else:
        typer.echo(
            "\nKein Kandidat hat bestanden. Das ist der Normalfall und kein "
            "Fehler (ADR-005)."
        )

    registry.close()
    raise typer.Exit(code=0)


def _show_candidate(registry, candidate_id: str) -> None:
    """Vollen Audit-Pfad eines Kandidaten drucken. Nur lesen, nie schreiben."""
    row = registry.get(candidate_id)
    if row is None:
        typer.echo(f"Kein Kandidat mit der ID {candidate_id}.")
        raise typer.Exit(code=1)

    typer.echo(f"Kandidat {row['id']}")
    typer.echo(f"  erzeugt      {row['created_at']}")
    typer.echo(f"  Modell       {row['generator_model']} (Effort {row['generator_effort']})")
    typer.echo(f"  Klasse       {row['class_name']}")
    typer.echo(f"  Sandbox      {row['sandbox_status']}")
    if row.get("sandbox_reasons"):
        for reason in row["sandbox_reasons"]:
            typer.echo(f"               {reason}")
    typer.echo(f"  Kritik       {row['critic_recommendation']}")
    if row.get("critic_reasoning"):
        typer.echo(f"               {row['critic_reasoning']}")
    typer.echo(f"  Screening    {row['screening_status']}")
    if row.get("dsr") is not None:
        typer.echo(
            f"               DSR {row['dsr']:.3f} gegen "
            f"{row['trial_count_at_screening']} Versuche, Sharpe {row['sharpe']:+.2f}"
        )
    typer.echo(f"  uebernommen  {row['promoted']}")
    typer.echo()
    typer.echo("Begruendung:")
    typer.echo(f"  {row['rationale']}")
    typer.echo()
    typer.echo("Uebernehmen: den folgenden Block nach")
    typer.echo(f"src/qt/strategy/library/{_module_name(row)}.py kopieren.")
    typer.echo("Der Kandidat laeuft danach im Sandbox-Dialekt weiter -- Konstanten")
    typer.echo("statt Konstruktor. Wer ihn umschreibt, testet einen anderen Kandidaten")
    typer.echo("als den, der die DSR bestanden hat.")
    typer.echo()
    typer.echo(_promotable_module(row))


def _module_name(row: dict) -> str:
    """Dateiname aus dem Klassennamen: DonchianTrend -> donchian_trend."""
    name = row.get("class_name") or "kandidat"
    out = [name[0].lower()]
    for char in name[1:]:
        out.append(f"_{char.lower()}" if char.isupper() else char)
    return "".join(out)


def _promotable_module(row: dict) -> str:
    """Den Kandidaten als fertiges Bibliotheksmodul ausgeben.

    Bewusst nur **gedruckt**, nicht geschrieben: die Freigabe ist der einzige
    Punkt, an dem ein Mensch zwischen einem generierten Kandidaten und dem
    Handelssystem steht. Ein Befehl, der die Datei selbst anlegt -- auch
    hinter einer Bestaetigung -- waere der schleichende Weg, diesen Punkt
    abzuschaffen.

    Was hier trotzdem passiert: der Kopf mit Herkunft, Kennzahlen und
    Kandidaten-ID wird mitgeliefert. Eine Strategie in der Bibliothek, der man
    nicht mehr ansieht, aus welchem Lauf und gegen wie viele Versuche sie
    gemessen wurde, ist in einem halben Jahr nicht mehr einzuordnen -- und
    genau diese Zahl entscheidet, wie ernst ihr Sharpe zu nehmen ist.
    """
    dsr = row.get("dsr")
    trials = row.get("trial_count_at_screening")
    kennzahl = (
        f"DSR {dsr:.3f} gegen {trials} Versuche, Sharpe {row['sharpe']:+.2f} "
        f"ueber {row['n_windows']} Walk-Forward-Fenster ({row['oos_bars']} OOS-Bars)."
        if dsr is not None
        else "Nicht gescreent -- Kennzahlen fehlen."
    )
    kopf = (
        f'"""{row.get("rationale") or "LLM-generierter Kandidat."}\n'
        f"\n"
        f"Erzeugt vom Research-Loop, Kandidat {row['id']}.\n"
        f"{kennzahl}\n"
        f'"""\n'
        f"\n"
        f"from __future__ import annotations\n"
        f"\n"
        f"import math\n"
        f"\n"
        f"from qt.features import ta\n"
        f"from qt.strategy.base import Strategy\n"
        f"from qt.strategy.registry import register\n"
        f"\n"
        f"\n"
        f"@register\n"
    )
    return kopf + row["code"]


@data_app.command("trades")
def data_trades(
    symbols: Annotated[str, typer.Option(help="Kommagetrennt")] = "BTC/USD",
    days: Annotated[int, typer.Option(help="Wie viele Tage Historie")] = 30,
    tf: Annotated[str, typer.Option(help="Timeframe fuer den Bericht")] = "4h",
    resume: Annotated[
        bool,
        typer.Option(
            help="Bei bereits gespeicherten Daten am Ende des zusammen"
            "haengenden Blocks weitermachen statt von vorn."
        ),
    ] = True,
) -> None:
    """Einzeltrades mit Aggressor-Seite ziehen -- die Rohdaten fuer Order Flow.

    Quelle ist Kraken und nicht Coinbase: Coinbase ignoriert `since` und
    liefert immer die juengsten Trades, dort ist keine Historie beschaffbar
    (ADR-034). Der Fluss stammt damit von einer anderen Boerse als die
    Kursreihe -- eine Annahme, die man kennen muss.

    Rechnen mit rund 19 Anfragen je Tag Historie: 30 Tage sind etwa 580
    Anfragen und gut zehn Minuten.
    """
    from datetime import datetime, timedelta, timezone

    from qt.data.trades import (
        TRADES_EXCHANGE,
        coverage,
        fetch_trades,
        make_trades_exchange,
        read_trades,
        resume_point,
        write_trades,
    )
    from qt.features import orderflow as of

    exchange = make_trades_exchange()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    typer.echo(f"Quelle {TRADES_EXCHANGE}, ab {since:%Y-%m-%d %H:%M} UTC\n")

    for symbol in _split(symbols):

        def fortschritt(pages: int, n: int, message: str) -> None:
            if message:
                typer.echo(f"  {message}")
            elif pages % 25 == 0:
                typer.echo(f"  {pages} Seiten, {n:,} Trades")

        start = resume_point(symbol, since) if resume else since
        if start > since:
            typer.echo(
                f"{symbol}: setze fort ab {start:%Y-%m-%d %H:%M} "
                f"(statt {since:%Y-%m-%d %H:%M})"
            )
        else:
            typer.echo(f"{symbol}:")
        # `sink` schreibt unterwegs: ein Abzug ueber vierzig Minuten, den ein
        # Abbruch auf der vorletzten Seite erwischt, haette sonst nichts
        # geliefert. Der zurueckgegebene Rest ist nur der letzte Block.
        def ablegen(chunk):
            write_trades(symbol, chunk)

        fetch_trades(exchange, symbol, since=start, on_page=fortschritt, sink=ablegen)
        df = read_trades(symbol)
        if df.empty:
            typer.echo("  keine Trades erhalten")
            continue

        path = write_trades(symbol, df)
        info = coverage(df)
        typer.echo(f"  {info['n']:,} Trades -> {path}")
        typer.echo(f"  {info['start']:%Y-%m-%d %H:%M} bis {info['end']:%Y-%m-%d %H:%M}")
        typer.echo(f"  Kaufanteil {info['buy_share']:.1%}")

        # Die groesste Luecke ist die wichtigste Zahl: Order-Flow-Kennzahlen
        # ueber einer Luecke sind nicht ungenau, sie sind erfunden.
        luecke = info["max_gap_s"]
        typer.echo(f"  groesste Luecke {luecke:,.0f}s")
        if luecke > 3600:
            typer.echo(
                "  Achtung: eine Luecke ueber einer Stunde heisst, dass der\n"
                "  Abzug abgebrochen ist. Befehl erneut laufen lassen."
            )

        flow = of.aggregate(df, tf)
        typer.echo(f"  ergibt {len(flow)} {tf}-Bars mit Flussdaten\n")


@paper_app.command("run")
def paper_run(
    strategy: Annotated[str, typer.Option(help="Strategiename, siehe qt strategies")] = "macross",
    symbols: Annotated[str, typer.Option(help="Kommagetrennt")] = "BTC/USD",
    tf: Annotated[str, typer.Option(help="Timeframe")] = "1d",
    cash: Annotated[float, typer.Option(help="Startkapital eines frischen Kontos")] = 100_000.0,
    max_drawdown: Annotated[
        float, typer.Option(help="Kill-Switch-Schwelle, Bruchteil des Hoechststands")
    ] = 0.20,
    shape_risk: Annotated[
        bool,
        typer.Option(
            "--shape-risk",
            help="Vol-Targeting und Symbol-Cap der Risk-Engine einschalten. "
            "Aus by default, damit das Konto dieselbe Groesse handelt wie der "
            "Backtest, gegen den es verglichen wird (ADR-053).",
        ),
    ] = False,
    no_refresh: Annotated[
        bool, typer.Option("--no-refresh", help="Keine frischen Bars von der Exchange holen")
    ] = False,
) -> None:
    """Einen Tick: Konto um alle seit dem letzten Aufruf geschlossenen Bars fortschreiben.

    Sicher wiederholbar -- ein zweiter Aufruf ohne neue Bars aendert nichts.
    Ein frisches Konto startet **flach**, nicht rueckwirkend: die erste
    Ausfuehrung legt nur den Startpunkt fest, gehandelt wird erst ab dem
    naechsten Bar, der danach schliesst.

    **Die Risk-Engine formt hier per Default nicht.** Ein Paper-Konto soll ein
    gemessenes Ergebnis nachpruefen, und gemessen wurde ohne sie: `qt backtest`
    und `qt wf` rufen gar keine Risk-Engine auf. Mit den Portfolio-Defaults
    (Vol-Targeting, 25% je Symbol) handelte das Konto rund ein Viertel der
    Groesse und damit eine andere Strategie -- der Kill-Switch bei 20%
    Kontodrawdown braeuchte dann rund 80% Marktdrawdown und koennte praktisch
    nie ausloesen (ADR-053). Der **Kill-Switch bleibt** in jedem Fall an; er
    ist die Sicherung, nicht die Formung. `--shape-risk` schaltet die Formung
    dazu, wenn man den Portfolio-Pfad nachstellen will.
    """
    from qt.backtest.costs import round_trip_bps
    from qt.core.config import BacktestConfig
    from qt.live.runner import run_paper_tick
    from qt.portfolio.risk import RiskConfig
    from qt.strategy.registry import get, load_library

    load_library()
    symbol_list = _split(symbols)
    cfg = BacktestConfig(initial_cash=cash)
    risk_cfg = RiskConfig(
        max_drawdown=max_drawdown,
        vol_targeting=shape_risk,
        max_weight_per_symbol=RiskConfig().max_weight_per_symbol if shape_risk else 1.0,
    )

    formung = "an" if shape_risk else "aus (Konto handelt wie der Backtest)"
    typer.echo(
        f"Kosten je Round-Trip: {round_trip_bps(cfg.costs):.0f}bps  |  "
        f"Kill-Switch bei {max_drawdown:.0%} Drawdown vom Hoechststand  |  "
        f"Risiko-Formung {formung}\n"
    )

    try:
        report = run_paper_tick(
            lambda: get(strategy)(symbol_list, tf),
            symbol_list,
            tf,
            cfg=cfg,
            risk_cfg=risk_cfg,
            refresh=not no_refresh,
        )
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from None

    typer.echo(report.summary())
    for fill in report.new_fills:
        typer.echo(
            f"  Fill {fill.symbol} qty={fill.qty:+.6f} price={fill.price:.2f} "
            f"fee={fill.fee:.2f}"
        )
    if report.halted:
        typer.echo(
            "\n!! Kill-Switch ausgeloest. Erst nachsehen, dann von Hand:\n"
            f"   qt paper reset-killswitch --strategy {strategy} --symbols {symbols} --tf {tf}"
        )
        raise typer.Exit(code=2)


@paper_app.command("status")
def paper_status(
    strategy: Annotated[str, typer.Option(help="Strategiename")] = "macross",
    symbols: Annotated[str, typer.Option(help="Kommagetrennt")] = "BTC/USD",
    tf: Annotated[str, typer.Option(help="Timeframe")] = "1d",
) -> None:
    """Kontostand anzeigen, ohne etwas zu veraendern."""
    from qt.data.store import read_bars
    from qt.live.state import PaperState, state_path
    from qt.report.daily import render

    symbol_list = _split(symbols)
    path = state_path(strategy, symbol_list, tf)
    state = PaperState.load(path)
    if state is None:
        typer.echo(f"Kein Paper-Konto unter {path}. Erst `qt paper run` laufen lassen.")
        raise typer.Exit(code=1)

    # Letzte bekannte Schlusskurse aus dem Store. Ohne sie kann der Report
    # offene Positionen nicht bewerten und sagt das auch -- statt wie frueher
    # den Einstand als "letzte bekannte Preise" auszugeben (ADR-053).
    prices: dict[str, float] = {}
    for sym in symbol_list:
        try:
            df = read_bars(sym, tf)
        except FileNotFoundError:
            continue
        if not df.empty:
            prices[sym] = float(df["close"].iloc[-1])

    typer.echo(render(state, strategy, symbol_list, prices=prices))


@paper_app.command("reset-killswitch")
def paper_reset(
    strategy: Annotated[str, typer.Option(help="Strategiename")] = "macross",
    symbols: Annotated[str, typer.Option(help="Kommagetrennt")] = "BTC/USD",
    tf: Annotated[str, typer.Option(help="Timeframe")] = "1d",
    note: Annotated[str, typer.Option(help="Warum wird zurueckgesetzt")] = "",
) -> None:
    """Kill-Switch manuell loesen -- der einzige Weg zurueck.

    Es gibt bewusst keinen automatischen Reset bei erholter Equity: zwischen
    Ausloesung und Wiederanlauf gehoert ein Mensch, der klaert, warum das
    Konto ueberhaupt so weit gefallen ist.
    """
    from qt.live.killswitch import NoPaperAccount, reset

    try:
        state = reset(strategy, _split(symbols), tf, note=note)
    except NoPaperAccount as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from None
    typer.echo(f"Kill-Switch zurueckgesetzt. halted={state.halted}")


@placebo_app.command("shuffle")
def placebo_shuffle(
    strategy: Annotated[str, typer.Option(help="Strategiename")] = "macross",
    symbol: Annotated[
        str | None,
        typer.Option(
            help="Kommagetrennt. Ohne Angabe: BTC/USD fuer Timing-Strategien, "
            "der ganze Store fuer Querschnittsstrategien."
        ),
    ] = None,
    tf: Annotated[str, typer.Option(help="Timeframe")] = "1d",
    train: Annotated[int, typer.Option(help="Train-Fenster in Bars")] = 1000,
    test: Annotated[int, typer.Option(help="Test-Fenster in Bars")] = 250,
    embargo: Annotated[int, typer.Option(help="Embargo-Bars")] = 20,
    draws: Annotated[int, typer.Option(help="Zahl der Ziehungen")] = 200,
    seed: Annotated[int, typer.Option(help="Zufallssaat")] = 0,
    threshold: Annotated[
        float, typer.Option(help="Perzentil, das die echte Strategie halten muss")
    ] = 0.95,
    since: Annotated[str | None, typer.Option()] = None,
    until: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Die Strategie gegen gewuerfelte Fassungen ihrer selbst.

    **Timing-Strategie:** die Episodenlaengen des echten Gewichtsverlaufs
    werden untereinander getauscht -- gleiche Zeit im Markt, gleiche
    Trade-Zahl, gleiche Gebuehren, nur die **Lage** in der Zeit ist zufaellig.

    **Querschnittsstrategie:** je Halteblock wird neu ausgelost, **welcher**
    Markt welches Gewicht bekommt -- gleiches Brutto, gleiches Netto, gleiche
    Haltedauer. Die Zuordnung ist dort die Behauptung, nicht der Zeitpunkt,
    und die Kontrolle richtet sich danach (ADR-059).

    Verglichen wird gegen den Abspieler mit den echten Gewichten, nicht gegen
    die Strategie selbst: nur er ist mit den Ziehungen konstruktionsgleich.
    Der Abstand zwischen beiden steht als **Pfadabhaengigkeit** im Bericht.
    Bei einer zustandslosen Strategie muss er null sein.
    """
    from qt.backtest.walkforward import InsufficientDataError
    from qt.core.config import BacktestConfig, costs_for_symbols
    from qt.data.store import available, read_bars, to_bars
    from qt.research.placebo import permutation_control
    from qt.strategy.cross_sectional import CrossSectionalStrategy
    from qt.strategy.registry import get, load_library

    load_library()
    klasse = get(strategy)
    quer = isinstance(klasse, type) and issubclass(klasse, CrossSectionalStrategy)

    if symbol is not None:
        namen = _split(symbol)
    elif quer:
        namen = sorted({sym for sym, timeframe in available() if timeframe == tf})
    else:
        namen = ["BTC/USD"]
    if not namen:
        typer.echo(f"Keine Maerkte mit Timeframe {tf} im Store.")
        raise typer.Exit(code=1)

    bars = {
        sym: to_bars(sym, tf, read_bars(sym, tf, start=since, end=until))
        for sym in namen
    }
    # Ohne das zahlen die ETFs den Krypto-Taker (ADR-056).
    cfg = BacktestConfig(costs_by_symbol=costs_for_symbols(namen))

    wobei = "Querschnitt" if quer else "Timing"
    typer.echo(
        f"{strategy} auf {len(namen)} Markt/Maerkten @ {tf}, "
        f"{draws} Ziehungen ({wobei}) ..."
    )
    fortschritt = max(1, draws // 10)

    def melden(i: int, wert: float) -> None:
        if i % fortschritt == 0:
            typer.echo(f"  {i}/{draws}")

    try:
        ergebnis = permutation_control(
            lambda: klasse(namen, tf),
            bars, train_bars=train, test_bars=test, embargo_bars=embargo,
            draws=draws, seed=seed, on_draw=melden, cfg=cfg,
        )
    except InsufficientDataError as exc:
        typer.echo(f"\nNicht auswertbar: {exc}")
        raise typer.Exit(code=1) from None

    typer.echo("")
    typer.echo(ergebnis.table())

    if ergebnis.pfadabhaengigkeit > 1e-6:
        typer.echo(
            f"\n  Hinweis: Pfadabhaengigkeit {ergebnis.pfadabhaengigkeit:.3f} -- "
            "die Strategie traegt Zustand ueber Bars.\n"
            "  Der Walk-Forward setzt sie je Fenster neu auf, der Abspieler "
            "laeuft durch. Verglichen\n"
            "  wird deshalb gegen den Abspieler; die Ziehungen sind mit ihm "
            "konstruktionsgleich."
        )

    if ergebnis.bestanden(threshold):
        typer.echo(
            f"\nBESTANDEN -- die echte Strategie liegt im obersten "
            f"{1 - threshold:.0%} ihrer eigenen Permutationen."
        )
        raise typer.Exit(code=0)

    typer.echo(
        f"\nDURCHGEFALLEN -- Perzentil {ergebnis.perzentil:.1%} liegt unter "
        f"{threshold:.0%}.\n"
        "Die Zeit im Markt traegt das Ergebnis, nicht das Timing. Das ist ein\n"
        "Ergebnis und kein Fehler (ADR-048)."
    )
    raise typer.Exit(code=2)


@placebo_app.command("cross")
def placebo_cross(
    strategy: Annotated[str, typer.Option(help="Strategiename")] = "macross",
    tf: Annotated[str, typer.Option(help="Timeframe")] = "1d",
    train: Annotated[int, typer.Option(help="Train-Fenster in Bars")] = 1000,
    test: Annotated[int, typer.Option(help="Test-Fenster in Bars")] = 250,
    embargo: Annotated[int, typer.Option(help="Embargo-Bars")] = 20,
    symbols: Annotated[
        str | None,
        typer.Option(help="Kommagetrennt. Ohne Angabe alle Maerkte des Stores."),
    ] = None,
) -> None:
    """Dieselbe Strategie, unveraendert, auf jedem Markt des Stores.

    Kein Parameter wird je Markt neu gewaehlt -- das waere genau die Selektion,
    gegen die der Test gerichtet ist. Maerkte mit zu kurzer Historie werden mit
    Grund ausgewiesen statt still uebersprungen.

    Krypto-Maerkte laufen stark gleich. Der Bericht weist deshalb die mittlere
    paarweise Korrelation und die daraus folgende **effektive** Marktzahl aus
    (ADR-052): dreizehn Maerkte sind keine dreizehn Tests.
    """
    from qt.core.config import BacktestConfig, costs_for_symbols
    from qt.data.store import available, read_bars, to_bars
    from qt.research.placebo import cross_market_control
    from qt.strategy.cross_sectional import CrossSectionalStrategy
    from qt.strategy.registry import get, load_library

    load_library()
    klasse = get(strategy)
    if isinstance(klasse, type) and issubclass(klasse, CrossSectionalStrategy):
        # Sonst laeuft eine Rangfolge ueber einen einzigen Namen -- sie
        # liefert nichts, jeder Markt meldet Sharpe 0, und der Median darueber
        # sieht aus wie ein Ergebnis (ADR-059).
        typer.echo(
            f"{strategy} ist eine Querschnittsstrategie: sie **braucht** alle\n"
            "Maerkte gleichzeitig. Ein Lauf je Markt einzeln ist keine "
            "schwaechere Pruefung,\n"
            "sondern gar keine. Ihre Negativkontrolle ist die Umbenennung "
            "der Maerkte:\n"
            f"  qt placebo shuffle --strategy {strategy} --tf {tf}"
        )
        raise typer.Exit(code=1)

    if symbols is not None:
        namen = _split(symbols)
    else:
        namen = sorted({sym for sym, timeframe in available() if timeframe == tf})
    if not namen:
        typer.echo(f"Keine Maerkte mit Timeframe {tf} im Store.")
        raise typer.Exit(code=1)

    maerkte = {sym: to_bars(sym, tf, read_bars(sym, tf)) for sym in namen}
    # Ohne das zahlen die ETFs den Krypto-Taker (ADR-056).
    cfg = BacktestConfig(costs_by_symbol=costs_for_symbols(namen))
    typer.echo(f"{strategy} unveraendert auf {len(maerkte)} Maerkten @ {tf} ...")

    def melden(lauf) -> None:
        stand = f"{lauf.sharpe:+.2f}" if lauf.testbar else lauf.grund
        typer.echo(f"  {lauf.symbol:<12} {stand}")

    ergebnis = cross_market_control(
        klasse, maerkte, tf, train_bars=train, test_bars=test,
        embargo_bars=embargo, on_market=melden, cfg=cfg,
    )

    typer.echo("")
    typer.echo(ergebnis.table())
    raise typer.Exit(code=0 if ergebnis.median_sharpe > 0 else 2)

@data_app.command("stocks")
def data_stocks(
    symbols: Annotated[
        str | None,
        typer.Option(help="Kommagetrennt. Ohne Angabe der Korb aus docs/ZIEL.md."),
    ] = None,
    since: Annotated[str, typer.Option(help="Startdatum YYYY-MM-DD")] = "2019-01-01",
    until: Annotated[str | None, typer.Option(help="Enddatum YYYY-MM-DD")] = None,
) -> None:
    """Aktien-, Anleihen-, Rohstoff- und FX-ETFs von Tiingo ziehen.

    Die zweite Anlageklasse ist der einzige Hebel, der die beweisbare
    Sharpe-Schwelle wirklich senkt: bei 1,4 effektiv unabhaengigen Maerkten
    liegt sie bei 0,67, bei 5 nur noch bei 0,33 (docs/ZIEL.md). Weitere
    Krypto-Paare bringen bei einer Korrelation von 0,67 fast nichts.

    Geladen werden **adjustierte** OHLC -- eine Dividende ist sonst ein
    Uebernachtsprung, den ein Trendfolger als Signal handelt. Neben jede Reihe
    kommt eine Meta-Datei mit Abrufdatum, weil die Adjustierung retroaktiv ist
    und eine heute gezogene Reihe damit eine andere ist als dieselbe von
    letztem Jahr (ADR-055).

    Schluessel aus `TIINGO_API_KEY`.
    """
    from qt.data.tiingo import BASKET, TiingoUnavailable, describe_basket, pull

    start = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(until).replace(tzinfo=timezone.utc) if until else None
    namen = _split(symbols) if symbols else sorted(BASKET)

    if not symbols:
        typer.echo("Korb aus docs/ZIEL.md:")
        typer.echo(describe_basket())
        typer.echo("")

    typer.echo(f"Ziehe {len(namen)} Ticker ab {since} von Tiingo ...")

    def melden(ticker: str, n: int, hinweis: str) -> None:
        klasse = BASKET.get(ticker, ("", ""))[0]
        typer.echo(f"  {ticker:<6} {klasse:<10} {n:>6,} Bars  {hinweis}")

    try:
        geschrieben = pull(namen, start, end, on_symbol=melden)
    except TiingoUnavailable as exc:
        typer.echo(f"\n{exc}")
        raise typer.Exit(code=1) from None

    if not geschrieben:
        typer.echo("Nichts geschrieben -- Ticker oder Zeitraum pruefen.")
        raise typer.Exit(code=1)
    typer.echo(f"\n{len(geschrieben)} Reihen geschrieben. Jetzt pruefen: qt data report")

# **Ganz am Ende, und das ist keine Formsache.** Der Block stand lange in der
# Mitte der Datei -- vor `research`, `data trades` und allen drei
# `paper`-Befehlen. Ueber das Konsolenskript (`qt = qt.cli:app`) faellt das
# nicht auf, weil das Modul erst vollstaendig importiert und dann `app()`
# gerufen wird. `python src/qt/cli.py` dagegen fuehrt die Datei von oben nach
@app.command("costs")
def costs_cmd(
    strategy: Annotated[
        str, typer.Option(help="Kommagetrennt, oder 'alle' fuer die Registry")
    ] = "alle",
    symbol: Annotated[str, typer.Option(help="Kommagetrennt")] = "BTC/USD",
    tf: Annotated[str, typer.Option(help="Timeframe")] = "1d",
    regimes: Annotated[
        str, typer.Option(help="Kommagetrennt; leer = alle bekannten")
    ] = "none,adr009_maker,coinbase_maker,coinbase_taker,kraken_taker",
    cash: Annotated[float, typer.Option(help="Startkapital")] = 100_000.0,
    since: Annotated[str | None, typer.Option()] = None,
    until: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Wie stark haengt das Ergebnis an der Kostenannahme?

    Zieht die Sensitivitaetstabelle aus ADR-009 fuer beliebige Strategien nach.
    Sie stand dort fuer genau eine Strategie auf genau einem Markt und wurde
    seither zitiert, als gaelte sie allgemein -- diese Annahme ist mit einem
    Befehl pruefbar statt mit Vertrauen (ADR-056).

    Die Spalte `none` ist kein Szenario, sondern das Messgeraet: die Differenz
    zu ihr ist genau das, was die Ausfuehrung frisst.
    """
    from qt.backtest.costs import round_trip_bps
    from qt.backtest.engine import run_backtest
    from qt.backtest.metrics import compute
    from qt.core.config import BacktestConfig
    from qt.data.store import read_bars, to_bars
    from qt.strategy.registry import get, load_library, names

    load_library()
    namen = names() if strategy == "alle" else _split(strategy)
    regime_namen = _split(regimes)
    konfigs = {r: _regime(r) for r in regime_namen}
    symbols = _split(symbol)

    bars = {
        sym: to_bars(sym, tf, read_bars(sym, tf, start=since, end=until))
        for sym in symbols
    }

    typer.echo(f"\n{', '.join(symbols)}  |  {tf}  |  Startkapital {cash:,.0f}\n")
    kopf = f"{'Strategie':<14}" + "".join(f"{r:>16}" for r in regime_namen)
    typer.echo(kopf)
    typer.echo(f"{'Round-Trip bps':<14}" + "".join(
        f"{round_trip_bps(konfigs[r]):>16.1f}" for r in regime_namen
    ))
    typer.echo("-" * len(kopf))

    for name in namen:
        try:
            strategie = get(name)(symbols, tf)
        except Exception as exc:  # noqa: BLE001 -- eine Strategie darf fehlen
            typer.echo(f"{name:<14}  uebersprungen: {exc}")
            continue

        zeile = f"{name:<14}"
        for r in regime_namen:
            cfg = BacktestConfig(initial_cash=cash, costs=konfigs[r])
            try:
                res = run_backtest(strategie, bars, cfg)
                m = compute(res.equity.set_index("ts")["equity"], tf)
                faktor = res.equity["equity"].iloc[-1] / cash
                zeile += f"{faktor:>9.2f}x{m.sharpe:>6.2f}"
            except Exception:  # noqa: BLE001 -- Warmup zu lang o.ae.
                zeile += f"{'--':>16}"
        typer.echo(zeile)

    typer.echo("\nJe Zelle: Endkapital als Faktor, dahinter Sharpe.")


@app.command("maker")
def maker_cmd(
    strategy: Annotated[str, typer.Option(help="Strategiename")] = "macross",
    symbol: Annotated[str, typer.Option(help="Kommagetrennt")] = "BTC/USD",
    tf: Annotated[str, typer.Option(help="Timeframe")] = "1d",
    since: Annotated[str | None, typer.Option()] = None,
    until: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Waeren die Orders dieses Laufs passiv ueberhaupt gefuellt worden?

    Das Maker-Kostenregime halbiert die Gebuehr und streicht Spanne und
    Slippage -- unter der Bedingung, dass die Limit-Order gefuellt wird. Diese
    Bedingung ist nachrechenbar (`qt.backtest.maker`), und die Antwort
    entscheidet, ob das guenstigste Regime des Projekts ein Szenario ist oder
    ein Wunsch.
    """
    from qt.backtest.engine import run_backtest
    from qt.backtest.maker import klassifiziere, zusammenfassen
    from qt.core.config import BacktestConfig
    from qt.data.store import read_bars, to_bars
    from qt.strategy.registry import get, load_library

    load_library()
    symbols = _split(symbol)
    strategie = get(strategy)(symbols, tf)
    bars = {
        sym: to_bars(sym, tf, read_bars(sym, tf, start=since, end=until))
        for sym in symbols
    }

    result = run_backtest(strategie, bars, BacktestConfig(costs=_regime("coinbase_taker")))
    quoten = klassifiziere(result.fills, bars)
    if not quoten:
        typer.echo("Keine Orders im Zeitraum -- nichts zu klassifizieren.")
        raise typer.Exit(code=1)

    typer.echo(f"\n{strategy}  |  {', '.join(symbols)}  |  {tf}")
    typer.echo(f"{len(result.fills)} Orders aus dem Taker-Lauf\n")
    typer.echo(
        f"{'Symbol':<12} {'n':>4} {'marktnah':>9} {'passiv':>7} "
        f"{'Ausfall':>8} {'Maker%':>7} {'Ausfall%':>9} {'Ausf.$%':>8}"
    )
    typer.echo("-" * 70)
    for q in [*quoten, zusammenfassen(quoten)]:
        typer.echo(
            f"{q.symbol:<12} {q.n:>4} {q.marktnah:>9} {q.passiv_gefuellt:>7} "
            f"{q.nicht_gefuellt:>8} {q.maker_quote:>6.0%} "
            f"{q.ausfallquote:>8.0%} {q.ausfallquote_notional:>7.0%}"
        )

    g = zusammenfassen(quoten)
    typer.echo(
        f"\nNur {g.maker_quote:.0%} der Orders waeren passiv gefuellt worden. "
        f"'marktnah' heisst: das Limit war schon beim Open erreichbar --\n"
        f"die Order geht durch, zahlt aber Taker. Die Fuellquote ist eine "
        f"Obergrenze (Warteschlange nicht modelliert)."
    )


@app.command("gate")
def gate_cmd(
    strategy: Annotated[str, typer.Option(help="Strategiename, siehe qt strategies")] = "macross",
    tf: Annotated[str, typer.Option(help="Timeframe")] = "1d",
    symbol: Annotated[
        str | None,
        typer.Option(
            help="Hauptmarkt fuer Umschlag, Walk-Forward und Permutation. "
            "Ohne Angabe: BTC/USD fuer Timing-Strategien, der ganze Store "
            "fuer Querschnittsstrategien."
        ),
    ] = None,
    train: Annotated[int, typer.Option(help="Train-Fenster in Bars")] = 1000,
    test: Annotated[int, typer.Option(help="Test-Fenster in Bars")] = 250,
    embargo: Annotated[int, typer.Option(help="Embargo-Bars")] = 20,
    draws: Annotated[int, typer.Option(help="Ziehungen der Permutationskontrolle")] = 200,
) -> None:
    """Gate 1 aus ZIEL.md, alle Kriterien auf einmal, Abbruch beim ersten Nein.

    **Es gibt hier absichtlich keine Option, die eine Schwelle setzt.** Die
    Zahlen stehen als Konstanten in `qt.research.gate`; wer sie aendert,
    hinterlaesst einen Diff. Nach einem verfehlten Kriterium ist die
    Versuchung, den Massstab nachzubessern, am groessten -- ADR-055 haelt
    fest, wie nah dieses Projekt daran schon war.

    Der Lauf **schreibt nichts** in die Registry. Er rechnet die DSR gegen
    `trial_count() + 1` -- also so, als zaehlte er mit --, legt aber keine
    Zeile an: eine Bibliotheksstrategie ist kein Kandidat mit Quelltext-
    Schnappschuss. Wer einen neuen Kandidaten einbucht, tut das ueber
    `qt research --screen`; wer den Bestand nachtraegt, ueber
    `qt trials --backfill` (ADR-057).
    """
    from qt.data.store import available, read_bars, to_bars
    from qt.research.gate import run_gate
    from qt.research.registry import ResearchRegistry
    from qt.strategy.registry import get, load_library

    load_library()
    strategie = get(strategy)
    namen = sorted({sym for sym, timeframe in available() if timeframe == tf})
    if not namen:
        typer.echo(f"Keine Maerkte mit Timeframe {tf} im Store.")
        raise typer.Exit(code=1)

    maerkte = {sym: to_bars(sym, tf, read_bars(sym, tf)) for sym in namen}
    haupt = _split(symbol) if symbol else None

    with ResearchRegistry.open() as registry:
        vorher = registry.trial_count()

    beschreibung = ", ".join(haupt) if haupt else "automatisch"
    typer.echo(f"\nGate 1: {strategy} @ {tf}, Hauptmarkt {beschreibung}")
    typer.echo(f"Versuchszaehler vor diesem Lauf: {vorher}\n")

    ergebnis = run_gate(
        strategie,
        maerkte,
        tf,
        trial_count=vorher + 1,
        haupt_symbole=haupt,
        train_bars=train,
        test_bars=test,
        embargo_bars=embargo,
        draws=draws,
        on_stage=lambda name: typer.echo(f"  ... {name}"),
    )

    typer.echo("")
    typer.echo(ergebnis.table())

    raise typer.Exit(code=0 if ergebnis.bestanden else 2)


@app.command("trials")
def trials_cmd(
    backfill: Annotated[
        bool,
        typer.Option(
            "--backfill",
            help="Die handgeschriebenen Bibliotheksstrategien als Versuche "
            "nachtragen. Idempotent.",
        ),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Nur zeigen, was fehlen wuerde")
    ] = False,
) -> None:
    """Der Versuchszaehler der Deflated Sharpe Ratio, und was ihn ausmacht.

    Der Zaehler ist der Nenner jeder DSR im Projekt. Faellt er zu niedrig aus,
    wird jede kuenftige Korrektur zu optimistisch -- und ein zu optimistischer
    Overfitting-Schutz ist schlimmer als keiner, weil er Sicherheit
    vortaeuscht (siehe `qt.research.registry`).
    """
    import pandas as pd

    from qt.research.backfill import HYPOTHESEN, QUELLE, nachtragen
    from qt.research.registry import ResearchRegistry

    with ResearchRegistry.open() as registry:
        if backfill or dry_run:
            neu = nachtragen(registry, dry_run=dry_run)
            if not neu:
                typer.echo("Nichts nachzutragen -- alle sieben stehen schon drin.")
            elif dry_run:
                typer.echo(f"Wuerde {len(neu)} Versuche nachtragen: {', '.join(neu)}")
            else:
                typer.echo(f"{len(neu)} Versuche nachgetragen: {', '.join(neu)}")

        stand = registry.trial_count()
        df = registry.history()

    typer.echo(f"\nVersuchszaehler: {stand}")
    if df.empty or "generator_model" not in df.columns:
        return

    gezaehlt = df[df["screening_status"].notna()] if "screening_status" in df else df
    hand = int((gezaehlt["generator_model"] == QUELLE).sum())
    typer.echo(f"  davon handgeschrieben: {hand} von {len(HYPOTHESEN)} bekannten")
    typer.echo(f"  davon aus dem Research-Loop: {stand - hand}")
    typer.echo(
        "\nJeder abgeschlossene Screening-Lauf erhoeht diese Zahl dauerhaft "
        "und macht\ndie DSR-Huerde fuer jeden kuenftigen Kandidaten haerter "
        "(ADR-032)."
    )
    if not pd.isna(stand) and stand > 0:
        from qt.research.dsr import expected_max_sharpe

        try:
            typer.echo(
                f"\nErwarteter bester Sharpe aus reinem Rauschen bei {stand} "
                f"Versuchen: {expected_max_sharpe(stand):.3f}"
            )
        except Exception:  # noqa: BLE001 -- reine Zusatzinfo
            pass


@app.command("ic")
def ic_cmd(
    strategy: Annotated[
        str, typer.Option(help="Querschnittsstrategie, siehe qt strategies")
    ] = "crossmom",
    tf: Annotated[str, typer.Option(help="Timeframe")] = "1d",
    horizon: Annotated[int, typer.Option(help="Vorwaertshorizont in Bars")] = 21,
    symbols: Annotated[
        str | None,
        typer.Option(help="Kommagetrennt. Ohne Angabe alle Maerkte des Stores."),
    ] = None,
) -> None:
    """Sagt das Signal die **Reihenfolge** der Maerkte voraus?

    Der Querschnitts-Rank-IC. Er nutzt die Korrelation der Maerkte, statt an
    ihr zu scheitern: was allen gemeinsam ist, faellt heraus (ADR-058).

    Zwei Zahlen stehen nebeneinander -- der naive t-Wert und der um die
    Autokorrelation der IC-Reihe korrigierte. Die Differenz ist der Grund,
    warum es diesen Befehl gibt: fuer 12-1-Momentum ueber diesen Store sind
    das +4,22 gegen +1,42.
    """
    from qt.data.store import available
    from qt.research.ic import forward_returns, rank_ic
    from qt.strategy.cross_sectional import (
        CrossSectionalStrategy,
        panel_from_store,
        score_panel,
    )
    from qt.strategy.registry import get, load_library

    load_library()
    klasse = get(strategy)
    if not issubclass(klasse, CrossSectionalStrategy):
        typer.echo(
            f"{strategy} ist keine Querschnittsstrategie. Der Rank IC misst "
            f"eine Rangfolge ueber Maerkte; eine Timing-Strategie hat keine."
        )
        raise typer.Exit(code=1)

    namen = _split(symbols) if symbols else sorted(
        {sym for sym, timeframe in available() if timeframe == tf}
    )
    if not namen:
        typer.echo(f"Keine Maerkte mit Timeframe {tf} im Store.")
        raise typer.Exit(code=1)

    schluss = panel_from_store(namen, tf, "close")
    opens = panel_from_store(namen, tf, "open")
    if schluss.empty:
        typer.echo("Panel leer.")
        raise typer.Exit(code=1)

    strategie = klasse(list(schluss.columns), tf)
    typer.echo(
        f"\n{strategy} ueber {schluss.shape[1]} Maerkte, {schluss.shape[0]} Zeitpunkte "
        f"({schluss.index[0].date()} .. {schluss.index[-1].date()})\n"
    )

    signal = score_panel(strategie, schluss)
    ergebnis = rank_ic(signal, forward_returns(opens, horizon), horizont=horizon)
    typer.echo(ergebnis.table())
    raise typer.Exit(code=0 if ergebnis.bestanden else 2)


# unten aus und startet die CLI, bevor die spaeteren Dekoratoren gelaufen
# sind: die Haelfte der Befehle existierte dort schlicht nicht, ohne
# Fehlermeldung (ADR-053).
if __name__ == "__main__":
    app()
