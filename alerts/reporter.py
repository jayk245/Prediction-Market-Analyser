"""
Reporting and alerting system.

Renders findings to:
  - Rich terminal output (colour-coded risk levels)
  - JSON file (for downstream processing / storage)
  - Plain-text summary
"""

import json
import os
from datetime import datetime, timezone
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

console = Console()

RISK_COLORS = {
    "CRITICAL": "bold red",
    "HIGH":     "red",
    "MEDIUM":   "yellow",
    "LOW":      "green",
}


# ── Formatting helpers ────────────────────────────────────────────────────────

def _truncate(s: str, n: int = 16) -> str:
    if len(s) <= n:
        return s
    return s[:n - 1] + "…"


# ── Terminal report: scored profiles ─────────────────────────────────────────

def print_profile_report(scored_profiles: list[dict], top_n: int = 20):
    """Print a rich table of the top flagged trading profiles."""
    flagged = [p for p in scored_profiles if p.get("flags")]
    if not flagged:
        console.print("[green]No flagged profiles found.[/green]")
        return

    console.print(Panel(
        f"[bold]Suspicious Trading Profiles[/bold]  "
        f"(showing top {min(top_n, len(flagged))} of {len(flagged)} flagged)",
        style="bold white",
    ))

    table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
        row_styles=["", "dim"],
    )
    table.add_column("Rank",      width=5,  justify="right")
    table.add_column("Source",    width=10)
    table.add_column("ID",        width=18)
    table.add_column("Score",     width=7,  justify="right")
    table.add_column("Risk",      width=10)
    table.add_column("Trades",    width=8,  justify="right")
    table.add_column("Win Rate",  width=9,  justify="right")
    table.add_column("Profit $",  width=11, justify="right")
    table.add_column("Flags",     width=40)

    for rank, prof in enumerate(flagged[:top_n], 1):
        source    = prof.get("source", "?")
        ident     = prof.get("wallet") or prof.get("ticker") or "?"
        score     = prof.get("composite_score", 0)
        risk      = prof.get("risk_level", "LOW")
        total     = prof.get("total_trades", "?")
        wr        = prof.get("win_rate")
        profit    = prof.get("profit_usd")
        flags     = ", ".join(prof.get("flags", []))

        wr_str = f"{wr:.1%}" if isinstance(wr, float) else "N/A"
        profit_str = f"${profit:,.0f}" if isinstance(profit, float) else "N/A"
        score_str = f"{score:.0f}"
        color = RISK_COLORS.get(risk, "white")

        table.add_row(
            str(rank),
            source,
            _truncate(ident),
            f"[{color}]{score_str}[/{color}]",
            f"[{color}]{risk}[/{color}]",
            str(total),
            wr_str,
            profit_str,
            f"[dim]{flags}[/dim]",
        )

    console.print(table)
    console.print()

    # Per-profile trade drill-down
    for rank, prof in enumerate(flagged[:top_n], 1):
        if not prof.get("flags"):
            continue
        _print_profile_trade_detail(rank, prof)


