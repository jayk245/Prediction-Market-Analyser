"""
Diagnostic script — run this to understand why nothing is being flagged.
Shows raw API field names, profile counts, and score distributions.

Usage:
  python diagnose.py                  # both sources
  python diagnose.py --source kalshi
  python diagnose.py --source polymarket
"""

import asyncio
import sys
import os
import json
from datetime import datetime, timezone, timedelta

import httpx
import click
from rich.console import Console
from rich.table import Table
from rich import box

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (
    KALSHI_BASE_URL, POLYMARKET_GAMMA_URL, POLYMARKET_DATA_URL,
    REQUEST_TIMEOUT, DETECTION,
)
from analyzers.statistical import score_profiles

console = Console()


# ── Kalshi diagnostics ────────────────────────────────────────────────────────

async def diagnose_kalshi():
    console.rule("[bold cyan]Kalshi Diagnostics[/bold cyan]")

    async with httpx.AsyncClient(base_url=KALSHI_BASE_URL, timeout=REQUEST_TIMEOUT) as client:

        # 1. Fetch a few settled markets
        r = await client.get("/markets", params={"status": "settled", "limit": 5})
        r.raise_for_status()
        markets = r.json().get("markets", [])
        console.print(f"[green]Settled markets fetched:[/green] {len(markets)}")

        if not markets:
            console.print("[red]No settled markets returned — API may be down or endpoint changed[/red]")
            return

        m = markets[0]
        console.print("\n[bold]First settled market keys:[/bold]")
        console.print(sorted(m.keys()))
        console.print(f"\n  ticker      = {m.get('ticker')}")
        console.print(f"  result      = {m.get('result')!r}   ← must be 'yes' or 'no'")
        console.print(f"  close_time  = {m.get('close_time')}")
        console.print(f"  title       = {m.get('title') or m.get('subtitle')}")

        # 2. Count how many have a usable result
        r2 = await client.get("/markets", params={"status": "settled", "limit": 100})
        all_settled = r2.json().get("markets", [])
        with_result = [m for m in all_settled if m.get("result") in ("yes", "no")]
        console.print(f"\n[bold]Markets with result='yes'/'no':[/bold] {len(with_result)} / {len(all_settled)}")

        # 3. Fetch trades for the first market with a result
        if with_result:
            ticker = with_result[0]["ticker"]
            r3 = await client.get("/markets/trades", params={"ticker": ticker, "limit": 10})
            trades = r3.json().get("trades", [])
            console.print(f"\n[bold]Sample trades for {ticker}:[/bold] {len(trades)} returned")
            if trades:
                t = trades[0]
                console.print(f"  Trade keys: {sorted(t.keys())}")
                console.print(f"  taker_side = {t.get('taker_side')!r}")
                console.print(f"  count      = {t.get('count')}")
                console.print(f"  yes_price  = {t.get('yes_price')}")
                console.print(f"  created_time = {t.get('created_time')}")

                close_str = with_result[0].get("close_time") or with_result[0].get("expiration_time", "")
                try:
                    close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
                    created_str = t.get("created_time", "")
                    created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    secs_before = (close_dt - created_dt).total_seconds()
                    console.print(f"  seconds before close = {secs_before:.0f}  (last_minute_window={DETECTION.last_minute_window_seconds}s)")
                except Exception as e:
                    console.print(f"  [red]Could not parse timestamps: {e}[/red]")

        # 4. Check how many last-minute trades exist across 10 markets
        console.print(f"\n[bold]Checking last-minute trades across {min(10, len(with_result))} markets...[/bold]")
        lm_count = 0
        for m in with_result[:10]:
            ticker = m["ticker"]
            close_str = m.get("close_time") or m.get("expiration_time", "")
            try:
                close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
            except Exception:
                continue
            r4 = await client.get("/markets/trades", params={"ticker": ticker, "limit": 100})
            trades = r4.json().get("trades", [])
            for t in trades:
                created_str = t.get("created_time", "")
                try:
                    created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    secs = (close_dt - created_dt).total_seconds()
                    if 0 <= secs <= DETECTION.last_minute_window_seconds:
                        lm_count += 1
                except Exception:
                    pass
        console.print(f"  Last-minute trades found: {lm_count}")


