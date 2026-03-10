"""
cogs/dashboard.py
────────────────────────────────────────────────────────────
Enterprise Ticket Bot — Flask Web Dashboard Cog
Runs a Flask server in a background thread alongside the bot.
Serves live ticket data, staff stats, and transcript viewer.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

import discord
from discord.ext import commands
from flask import Flask, render_template, jsonify, send_file, abort
from flask_cors import CORS

from database import db

log = logging.getLogger("ticketbot.dashboard")

# ─── Flask app ────────────────────────────────────────────
flask_app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent.parent / "templates"),
    static_folder=str(Path(__file__).parent.parent / "static"),
)
flask_app.secret_key = os.getenv("FLASK_SECRET_KEY", "changeme-in-production")
CORS(flask_app)

# ─── Shared bot reference ─────────────────────────────────
_bot_ref: discord.Client | None = None


def _run_async(coro):
    """Run an async coroutine from sync Flask context."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ══════════════════════════════════════════════════════════
#  FLASK ROUTES
# ══════════════════════════════════════════════════════════

@flask_app.route("/")
def index():
    """Main dashboard page."""
    tickets      = _run_async(db.find_all("tickets")) or []
    staff_stats  = _run_async(db.find_all("staff_stats")) or []
    ratings      = _run_async(db.find_all("ratings")) or []

    total  = len(tickets)
    open_t = sum(1 for t in tickets if t.get("status") == "open")

    today = datetime.now(timezone.utc).date()
    closed_today = sum(
        1 for t in tickets
        if t.get("status") == "closed" and
        t.get("closed_at", "")[:10] == str(today)
    )

    avg_rating = round(
        sum(r.get("rating", 0) for r in ratings) / len(ratings), 1
    ) if ratings else 0.0

    servers = len(_bot_ref.guilds) if _bot_ref else 0
    staff_count = len(staff_stats)

    # Category counts
    category_counts: dict[str, int] = {}
    for t in tickets:
        cat = t.get("category", "unknown")
        category_counts[cat] = category_counts.get(cat, 0) + 1
    category_labels = list(category_counts.keys())
    category_data   = list(category_counts.values())

    # Weekly ticket counts (last 7 days)
    week_labels = []
    week_data   = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        label = day.strftime("%a")
        count = sum(
            1 for t in tickets
            if t.get("created_at", "")[:10] == str(day)
        )
        week_labels.append(label)
        week_data.append(count)

    # Enrich staff stats with display name
    enriched_staff = []
    for s in staff_stats:
        rc = s.get("response_count", 0)
        ms = s.get("total_response_ms", 0)
        enriched_staff.append({
            **s,
            "name":         s.get("staff_id"),  # resolved in template if possible
            "avg_response": f"{ms // rc // 1000}s" if rc > 0 else "—",
        })
    enriched_staff.sort(key=lambda x: x.get("claimed", 0), reverse=True)

    return render_template(
        "dashboard.html",
        tickets=sorted(tickets, key=lambda x: x.get("created_at",""), reverse=True)[:50],
        staff_stats=enriched_staff[:20],
        stats={
            "total":        total,
            "open":         open_t,
            "closed_today": closed_today,
            "avg_rating":   avg_rating,
            "servers":      servers,
            "staff_count":  staff_count,
        },
        category_labels=category_labels,
        category_data=category_data,
        week_labels=week_labels,
        week_data=week_data,
    )


@flask_app.route("/tickets")
def tickets_page():
    """All tickets list as JSON (for AJAX or external use)."""
    tickets = _run_async(db.find_all("tickets")) or []
    # Sanitize for JSON
    safe = []
    for t in tickets:
        t.pop("_id", None)
        safe.append(t)
    return jsonify({"tickets": safe, "total": len(safe)})


@flask_app.route("/staff")
def staff_page():
    """Staff stats as JSON."""
    stats = _run_async(db.find_all("staff_stats")) or []
    for s in stats:
        s.pop("_id", None)
    return jsonify({"staff": stats})


@flask_app.route("/transcripts")
def transcripts_page():
    """List available transcript files."""
    trans_dir = Path("data/transcripts")
    files = []
    if trans_dir.exists():
        for day_dir in sorted(trans_dir.iterdir(), reverse=True):
            if day_dir.is_dir():
                for f in day_dir.glob("*.html"):
                    files.append({
                        "name": f.name,
                        "date": day_dir.name,
                        "size": f.stat().st_size,
                        "url":  f"/transcripts/{day_dir.name}/{f.name}",
                    })
    return jsonify({"transcripts": files, "total": len(files)})


@flask_app.route("/transcripts/<date>/<filename>")
def serve_transcript(date: str, filename: str):
    """Serve a transcript HTML file."""
    path = Path("data/transcripts") / date / filename
    if not path.exists() or not path.is_file():
        abort(404)
    return send_file(str(path))


@flask_app.route("/servers")
def servers_page():
    """Server list as JSON."""
    if not _bot_ref:
        return jsonify({"servers": []})
    guilds = [
        {"id": g.id, "name": g.name, "members": g.member_count}
        for g in _bot_ref.guilds
    ]
    return jsonify({"servers": guilds})


@flask_app.route("/api/stats")
def api_stats():
    """Quick stats API endpoint."""
    tickets = _run_async(db.find_all("tickets")) or []
    return jsonify({
        "total":  len(tickets),
        "open":   sum(1 for t in tickets if t.get("status") == "open"),
        "closed": sum(1 for t in tickets if t.get("status") == "closed"),
    })


# ══════════════════════════════════════════════════════════
#  DASHBOARD COG
# ══════════════════════════════════════════════════════════

class DashboardCog(commands.Cog, name="DashboardCog"):
    """Manages the Flask web dashboard in a background thread."""

    def __init__(self, bot: commands.Bot):
        self.bot  = bot
        self._thread: threading.Thread | None = None
        global _bot_ref
        _bot_ref = bot

    async def cog_load(self):
        self._start_dashboard()

    def _start_dashboard(self):
        port = int(os.getenv("DASHBOARD_PORT", "5000"))

        def run():
            log.info("🌐  Web dashboard starting on port %d", port)
            flask_app.run(
                host="0.0.0.0",
                port=port,
                debug=False,
                use_reloader=False,
                threaded=True,
            )

        self._thread = threading.Thread(target=run, daemon=True, name="FlaskDashboard")
        self._thread.start()
        log.info("✅  Dashboard thread started.")

    async def cog_unload(self):
        log.info("DashboardCog unloaded.")


async def setup(bot: commands.Bot):
    await bot.add_cog(DashboardCog(bot))
    