"""
FastAPI backend for the Prediction Market Surveillance Dashboard.

Reads JSON report files from the reports/ directory — no database needed.

On startup the server automatically kicks off an initial scan if no reports
exist yet, so the user never has to run a CLI command manually.

A POST /api/scan endpoint lets the dashboard trigger new scans on demand.

Run:
    uvicorn server:app --reload --port 8000

In production, uncomment the StaticFiles mount at the bottom to serve the
built React app from web/dist/ on the same port.
"""

import asyncio
import glob
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware

# ── Scan state ────────────────────────────────────────────────────────────────

_scan_info: dict = {
    "running":        False,
    "started_at":     None,
    "last_completed": None,
    "error":          None,
    "source":         None,
    "days_back":      None,
    "progress":       None,    # free-text status line
}

REPORTS_DIR = Path("reports")


def _report_files() -> list[str]:
    return sorted(
        glob.glob(str(REPORTS_DIR / "surveillance_report_*.json")),
        reverse=True,
    )


def _read_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


async def _do_scan(days_back: int = 30, source: str = "polymarket"):
    """Run a full surveillance scan in the background."""
    if _scan_info["running"]:
        return

    # Add project root so we can import main.run_surveillance
    project_root = str(Path(__file__).parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from main import run_surveillance  # noqa: PLC0415

    _scan_info.update(
        running=True,
        started_at=datetime.now(timezone.utc).isoformat(),
        error=None,
        source=source,
        days_back=days_back,
        progress=f"Starting {source} scan for last {days_back} days…",
    )

    try:
        await run_surveillance(days_back=days_back, source=source, export=True, top_n=20)
        _scan_info["last_completed"] = datetime.now(timezone.utc).isoformat()
        _scan_info["progress"] = "Completed"
    except SystemExit:
        # run_surveillance calls sys.exit(1) when no data is collected
        _scan_info["error"] = "Scan returned no data — check API connectivity."
        _scan_info["progress"] = "Failed"
    except Exception as e:
        _scan_info["error"] = str(e)
        _scan_info["progress"] = "Failed"
    finally:
        _scan_info["running"] = False


_watch_proc: Optional[asyncio.subprocess.Process] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """On startup: launch the live watch monitor and kick off an initial scan if needed."""
    global _watch_proc

    # Always start the live monitor (writes to reports/live_alerts.json every poll)
    project_root = Path(__file__).parent
    _watch_proc = await asyncio.create_subprocess_exec(
        sys.executable, str(project_root / "main.py"), "watch",
        "--source", "polymarket",
        "--export", str(REPORTS_DIR / "live_alerts.json"),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=str(project_root),
    )

    # Auto-scan if no reports exist yet
    if not _report_files():
        asyncio.create_task(_do_scan(days_back=30, source="polymarket"))

    yield

    # Shutdown: stop the watch process
    if _watch_proc and _watch_proc.returncode is None:
        _watch_proc.terminate()
        try:
            await asyncio.wait_for(_watch_proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            _watch_proc.kill()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Surveillance Dashboard API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── helpers ───────────────────────────────────────────────────────────────────

# (already defined above: _report_files, _read_json)


# ── Scan endpoints ────────────────────────────────────────────────────────────

@app.get("/api/scan/status")
async def get_scan_status():
    """Return the current scan state (running / last completed / error)."""
    return {**_scan_info, "report_count": len(_report_files())}


@app.post("/api/scan")
async def trigger_scan(
    background_tasks: BackgroundTasks,
    days_back: int = 30,
    source: str = "polymarket",
):
    """Kick off a new scan. Returns immediately; poll /api/scan/status for progress."""
    if _scan_info["running"]:
        raise HTTPException(status_code=409, detail="A scan is already in progress.")
    if source not in ("polymarket", "kalshi", "both"):
        raise HTTPException(status_code=400, detail="source must be polymarket, kalshi, or both")
    background_tasks.add_task(_do_scan, days_back=days_back, source=source)
    return {"status": "started", "days_back": days_back, "source": source}


# ── Report endpoints ──────────────────────────────────────────────────────────

@app.get("/api/reports")
async def list_reports():
    """Return metadata for all scan reports, newest first."""
    result = []
    for filepath in _report_files():
        try:
            data = _read_json(Path(filepath))
            meta = data.get("metadata", {})
            flagged = data.get("flagged_profiles", [])
            result.append({
                "filename":       os.path.basename(filepath),
                "run_time":       meta.get("run_time"),
                "days_back":      meta.get("days_back"),
                "source":         meta.get("source"),
                "total_markets":  meta.get("total_markets"),
                "total_trades":   meta.get("total_trades"),
                "total_profiles": meta.get("total_profiles"),
                "flagged_count":  len(flagged),
                "critical":       sum(1 for p in flagged if p.get("risk_level") == "CRITICAL"),
                "high":           sum(1 for p in flagged if p.get("risk_level") == "HIGH"),
                "medium":         sum(1 for p in flagged if p.get("risk_level") == "MEDIUM"),
            })
        except Exception:
            continue
    return result


@app.get("/api/reports/latest")
async def get_latest_report():
    """Return the most recent scan report in full."""
    files = _report_files()
    if not files:
        raise HTTPException(status_code=404, detail="No reports found")
    return _read_json(Path(files[0]))


@app.get("/api/reports/{filename}")
async def get_report(
    filename: str,
    flagged_only: bool = False,
    min_score: float = 0.0,
    source: Optional[str] = None,
):
    """Return a specific report, with optional server-side filtering."""
    if not filename.startswith("surveillance_report_") or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = REPORTS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report not found")

    data = _read_json(path)

    def _filter(profiles: list) -> list:
        out = profiles
        if min_score > 0:
            out = [p for p in out if p.get("composite_score", 0) >= min_score]
        if source:
            out = [p for p in out if p.get("source", "") == source]
        return out

    data["flagged_profiles"] = _filter(data.get("flagged_profiles", []))
    if flagged_only:
        data["all_profiles"] = []
    else:
        data["all_profiles"] = _filter(data.get("all_profiles", []))

    return data


# ── Live alerts endpoint ──────────────────────────────────────────────────────

@app.get("/api/live")
async def get_live_alerts():
    """Return the current live_alerts.json produced by the watch command."""
    path = REPORTS_DIR / "live_alerts.json"
    if not path.exists():
        return {"alerts": [], "last_updated": None, "poll_count": 0, "markets_tracked": 0}
    data = _read_json(path)
    # Handle legacy array format written by older versions of the watch command
    if isinstance(data, list):
        return {"alerts": data, "last_updated": None, "poll_count": 0, "markets_tracked": 0}
    return data


# ── Stats endpoint ────────────────────────────────────────────────────────────

@app.get("/api/stats")
async def get_stats():
    """Return a top-level summary used in the dashboard header."""
    files = _report_files()
    latest_meta: dict = {}
    by_risk = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    total_flagged = 0

    if files:
        try:
            data = _read_json(Path(files[0]))
            latest_meta = data.get("metadata", {})
            for p in data.get("flagged_profiles", []):
                level = p.get("risk_level", "LOW")
                by_risk[level] = by_risk.get(level, 0) + 1
                total_flagged += 1
        except Exception:
            pass

    live_count = 0
    live_path = REPORTS_DIR / "live_alerts.json"
    if live_path.exists():
        try:
            live_count = len(_read_json(live_path))
        except Exception:
            pass

    return {
        "report_count":     len(files),
        "latest_run_time":  latest_meta.get("run_time"),
        "days_back":        latest_meta.get("days_back"),
        "total_profiles":   latest_meta.get("total_profiles", 0),
        "total_flagged":    total_flagged,
        "by_risk_level":    by_risk,
        "live_alert_count": live_count,
        "total_trades":     latest_meta.get("total_trades", 0),
        "total_markets":    latest_meta.get("total_markets", 0),
    }


# ── production static file serving ────────────────────────────────────────────
# Uncomment once you have run `cd web && npm run build`:
#
# from fastapi.staticfiles import StaticFiles
# app.mount("/", StaticFiles(directory="web/dist", html=True), name="static")
