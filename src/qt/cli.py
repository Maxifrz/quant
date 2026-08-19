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

    result = run_backtest(strategy_obj, bars, BacktestConfig(initial_cash=cash))
    print_summary(result)
    path = render(result, out)
    typer.echo(f"\nTearsheet: {path}")


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
