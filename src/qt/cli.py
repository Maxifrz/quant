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


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


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
