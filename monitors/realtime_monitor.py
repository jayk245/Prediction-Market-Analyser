"""
Real-time surveillance monitor.

Polls Kalshi and Polymarket on a configurable interval, feeds new trades
into per-market MarketState objects, and fires the pre-resolution detectors.

Run directly:
  python monitors/realtime_monitor.py
  python monitors/realtime_monitor.py --interval 30 --source kalshi
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional

import httpx
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich import box

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    KALSHI_BASE_URL,
    POLYMARKET_GAMMA_URL,
    POLYMARKET_DATA_URL,
    REQUEST_TIMEOUT,
    NTFY_TOPIC,
)
from analyzers.realtime_detector import MarketState, analyze_market

console = Console()

# ── ntfy.sh notifications (fire-and-forget, only HIGH/CRITICAL) ───────────────

_NOTIFY_SEVERITIES = {"CRITICAL", "HIGH"}


def _send_ntfy(alert: dict):
    """Post a push notification to ntfy.sh in a background thread."""
    if not NTFY_TOPIC:
        return
    severity    = alert.get("severity", "HIGH")
    signal      = alert.get("signal", "unknown").replace("_", " ")
    source      = alert.get("_source", "?")
    trades      = alert.get("triggering_trades", [])
    market_name = (trades[0].get("market_name") if trades else None) or alert.get("market_id", "?")
    desc        = alert.get("description", "")
    fired_at    = alert.get("_fired_at", "")

    title    = f"[{severity}] {signal.upper()} - {source}".encode("ascii", "ignore").decode()
    message  = f"{market_name}\n{desc}" + (f"\n{fired_at} UTC" if fired_at else "")
    priority = "urgent" if severity == "CRITICAL" else "high"

    try:
        httpx.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            content=message.encode("utf-8"),
            headers={
                "Title":    title,
                "Priority": priority,
                "Tags":     f"warning,{source}",
            },
            timeout=5,
        )
    except Exception:
        pass  # never let notification errors affect the monitor

SEVERITY_COLOR = {
    "CRITICAL": "bold red",
    "HIGH":     "red",
    "MEDIUM":   "yellow",
    "LOW":      "green",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_close_time(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ── Kalshi poller ─────────────────────────────────────────────────────────────

class KalshiPoller:
    def __init__(self):
        self._http = httpx.AsyncClient(
            base_url=KALSHI_BASE_URL,
            timeout=REQUEST_TIMEOUT,
            headers={"Accept": "application/json"},
        )

    async def close(self):
        await self._http.aclose()

    async def fetch_open_markets(self, limit: int = 100) -> list[dict]:
        try:
            r = await self._http.get("/markets", params={"status": "open", "limit": limit})
            r.raise_for_status()
            return r.json().get("markets", [])
        except Exception as e:
            console.print(f"[dim red][Kalshi] fetch_open_markets error: {e}[/dim red]")
            return []

    async def fetch_recent_trades(
        self, ticker: str, since_ts: Optional[datetime] = None, limit: int = 100
    ) -> list[dict]:
        params: dict = {"ticker": ticker, "limit": limit}
        try:
            r = await self._http.get("/markets/trades", params=params)
            r.raise_for_status()
            trades = r.json().get("trades", [])
        except Exception as e:
            console.print(f"[dim red][Kalshi] trades({ticker}) error: {e}[/dim red]")
            return []

        if since_ts:
            trades = [
                t for t in trades
                if _parse_trade_ts(t) and _parse_trade_ts(t) > since_ts
            ]
        return trades


# ── Polymarket poller ─────────────────────────────────────────────────────────

class PolymarketPoller:
    def __init__(self):
        self._gamma = httpx.AsyncClient(
            base_url=POLYMARKET_GAMMA_URL,
            timeout=REQUEST_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        self._data = httpx.AsyncClient(
            base_url=POLYMARKET_DATA_URL,
            timeout=REQUEST_TIMEOUT,
            headers={"Accept": "application/json"},
        )

    async def close(self):
        await self._gamma.aclose()
        await self._data.aclose()

    async def fetch_active_markets(self, limit: int = 100) -> list[dict]:
        try:
            # closed=false is required to skip old v1/legacy markets that have
            # active=true but no trade data in the current CLOB-era data API.
            r = await self._gamma.get(
                "/markets",
                params={"active": "true", "closed": "false", "limit": limit},
            )
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else data.get("data", data.get("markets", []))
        except Exception as e:
            console.print(f"[dim red][Polymarket] fetch_active_markets error: {e}[/dim red]")
            return []

    async def fetch_recent_trades(
        self, condition_id: str, since_ts: Optional[datetime] = None, limit: int = 100
    ) -> list[dict]:
        try:
            r = await self._data.get("/trades", params={"market": condition_id, "limit": limit})
            r.raise_for_status()
            data = r.json()
            trades = data if isinstance(data, list) else data.get("data", [])
        except Exception as e:
            console.print(f"[dim red][Polymarket] trades({condition_id[:12]}…) error: {e}[/dim red]")
            return []

        if since_ts:
            trades = [
                t for t in trades
                if _parse_trade_ts(t) and _parse_trade_ts(t) > since_ts
            ]
        return trades


def _parse_trade_ts(t: dict) -> Optional[datetime]:
    raw = t.get("created_time") or t.get("timestamp") or t.get("createdAt") or ""
    try:
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, OSError):
        return None


# ── Alert display ─────────────────────────────────────────────────────────────

class AlertLog:
    """Keeps the last N alerts and renders them as a live rich table."""

    def __init__(self, maxlen: int = 200):
        self._alerts: list[dict] = []
        self._maxlen = maxlen

    def add(self, alert: dict):
        enriched = {**alert, "_fired_at": _now_utc().strftime("%H:%M:%S")}
        self._alerts.insert(0, enriched)
        if alert.get("severity") in _NOTIFY_SEVERITIES:
            import threading
            threading.Thread(target=_send_ntfy, args=(enriched,), daemon=True).start()
        if len(self._alerts) > self._maxlen:
            self._alerts.pop()

    def render(self):
        from rich.console import Group
        from rich.padding import Padding

        return Group(
            self._render_alert_table(),
            Padding(self._render_trade_detail(), (1, 0, 0, 0)),
        )

    def _render_alert_table(self) -> Table:
        table = Table(
            title=f"[bold]Live Pre-Resolution Alerts[/bold]  "
                  f"({len(self._alerts)} total)  "
                  f"[dim]{_now_utc().strftime('%H:%M:%S UTC')}[/dim]",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan",
            expand=True,
        )
        table.add_column("Time",         width=9)
        table.add_column("Source",       width=10)
        table.add_column("Market Name",  width=32)
        table.add_column("Signal",       width=22)
        table.add_column("Severity",     width=10)
        table.add_column("# Trades",     width=9,  justify="right")
        table.add_column("Detail",       min_width=30)

        for a in self._alerts[:30]:
            sev      = a.get("severity", "MEDIUM")
            color    = SEVERITY_COLOR.get(sev, "white")
            n_trades = len(a.get("triggering_trades", []))
            # Prefer market_name from the first triggering trade, fall back to market_id
            trades   = a.get("triggering_trades", [])
            mname    = (trades[0].get("market_name") if trades else None) or a.get("market_id", "?")
            table.add_row(
                a.get("_fired_at", "?"),
                a.get("_source", "?"),
                _truncate(mname, 30),
                a.get("signal", "?").replace("_", " "),
                f"[{color}]{sev}[/{color}]",
                str(n_trades) if n_trades else "—",
                f"[dim]{a.get('description', '')}[/dim]",
            )
        return table

    def _render_trade_detail(self) -> Table:
        """
        Show the individual trades from the most recent CRITICAL or HIGH alert.
        """
        # Find the most recent alert that has triggering trades
        target = next(
            (a for a in self._alerts
             if a.get("triggering_trades") and a.get("severity") in ("CRITICAL", "HIGH")),
            None,
        )

        market_label = "—"
        trades: list[dict] = []
        if target:
            market_label = _truncate(target.get("market_id", "?"), 40)
            trades = target["triggering_trades"]

        table = Table(
            title=(
                f"[bold]Trade Detail[/bold]  "
                f"[yellow]{target.get('signal', '').replace('_', ' ')}[/yellow]  "
                f"[dim]{market_label}[/dim]"
                if target else "[bold dim]Trade Detail[/bold dim]  (no alerts yet)"
            ),
            box=box.SIMPLE,
            show_header=True,
            header_style="cyan",
            expand=True,
        )
        table.add_column("Time",         width=9)
        table.add_column("Wallet",       width=22)
        table.add_column("Side",         width=6)
        table.add_column("Market Name",  width=32)
        table.add_column("Contracts",    width=11, justify="right")
        table.add_column("Price",        width=7,  justify="right")
        table.add_column("Notional $",   width=12, justify="right")

        for t in trades[:20]:
            side  = t.get("side", "?")
            color = "green" if side == "YES" else "red" if side == "NO" else "white"
            table.add_row(
                t.get("time", "?"),
                _truncate(t.get("wallet", "—"), 20),
                f"[bold {color}]{side}[/bold {color}]",
                _truncate(t.get("market_name", t.get("market_id", "?")), 30),
                f"{t.get('contracts', 0):,.0f}",
                f"{t.get('price', 0):.4f}",
                f"${t.get('notional_usd', 0):,.2f}",
            )

        return table

    def export(self, path: str, *, poll_count: int = 0, markets_tracked: int = 0, source: str = ""):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        clean = [{k: v for k, v in a.items()} for a in self._alerts]
        payload = {
            "alerts":          clean,
            "last_updated":    _now_utc().isoformat(),
            "poll_count":      poll_count,
            "markets_tracked": markets_tracked,
            "source":          source,
        }
        with open(path, "w") as f:
            json.dump(payload, f, indent=2, default=str)


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


# Core monitoring loop

class RealtimeMonitor:
    def __init__(
        self,
        source: str = "both",
        poll_interval: int = 30,
        max_markets: int = 50,
        flagged_wallets: Optional[set[str]] = None,
    ):
        self.source         = source
        self.poll_interval  = poll_interval
        self.max_markets    = max_markets
        self.flagged_wallets = flagged_wallets or set()

        self._kalshi    = KalshiPoller() if source in ("both", "kalshi") else None
        self._poly      = PolymarketPoller() if source in ("both", "polymarket") else None

        # market_id → MarketState
        self._states: dict[str, MarketState] = {}
        self._alert_log = AlertLog()

    async def _refresh_markets(self):
        """Re-discover open/active markets and create state objects for new ones."""
        if self._kalshi:
            markets = await self._kalshi.fetch_open_markets(limit=self.max_markets)
            for m in markets:
                ticker = m.get("ticker", "")
                if ticker and ticker not in self._states:
                    close_time = _parse_close_time(
                        m.get("close_time") or m.get("expiration_time")
                    )
                    name = m.get("title") or m.get("subtitle") or ticker
                    self._states[ticker] = MarketState(ticker, close_time, market_name=name)
                    self._states[ticker]._source = "kalshi"

        if self._poly:
            markets = await self._poly.fetch_active_markets(limit=self.max_markets)
            for m in markets:
                cid = m.get("conditionId") or m.get("condition_id", "")
                if cid and cid not in self._states:
                    close_time = _parse_close_time(
                        m.get("endDateIso") or m.get("end_date_iso")
                    )
                    # Prefer the full question text over the short groupItemTitle.
                    # Polymarket categorical outcomes have questions like "yes Colorado
                    # Avalanche" — strip that prefix so binary markets show their full
                    # question ("Will Khamenei leave as Supreme Leader by Feb 28?").
                    q = (m.get("question") or "").strip()
                    if q.lower().startswith(("yes ", "no ")):
                        q = q.split(" ", 1)[1].strip()
                    name = q or (m.get("groupItemTitle") or "").strip() or (m.get("title") or "").strip() or cid[:24]
                    self._states[cid] = MarketState(cid, close_time, market_name=name)
                    self._states[cid]._source = "polymarket"

    async def _poll_once(self):
        """Fetch new trades for all tracked markets and run detectors."""
        semaphore = asyncio.Semaphore(10)

        async def _process(market_id: str, state: MarketState):
            async with semaphore:
                source = getattr(state, "_source", "unknown")
                since  = state.last_poll_time

                try:
                    if source == "kalshi" and self._kalshi:
                        raw = await self._kalshi.fetch_recent_trades(market_id, since)
                    elif source == "polymarket" and self._poly:
                        raw = await self._poly.fetch_recent_trades(market_id, since)
                    else:
                        return
                except Exception:
                    return

                new_trades = state.ingest_trades(raw)

                # Only run detectors if we have new data or it's the first poll
                if not new_trades and state.last_poll_time is not None:
                    return

                signals = analyze_market(state, self.flagged_wallets)
                for sig in signals:
                    sig["_source"] = source
                    self._alert_log.add(sig)

        await asyncio.gather(*[_process(mid, st) for mid, st in list(self._states.items())])

    async def run(self, export_path: Optional[str] = None):
        """Main loop — runs until cancelled (Ctrl-C)."""
        console.print(
            f"[bold cyan]Starting real-time monitor[/bold cyan]  "
            f"source={self.source}  interval={self.poll_interval}s  "
            f"max_markets={self.max_markets}"
        )

        # Initial market discovery
        await self._refresh_markets()
        console.print(f"[green]Tracking {len(self._states)} markets[/green]")

        poll_count = 0

        with Live(self._alert_log.render(), refresh_per_second=1, console=console) as live:
            while True:
                try:
                    await self._poll_once()
                    poll_count += 1

                    # Refresh market list every 10 polls (~5 min at 30s interval)
                    if poll_count % 10 == 0:
                        await self._refresh_markets()

                    live.update(self._alert_log.render())

                    if export_path:
                        self._alert_log.export(
                            export_path,
                            poll_count=poll_count,
                            markets_tracked=len(self._states),
                            source=self.source,
                        )

                    await asyncio.sleep(self.poll_interval)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    console.print(f"[red]Poll error: {e}[/red]")
                    await asyncio.sleep(5)

        # Cleanup
        if self._kalshi:
            await self._kalshi.close()
        if self._poly:
            await self._poly.close()

        console.print(f"[yellow]Monitor stopped. {poll_count} polls completed.[/yellow]")
        if export_path:
            self._alert_log.export(
                export_path,
                poll_count=poll_count,
                markets_tracked=len(self._states),
                source=self.source,
            )
            console.print(f"[green]Alerts saved to {export_path}[/green]")


# ── CLI ───────────────────────────────────────────────────────────────────────

import click

@click.command()
@click.option("--interval",    default=30,     show_default=True, help="Poll interval in seconds")
@click.option("--source",      default="both", show_default=True,
              type=click.Choice(["both", "kalshi", "polymarket"], case_sensitive=False))
@click.option("--max-markets", default=50,     show_default=True, help="Max open markets to track")
@click.option("--flagged",     default=None,   help="Path to JSON file of previously flagged wallet addresses")
@click.option("--export",      default="reports/live_alerts.json", show_default=True,
              help="Path to continuously-updated JSON alert log")
def cli(interval: int, source: str, max_markets: int, flagged: Optional[str], export: str):
    """
    Real-time prediction market surveillance — fires alerts BEFORE resolution.
    Press Ctrl-C to stop.
    """
    flagged_wallets: set[str] = set()
    if flagged:
        try:
            with open(flagged) as f:
                data = json.load(f)
            if isinstance(data, list):
                flagged_wallets = set(data)
            elif isinstance(data, dict):
                # Accept output from retrospective scan
                flagged_wallets = {
                    p.get("wallet") for p in data.get("flagged_profiles", [])
                    if p.get("wallet")
                }
            console.print(f"[cyan]Loaded {len(flagged_wallets)} flagged wallets[/cyan]")
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
        console.print("\n[yellow]Stopped by user.[/yellow]")


if __name__ == "__main__":
    cli()
