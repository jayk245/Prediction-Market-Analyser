"""
Prediction Market Surveillance System
──────────────────────────────────────
Detects statistically anomalous and potentially insider-informed trading
on Kalshi and Polymarket.

Usage:
  python main.py                          # full scan, last 30 days
  python main.py --days 7                 # last 7 days only
  python main.py --source kalshi          # Kalshi only
  python main.py --source polymarket      # Polymarket only
  python main.py --no-export              # skip JSON export
  python main.py --top 50                 # show top 50 profiles
  python main.py --event-ts "2025-01-15T10:00:00Z"   # flag trades near this event
"""

import asyncio
import sys
import os
from datetime import datetime, timezone
from typing import Optional

import click
from rich.console import Console

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitors.realtime_monitor import RealtimeMonitor

from collectors.kalshi_collector import collect_kalshi_data
from collectors.polymarket_collector import collect_polymarket_data
from analyzers.statistical import score_profiles
from analyzers.pattern_detector import run_pattern_detection
from alerts.reporter import generate_full_report

console = Console()


# ── Async orchestration ───────────────────────────────────────────────────────

async def run_surveillance(
    days_back: int = 30,
    source: str = "both",
    event_timestamps: Optional[list[datetime]] = None,
    export: bool = True,
    top_n: int = 20,
):
    run_start = datetime.now(timezone.utc)
    console.rule("[bold blue]Starting Surveillance Scan[/bold blue]")
    console.print(f"  Days back: {days_back}  |  Source: {source}")

    kalshi_data: Optional[dict] = None
    polymarket_data: Optional[dict] = None

    # ── Data collection ───────────────────────────────────────────────────────
    try:
        if source in ("both", "kalshi"):
            console.rule("[cyan]Collecting Kalshi Data[/cyan]")
            kalshi_data = await collect_kalshi_data(days_back=days_back)
    except Exception as e:
        console.print(f"[red][Kalshi] Collection failed: {e}[/red]")

    try:
        if source in ("both", "polymarket"):
            console.rule("[cyan]Collecting Polymarket Data[/cyan]")
            polymarket_data = await collect_polymarket_data(days_back=days_back)
    except Exception as e:
        console.print(f"[red][Polymarket] Collection failed: {e}[/red]")

    if not kalshi_data and not polymarket_data:
        console.print("[bold red]No data collected. Exiting.[/bold red]")
        sys.exit(1)

    # ── Statistical scoring ───────────────────────────────────────────────────
    console.rule("[cyan]Running Statistical Analysis[/cyan]")
    all_profiles: list[dict] = []

    if kalshi_data:
        all_profiles.extend(kalshi_data.get("trader_profiles", []))
    if polymarket_data:
        all_profiles.extend(polymarket_data.get("wallet_profiles", []))

    console.print(f"[bold]Scoring {len(all_profiles)} trading profiles...[/bold]")
    scored = score_profiles(all_profiles)

    # ── Pattern detection ─────────────────────────────────────────────────────
    console.rule("[cyan]Running Pattern Detection[/cyan]")
    pattern_results = run_pattern_detection(
        kalshi_data=kalshi_data,
        polymarket_data=polymarket_data,
        event_timestamps=event_timestamps,
    )

    # ── Metadata ──────────────────────────────────────────────────────────────
    total_markets = 0
    total_trades = 0
    if kalshi_data:
        total_markets += len(kalshi_data.get("markets", []))
        total_trades += sum(len(v) for v in kalshi_data.get("trades", {}).values())
    if polymarket_data:
        total_markets += len(polymarket_data.get("markets", []))
        total_trades += sum(len(v) for v in polymarket_data.get("trades", {}).values())

    run_metadata = {
        "run_time": run_start.isoformat(),
        "run_duration_seconds": (datetime.now(timezone.utc) - run_start).total_seconds(),
        "days_back": days_back,
        "source": source,
        "total_markets": total_markets,
        "total_trades": total_trades,
        "total_profiles": len(scored),
    }

    # ── Report ────────────────────────────────────────────────────────────────
    console.rule("[cyan]Generating Report[/cyan]")
    generate_full_report(
        scored_profiles=scored,
        pattern_results=pattern_results,
        run_metadata=run_metadata,
        export=export,
        top_n=top_n,
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """Prediction Market Surveillance — detects suspicious / insider-like trading."""


@cli.command("scan")
@click.option("--days",      default=30,   show_default=True, help="Days of history to scan")
@click.option("--source",    default="both", show_default=True,
              type=click.Choice(["both", "kalshi", "polymarket"], case_sensitive=False))
@click.option("--top",       default=20,   show_default=True, help="Number of top profiles to show")
@click.option("--no-export", is_flag=True, default=False,     help="Skip JSON export")
@click.option("--event-ts",  multiple=True,
              help="ISO 8601 timestamp of a known external event (repeatable)")
def cmd_scan(days: int, source: str, top: int, no_export: bool, event_ts: tuple):
    """Retrospective scan — scores historical trades on settled markets."""
    event_timestamps: list[datetime] = []
    for ts_str in event_ts:
        try:
            event_timestamps.append(
                datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            )
        except ValueError:
            console.print(f"[red]Invalid timestamp: {ts_str}[/red]")
            sys.exit(1)

    asyncio.run(
        run_surveillance(
            days_back=days,
            source=source.lower(),
            event_timestamps=event_timestamps or None,
            export=not no_export,
            top_n=top,
        )
    )


@cli.command("watch")
@click.option("--source",      default="both", show_default=True,
              type=click.Choice(["both", "kalshi", "polymarket"], case_sensitive=False),
              help="Data source(s) to monitor")
@click.option("--interval",    default=30,     show_default=True, help="Poll interval in seconds")
@click.option("--max-markets", default=50,     show_default=True, help="Max open markets to track")
@click.option("--flagged",     default=None,   help="JSON report from a prior 'scan' to seed known bad actors")
@click.option("--export",      default="reports/live_alerts.json", show_default=True,
              help="Path for the continuously-updated JSON alert log")
def cmd_watch(source: str, interval: int, max_markets: int, flagged: Optional[str], export: str):
    """
    Live monitor — polls open markets every INTERVAL seconds and fires
    pre-resolution alerts (volume spikes, order-flow skew, coordinated entry,
    price drift, known bad actors, time-to-close rushes).

    Tip: run 'scan' first, then pass its output JSON via --flagged so the
    monitor knows which wallets to watch for.

    Press Ctrl-C to stop.
    """
    import json

    flagged_wallets: set[str] = set()
    if flagged:
        try:
            with open(flagged) as f:
                data = json.load(f)
            if isinstance(data, list):
                flagged_wallets = set(data)
            else:
                flagged_wallets = {
                    p.get("wallet") for p in data.get("flagged_profiles", [])
                    if p.get("wallet")
                }
            console.print(f"[cyan]Loaded {len(flagged_wallets)} flagged wallets from {flagged}[/cyan]")
        except Exception as e:
            console.print(f"[red]Could not load flagged wallets: {e}[/red]")

    monitor = RealtimeMonitor(
        source=source.lower(),
        poll_interval=interval,
        max_markets=max_markets,
        flagged_wallets=flagged_wallets,
    )

    try:
        asyncio.run(monitor.run(export_path=export))
    except KeyboardInterrupt:
        console.print("\n[yellow]Monitor stopped.[/yellow]")


if __name__ == "__main__":
    cli()