def _print_profile_trade_detail(rank: int, prof: dict):
    """Print the individual trades for a flagged profile, grouped by flag."""
    ident  = prof.get("wallet") or prof.get("ticker") or "?"
    risk   = prof.get("risk_level", "LOW")
    color  = RISK_COLORS.get(risk, "white")

    console.print(
        f"  [{color}]#{rank} {_truncate(ident, 30)} — {risk}[/{color}]  "
        f"flags: [dim]{', '.join(prof.get('flags', []))}[/dim]"
    )

    raw_trades = prof.get("raw_trades", [])
    if not raw_trades:
        console.print("    [dim]No individual trade data available.[/dim]\n")
        return

    table = Table(
        box=box.SIMPLE,
        show_header=True,
        header_style="cyan",
        padding=(0, 1),
    )
    table.add_column("Time",         width=20)
    table.add_column("Market Name",  width=34)
    table.add_column("Wallet",       width=20)
    table.add_column("Side",         width=6)
    table.add_column("Contracts",    width=11, justify="right")
    table.add_column("Price",        width=7,  justify="right")
    table.add_column("Notional $",   width=12, justify="right")
    table.add_column("W/L",          width=5)

    market_result = prof.get("market_result", "")

    for t in raw_trades[:25]:
        ts_raw = t.get("created_time") or t.get("timestamp") or t.get("createdAt") or ""
        try:
            if isinstance(ts_raw, (int, float)):
                ts_str = datetime.fromtimestamp(float(ts_raw), timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            else:
                ts_str = ts_raw[:19].replace("T", " ")
        except Exception:
            ts_str = str(ts_raw)[:19]

        mname    = _truncate(t.get("_market_name") or t.get("_ticker") or t.get("market") or "?", 32)
        wallet   = _truncate(t.get("proxyWallet") or t.get("maker") or "—", 18)
        side_raw = (t.get("taker_side") or t.get("side") or "").upper()
        outcome  = (t.get("outcome") or "").lower()
        side     = outcome.upper() if (side_raw == "BUY" and outcome) else side_raw or "?"
        contracts = float(t.get("count") or t.get("size") or 0)
        price    = float(t.get("yes_price") or t.get("price") or 0)
        notional = contracts * price

        side_color = "green" if side == "YES" else "red" if side == "NO" else "white"

        if market_result:
            is_win = (side.lower() == market_result.lower())
            wl_str = "[green]W[/green]" if is_win else "[red]L[/red]"
        else:
            wl_str = "[dim]—[/dim]"

        table.add_row(
            ts_str,
            mname,
            wallet,
            f"[bold {side_color}]{side}[/bold {side_color}]",
            f"{contracts:,.0f}",
            f"{price:.4f}",
            f"${notional:,.2f}",
            wl_str,
        )

    console.print(table)
    console.print()


# ── Terminal report: pattern alerts ──────────────────────────────────────────

def print_pattern_report(pattern_results: dict):
    """Print a rich summary of pattern-based alerts."""
    total = sum(len(v) for v in pattern_results.values())
    if total == 0:
        console.print("[green]No pattern alerts found.[/green]")
        return

    console.print(Panel(
        f"[bold]Pattern-Based Alerts[/bold]  ({total} total)",
        style="bold white",
    ))

    for pattern_type, alerts in pattern_results.items():
        if not alerts:
            continue

        label = pattern_type.replace("_", " ").title()
        console.print(f"[bold yellow]▶ {label}[/bold yellow]  ({len(alerts)} alerts)")

        table = Table(box=box.SIMPLE, show_header=True, header_style="cyan")

        if pattern_type == "position_spikes":
            table.add_column("Market", width=30)
            table.add_column("Volume ×", width=10)
            table.add_column("Spike Vol", width=12)
            table.add_column("Severity", width=10)
            for a in alerts[:10]:
                sev = a.get("severity", "")
                color = RISK_COLORS.get(sev, "white")
                table.add_row(
                    _truncate(a.get("market_id", "?"), 28),
                    f"{a.get('volume_multiplier', 0):.1f}×",
                    f"{a.get('spike_volume', 0):,.1f}",
                    f"[{color}]{sev}[/{color}]",
                )

        elif pattern_type == "coordinated_wallets":
            table.add_column("Wallet 1", width=18)
            table.add_column("Wallet 2", width=18)
            table.add_column("Shared Bets", width=12)
            table.add_column("Similarity", width=10)
            table.add_column("Severity", width=10)
            for a in alerts[:10]:
                sev = a.get("severity", "")
                color = RISK_COLORS.get(sev, "white")
                table.add_row(
                    _truncate(a.get("wallet_1", "?"), 16),
                    _truncate(a.get("wallet_2", "?"), 16),
                    str(a.get("shared_market_bets", 0)),
                    f"{a.get('jaccard_similarity', 0):.2%}",
                    f"[{color}]{sev}[/{color}]",
                )

        elif pattern_type == "cross_market_edge":
            table.add_column("Wallet", width=18)
            table.add_column("Win Rate", width=10)
            table.add_column("Markets", width=9)
            table.add_column("Edge Score", width=12)
            table.add_column("Severity", width=10)
            for a in alerts[:10]:
                sev = a.get("severity", "")
                color = RISK_COLORS.get(sev, "white")
                table.add_row(
                    _truncate(a.get("wallet", "?"), 16),
                    f"{a.get('win_rate', 0):.1%}",
                    str(a.get("markets_traded", 0)),
                    f"{a.get('edge_score', 0):.1f}",
                    f"[{color}]{sev}[/{color}]",
                )

        elif pattern_type == "event_timing_clusters":
            table.add_column("Market", width=30)
            table.add_column("Event Time", width=22)
            table.add_column("Pre-event Trades", width=16)
            table.add_column("Wallets", width=9)
            for a in alerts[:10]:
                table.add_row(
                    _truncate(a.get("market_id", "?"), 28),
                    a.get("event_time", "?")[:19],
                    str(a.get("pre_event_trade_count", 0)),
                    str(a.get("unique_wallets", 0)),
                )

        console.print(table)


# ── Summary panel ─────────────────────────────────────────────────────────────

def print_summary(
    scored_profiles: list[dict],
    pattern_results: dict,
    run_metadata: dict,
):
    """Print a brief executive summary."""
    critical = sum(1 for p in scored_profiles if p.get("risk_level") == "CRITICAL")
    high     = sum(1 for p in scored_profiles if p.get("risk_level") == "HIGH")
    medium   = sum(1 for p in scored_profiles if p.get("risk_level") == "MEDIUM")
    total_flags = sum(len(p.get("flags", [])) for p in scored_profiles)
    pattern_alerts = sum(len(v) for v in pattern_results.values())

    lines = [
        f"[bold]Run completed:[/bold] {run_metadata.get('run_time', 'N/A')}",
        f"[bold]Lookback:[/bold] {run_metadata.get('days_back', '?')} days",
        f"[bold]Markets analyzed:[/bold] {run_metadata.get('total_markets', 0):,}",
        f"[bold]Trades analyzed:[/bold] {run_metadata.get('total_trades', 0):,}",
        f"[bold]Profiles built:[/bold] {run_metadata.get('total_profiles', 0):,}",
        "",
        f"[bold red]CRITICAL profiles:[/bold red] {critical}",
        f"[red]HIGH profiles:[/red]     {high}",
        f"[yellow]MEDIUM profiles:[/yellow]   {medium}",
        f"[bold]Total stat flags:[/bold]  {total_flags}",
        f"[bold]Pattern alerts:[/bold]   {pattern_alerts}",
    ]

    console.print(Panel("\n".join(lines), title="[bold]Surveillance Summary[/bold]", style="blue"))


# ── JSON export ───────────────────────────────────────────────────────────────

def export_json(
    scored_profiles: list[dict],
    pattern_results: dict,
    run_metadata: dict,
    output_dir: str = "reports",
) -> str:
    """Export full results to a timestamped JSON file."""
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filepath = os.path.join(output_dir, f"surveillance_report_{ts}.json")

    # Summarise raw_trades into a compact `trades` list for the web dashboard.
    # Each entry has only the fields needed to show "what bet / what question".
    def _summarise_trades(trades: list) -> list:
        summary = []
        for t in (trades or [])[:50]:
            ts_raw = t.get("timestamp") or t.get("createdAt") or t.get("created_time") or ""
            size  = float(t.get("size")  or t.get("count")    or 0)
            price = float(t.get("price") or t.get("yes_price") or 0)
            summary.append({
                "timestamp":   str(ts_raw)[:19],
                "market_name": (t.get("_market_name") or t.get("_ticker") or t.get("market") or "")[:48],
                "market_id":   t.get("market") or t.get("_ticker") or "",
                "side":        (t.get("outcome") or t.get("taker_side") or t.get("side") or "").upper(),
                "contracts":   size,
                "price":       round(price, 4),
                "notional_usd": round(size * price, 2),
                "wallet":      t.get("proxyWallet") or t.get("maker") or "",
            })
        return summary

    clean_profiles = []
    for p in scored_profiles:
        cp = {k: v for k, v in p.items() if k != "raw_trades"}
        cp["trades"] = _summarise_trades(p.get("raw_trades", []))
        clean_profiles.append(cp)

    report = {
        "metadata": run_metadata,
        "flagged_profiles": [p for p in clean_profiles if p.get("flags")],
        "all_profiles": clean_profiles,
        "pattern_alerts": pattern_results,
    }

    with open(filepath, "w") as f:
        json.dump(report, f, indent=2, default=str)

    console.print(f"[green]Report saved:[/green] {filepath}")
    return filepath


# ── Full report ───────────────────────────────────────────────────────────────

def generate_full_report(
    scored_profiles: list[dict],
    pattern_results: dict,
    run_metadata: dict,
    export: bool = True,
    top_n: int = 20,
) -> Optional[str]:
    """Print all reports to terminal and optionally export to JSON."""
    console.rule("[bold blue]Prediction Market Surveillance Report[/bold blue]")
    print_summary(scored_profiles, pattern_results, run_metadata)
    console.rule()
    print_profile_report(scored_profiles, top_n=top_n)
    print_pattern_report(pattern_results)

    if export:
        return export_json(scored_profiles, pattern_results, run_metadata)
    return None