# ── Polymarket diagnostics ────────────────────────────────────────────────────

async def diagnose_polymarket():
    console.rule("[bold magenta]Polymarket Diagnostics[/bold magenta]")

    async with httpx.AsyncClient(base_url=POLYMARKET_GAMMA_URL, timeout=REQUEST_TIMEOUT) as gamma:
        async with httpx.AsyncClient(base_url=POLYMARKET_DATA_URL, timeout=REQUEST_TIMEOUT) as data:

            from collectors.polymarket_collector import _resolve_winner

            # 1. Fetch RECENT closed markets
            r = await gamma.get("/markets", params={"closed": "true", "limit": 20})
            r.raise_for_status()
            raw = r.json()
            all_markets = raw if isinstance(raw, list) else raw.get("data", raw.get("markets", []))
            # Sort newest first in Python
            def _end_ts(mx):
                try:
                    return datetime.fromisoformat(
                        (mx.get("endDateIso") or mx.get("end_date_iso") or "1970-01-01").replace("Z", "+00:00")
                    ).timestamp()
                except Exception:
                    return 0
            markets = sorted(all_markets, key=_end_ts, reverse=True)[:5]
            console.print(f"[green]Recent closed markets fetched:[/green] {len(all_markets)} (showing 5 newest)")

            if not markets:
                console.print("[red]No closed markets returned[/red]")
                return

            from collectors.polymarket_collector import _parse_outcomes, _parse_outcome_prices, _is_binary_yes_no

            m = markets[0]
            raw_outcomes = m.get('outcomes')
            console.print(f"\n  conditionId    = {m.get('conditionId') or m.get('condition_id')}")
            console.print(f"  question       = {m.get('question') or m.get('title')}")
            console.print(f"  endDateIso     = {m.get('endDateIso') or m.get('end_date_iso')}")
            console.print(f"  winnerOutcome  = {m.get('winnerOutcome')!r}")
            console.print(f"  lastTradePrice = {m.get('lastTradePrice')!r}   (type={type(m.get('lastTradePrice')).__name__})")
            console.print(f"  outcomes (raw) = {raw_outcomes!r}   type={type(raw_outcomes).__name__}")
            console.print(f"  outcomes (parsed) = {_parse_outcomes(m)}")
            console.print(f"  outcomePrices (parsed) = {_parse_outcome_prices(m)}")
            console.print(f"  is_binary_yes_no = {_is_binary_yes_no(m)}")
            console.print(f"  resolved winner = [bold]{_resolve_winner(m)!r}[/bold]")

            # Show winner resolution for 5 most-recent binary YES/NO markets
            binary_markets = [mx for mx in all_markets if _is_binary_yes_no(mx)]
            console.print(f"\n[bold]Binary YES/NO markets in batch:[/bold] {len(binary_markets)} / {len(all_markets)}")
            console.print("[bold]Winner resolution for up to 5 binary markets:[/bold]")
            for mx in binary_markets[:5]:
                w = _resolve_winner(mx)
                icon = "[green]✓[/green]" if w else "[red]✗[/red]"
                ltp = mx.get('lastTradePrice')
                op  = _parse_outcome_prices(mx)
                console.print(
                    f"  {icon} {(mx.get('question') or '?')[:48]:48s}"
                    f"  ltp={ltp!r:5}  prices={op}  winner={w!r}"
                )

            # 2. Test different market state filters — looking for recent resolved markets
            async def _probe_filter(label: str, params: dict):
                try:
                    rp = await gamma.get("/markets", params=params)
                    if rp.status_code != 200:
                        console.print(f"  [red]{label}: HTTP {rp.status_code}[/red]")
                        return []
                    raw_p = rp.json()
                    batch = raw_p if isinstance(raw_p, list) else raw_p.get("data", raw_p.get("markets", []))
                    binary = [m for m in batch if _is_binary_yes_no(m)]
                    # Find the most recent endDateIso
                    dates = sorted(
                        (m.get("endDateIso") or m.get("end_date_iso") or "") for m in batch
                    )
                    newest = dates[-1] if dates else "?"
                    with_trades_est = sum(1 for m in binary if _resolve_winner(m))
                    console.print(
                        f"  [cyan]{label}[/cyan]: {len(batch)} markets, "
                        f"{len(binary)} binary YES/NO, newest={newest}"
                    )
                    return binary
                except Exception as e:
                    console.print(f"  [red]{label}: error — {e}[/red]")
                    return []

            console.print("\n[bold]Probing different market state filters:[/bold]")
            binary_closed   = await _probe_filter("closed=true",   {"closed": "true",   "limit": 100})
            binary_archived = await _probe_filter("archived=true", {"archived": "true", "limit": 100})
            await _probe_filter("active=false,closed=false", {"active": "false", "closed": "false", "limit": 100})

            # Use whichever filter returned the most recent markets
            with_outcome = [m for m in binary_archived if _resolve_winner(m)]
            if not with_outcome:
                with_outcome = [m for m in binary_closed if _resolve_winner(m)]
            console.print(f"\n  Best batch: {len(with_outcome)} binary markets with resolved outcome")

            # 3. Try fetching trades — test both conditionId and id as the market key
            async def _try_trades(label: str, param_val: str):
                r3 = await data.get("/trades", params={"market": param_val, "limit": 5})
                raw3 = r3.json()
                trades = raw3 if isinstance(raw3, list) else raw3.get("data", [])
                console.print(f"  {label} → HTTP {r3.status_code}, {len(trades)} trades")
                return trades

            if with_outcome:
                mx = with_outcome[0]
                cid  = mx.get("conditionId") or mx.get("condition_id", "")
                mid  = str(mx.get("id") or "")
                slug = mx.get("slug") or ""
                console.print(f"\n[bold]Trade fetch for:[/bold] {mx.get('question','')[:60]}")
                console.print(f"  conditionId = {cid[:20]}…  id = {mid}  slug = {slug[:20]}")
                trades = await _try_trades(f"?market=conditionId", cid) or \
                         await _try_trades(f"?market=id",          mid) or \
                         await _try_trades(f"?market=slug",        slug)
                if trades:
                    t = trades[0]
                    console.print(f"\n  [bold]Sample trade fields:[/bold] {sorted(t.keys())}")
                    console.print(f"  side       = {t.get('side')!r}")
                    console.print(f"  outcome    = {t.get('outcome')!r}")
                    console.print(f"  size       = {t.get('size')}  price = {t.get('price')}")
                    notional = float(t.get("size", 0) or 0) * float(t.get("price", 0) or 0)
                    console.print(f"  notional   = ${notional:.2f}  (threshold={DETECTION.min_trade_size_usd})")
                    console.print(f"  proxyWallet = {t.get('proxyWallet') or t.get('maker')}")
            else:
                trades = []
                console.print("\n[yellow]No binary markets with resolved outcomes found in any filter.[/yellow]")

            # Find recent markets by trying high page offsets
            console.print("\n[bold]Probing page offsets to find recent closed markets:[/bold]")
            recent_market = None
            for offset_test in [500, 2000, 5000, 10000, 20000]:
                try:
                    roff = await gamma.get("/markets", params={"closed": "true", "limit": 5, "offset": offset_test})
                    if roff.status_code != 200:
                        console.print(f"  offset={offset_test}: HTTP {roff.status_code}")
                        break
                    batch_off = roff.json()
                    batch_off = batch_off if isinstance(batch_off, list) else batch_off.get("data", batch_off.get("markets", []))
                    if not batch_off:
                        console.print(f"  offset={offset_test}: empty (end of data)")
                        break
                    dates_off = sorted(m.get("endDateIso") or "" for m in batch_off if m.get("endDateIso"))
                    newest_off = dates_off[-1] if dates_off else "?"
                    binary_off = [m for m in batch_off if _is_binary_yes_no(m)]
                    console.print(f"  offset={offset_test:6d}: {len(batch_off)} markets, newest={newest_off}, binary={len(binary_off)}")
                    if newest_off >= "2024-01-01" and binary_off:
                        recent_market = binary_off[0]
                        console.print(f"  [green]Found recent market at offset {offset_test}![/green]")
                        break
                except Exception as e:
                    console.print(f"  offset={offset_test}: error — {e}")
                    break

            if recent_market:
                rcid = recent_market.get("conditionId") or ""
                rmid = str(recent_market.get("id") or "")
                console.print(f"\n[bold]Testing trades for recent market:[/bold] {recent_market.get('question','')[:60]}")
                console.print(f"  endDateIso = {recent_market.get('endDateIso')}  winner = {_resolve_winner(recent_market)!r}")
                await _try_trades("?market=conditionId", rcid)
                await _try_trades("?market=id",          rmid)

            # 4. Simulate wallet profile building for 5 markets
            console.print(f"\n[bold]Simulating wallet profile build for {min(5, len(with_outcome))} markets...[/bold]")
            from collectors.polymarket_collector import aggregate_wallet_stats

            market_trades = {}
            market_outcomes = {}
            market_close_times = {}
            market_names = {}

            for m in with_outcome[:5]:
                cid = m.get("conditionId") or m.get("condition_id", "")
                if not cid:
                    continue

                winner = _resolve_winner(m)
                market_outcomes[cid] = winner
                market_names[cid] = m.get("question") or m.get("title") or cid[:20]

                end_date = m.get("endDateIso") or m.get("end_date_iso") or ""
                try:
                    market_close_times[cid] = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                except Exception:
                    pass

                # Use numeric id — conditionId returns 0 trades on data API
                mid = str(m.get("id") or cid)
                r4 = await data.get("/trades", params={"market": mid, "limit": 100})
                raw4 = r4.json()
                market_trades[cid] = raw4 if isinstance(raw4, list) else raw4.get("data", [])

            total_raw_trades = sum(len(v) for v in market_trades.values())
            console.print(f"  Raw trades fetched: {total_raw_trades}")

            profiles = aggregate_wallet_stats(market_trades, market_outcomes, market_close_times, market_names)
            console.print(f"  Wallet profiles built: {len(profiles)}")

            if profiles:
                # Show score distribution
                scored = score_profiles(profiles)
                console.print(f"\n[bold]Score distribution (top 10):[/bold]")
                table = Table(box=box.SIMPLE, show_header=True, header_style="cyan")
                table.add_column("Wallet", width=20)
                table.add_column("Trades", width=7, justify="right")
                table.add_column("Win Rate", width=10, justify="right")
                table.add_column("Score", width=8, justify="right")
                table.add_column("Risk", width=10)
                table.add_column("Flags", width=30)
                for p in scored[:10]:
                    table.add_row(
                        (p.get("wallet") or "?")[:18],
                        str(p.get("total_trades", "?")),
                        f"{p.get('win_rate', 0):.1%}",
                        f"{p.get('composite_score', 0):.1f}",
                        p.get("risk_level", "?"),
                        ", ".join(p.get("flags", [])) or "—",
                    )
                console.print(table)

                flagged = [p for p in scored if p.get("flags")]
                console.print(f"\n[bold]Profiles with at least one flag:[/bold] {len(flagged)}")
                if not flagged:
                    console.print("[yellow]No flags fired. Showing why for the top profile:[/yellow]")
                    p = scored[0]
                    for name, res in p.get("test_results", {}).items():
                        icon = "[green]✓[/green]" if res["flagged"] else "[red]✗[/red]"
                        console.print(f"  {icon} {name:25s}  score={res['score']:5.1f}  {res['reason']}")
            else:
                console.print("[red]Zero profiles built — all trades were filtered out![/red]")
                console.print("[yellow]Likely cause: 'side' != 'BUY' or 'outcome' is empty in raw trades[/yellow]")


# ── CLI ───────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--source", default="both",
              type=click.Choice(["both", "kalshi", "polymarket"], case_sensitive=False))
def cli(source: str):
    """Diagnose why the surveillance scan produces no flagged profiles."""
    async def _run():
        if source in ("both", "kalshi"):
            await diagnose_kalshi()
            console.print()
        if source in ("both", "polymarket"):
            await diagnose_polymarket()

    asyncio.run(_run())


if __name__ == "__main__":
    cli()
