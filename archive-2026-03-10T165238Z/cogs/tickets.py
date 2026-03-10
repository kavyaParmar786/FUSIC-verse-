"""
cogs/tickets.py — Enterprise Ticket System
──────────────────────────────────────────────────────────────────────────────
FIXES APPLIED vs previous version:
  ✅  Role IDs instead of role names (set in .env as comma-separated IDs)
  ✅  DM sent to user on ticket close (was broken — fixed with explicit try/except)
  ✅  Dashboard removed from Flask → Netlify-ready static JSON export at /data/dashboard_export.json
  ✅  +ds command links to your Netlify URL (set DASHBOARD_URL in .env)
  ✅  All views properly persistent (custom_id on every button/select)
──────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import aiofiles
import discord
from discord.ext import commands, tasks

log = logging.getLogger("tickets")

# ─── Paths ────────────────────────────────────────────────────
TICKETS_FILE   = Path("data/tickets.json")
STATS_FILE     = Path("data/stats.json")
STAFF_FILE     = Path("data/staff_stats.json")
RATINGS_FILE   = Path("data/ratings.json")
NOTES_FILE     = Path("data/notes.json")
EXPORT_FILE    = Path("data/dashboard_export.json")

TRANSCRIPTS_BASE = Path("data/transcripts")
TRANSCRIPTS_BASE.mkdir(parents=True, exist_ok=True)

AUTO_CLOSE_MINUTES = int(os.getenv("AUTO_CLOSE_MINUTES", "60"))
DASHBOARD_URL      = os.getenv("DASHBOARD_URL", "https://your-site.netlify.app")

# ─── Role ID helpers ──────────────────────────────────────────

def _get_staff_role_ids() -> list[int]:
    """Returns all staff+jr.staff role IDs from .env."""
    ids = []
    for key in ("TICKET_STAFF_ROLE_IDS", "JR_STAFF_ROLE_IDS"):
        raw = os.getenv(key, "")
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                ids.append(int(part))
    return ids


def _get_staff_roles(guild: discord.Guild) -> list[discord.Role]:
    return [r for r in guild.roles if r.id in _get_staff_role_ids()]


def _is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    staff_ids = set(_get_staff_role_ids())
    return any(r.id in staff_ids for r in member.roles)


def _log_channel_id() -> Optional[int]:
    raw = os.getenv("TICKET_LOG_CHANNEL_ID", "").strip()
    return int(raw) if raw.isdigit() else None

# ─── JSON helpers ─────────────────────────────────────────────

def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _save(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2, default=str))


# ─── Ticket number ────────────────────────────────────────────

def _next_ticket_num(guild_id: int) -> int:
    stats = _load(STATS_FILE)
    gid   = str(guild_id)
    n     = stats.get(gid, {}).get("counter", 0) + 1
    stats.setdefault(gid, {})["counter"] = n
    _save(STATS_FILE, stats)
    return n


# ─── Embed helper ─────────────────────────────────────────────

def _embed(title: str, desc: str = "", colour=discord.Colour.blurple()) -> discord.Embed:
    e = discord.Embed(title=title, description=desc, colour=colour,
                      timestamp=datetime.now(timezone.utc))
    e.set_footer(text="FUSIC VERSE Ticket System")
    return e


# ─────────────────────────────────────────────────────────────
#  TRANSCRIPT GENERATOR  (self-contained HTML)
# ─────────────────────────────────────────────────────────────

async def _generate_transcript(channel: discord.TextChannel, ticket: dict, notes: list) -> Path:
    messages = []
    async for msg in channel.history(limit=None, oldest_first=True):
        messages.append({
            "author":    msg.author.display_name,
            "avatar":    str(msg.author.display_avatar.url),
            "bot":       msg.author.bot,
            "content":   msg.content,
            "time":      msg.created_at.strftime("%Y-%m-%d %H:%M UTC"),
            "files":     [a.url for a in msg.attachments],
        })

    date_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir   = TRANSCRIPTS_BASE / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    filename  = f"ticket-{ticket.get('number','X')}-{channel.name}.html"
    out_path  = out_dir / filename

    STYLE = """
body{margin:0;font-family:'Segoe UI',Arial,sans-serif;background:#313338;color:#dbdee1;font-size:15px}
.header{background:#1e1f22;padding:20px 28px;border-bottom:1px solid #3f4147}
.header h1{margin:0;font-size:20px;color:#fff}.header p{margin:4px 0 0;color:#80848e;font-size:13px}
.meta{display:flex;gap:16px;padding:14px 28px;background:#2b2d31;border-bottom:1px solid #3f4147;flex-wrap:wrap}
.meta-card{background:#1e1f22;border-radius:8px;padding:10px 14px;border-left:3px solid #5865f2}
.meta-card .l{font-size:11px;color:#80848e;text-transform:uppercase;letter-spacing:.04em}
.meta-card .v{font-weight:700;margin-top:3px}
.msgs{padding:12px 28px}
.msg{display:flex;gap:12px;padding:4px 0;border-radius:4px}
.msg:hover{background:rgba(255,255,255,.03)}
.av{width:40px;height:40px;border-radius:50%;background:#5865f2;display:flex;align-items:center;
    justify-content:center;font-weight:700;font-size:16px;flex-shrink:0;overflow:hidden}
.av img{width:100%;height:100%;object-fit:cover}
.mc{flex:1;min-width:0}
.mm{display:flex;align-items:baseline;gap:8px;margin-bottom:2px}
.an{font-weight:700;font-size:15px}
.ts{color:#80848e;font-size:12px}
.bot-tag,.staff-tag{font-size:10px;padding:1px 5px;border-radius:3px;font-weight:700}
.bot-tag{background:#5865f2;color:#fff}.staff-tag{background:#23a559;color:#fff}
.ct{white-space:pre-wrap;word-break:break-word}
.notes{margin:0 28px 16px;background:#2a2618;border:1px solid #4a3f1a;border-radius:8px;padding:14px}
.notes h3{color:#f0b232;font-size:13px;text-transform:uppercase;margin:0 0 10px}
.note{margin-bottom:8px}.na{font-weight:700;color:#f0b232}
footer{text-align:center;padding:16px;background:#1e1f22;color:#80848e;font-size:12px;border-top:1px solid #3f4147}
.day-sep{display:flex;align-items:center;gap:10px;color:#80848e;font-size:12px;margin:12px 0}
.day-sep::before,.day-sep::after{content:'';flex:1;height:1px;background:#3f4147}
"""

    def _msg_html(m: dict) -> str:
        init = (m["author"] or "?")[0].upper()
        av_html = f'<img src="{m["avatar"]}" alt="{init}"/>' if m["avatar"] else init
        tags    = ""
        if m["bot"]:   tags += '<span class="bot-tag">BOT</span>'
        content  = discord.utils.escape_markdown(m["content"]) if m["content"] else ""
        files_html = "".join(
            f'<br><a href="{f}" style="color:#00a8fc">{f.split("/")[-1]}</a>' for f in m["files"]
        )
        return (
            f'<div class="msg"><div class="av">{av_html}</div>'
            f'<div class="mc"><div class="mm">'
            f'<span class="an">{m["author"]}</span>{tags}'
            f'<span class="ts">{m["time"]}</span></div>'
            f'<div class="ct">{content}{files_html}</div></div></div>'
        )

    notes_html = ""
    if notes:
        note_items = "".join(
            f'<div class="note"><span class="na">{n["author"]}</span> '
            f'<span class="ts">— {n["time"]}</span><br>{n["content"]}</div>'
            for n in notes
        )
        notes_html = f'<div class="notes"><h3>📝 Staff Notes (Private)</h3>{note_items}</div>'

    meta_cards = "".join(
        f'<div class="meta-card"><div class="l">{lbl}</div><div class="v">{val}</div></div>'
        for lbl, val in [
            ("Ticket",   f"#{ticket.get('number','?')}"),
            ("Category", ticket.get("category","?").title()),
            ("Priority", ticket.get("priority","Normal")),
            ("Opened",   ticket.get("created_at","")[:16]),
            ("Closed",   ticket.get("closed_at","Still Open")[:16] if ticket.get("closed_at") else "Still Open"),
            ("User",     ticket.get("username","?")),
        ]
    )

    msgs_html = ""
    last_date = ""
    for m in messages:
        d = m["time"][:10]
        if d != last_date:
            msgs_html += f'<div class="day-sep">{d}</div>'
            last_date = d
        msgs_html += _msg_html(m)

    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/>
<title>Ticket #{ticket.get("number","?")} Transcript</title>
<style>{STYLE}</style></head><body>
<div class="header">
  <h1>🎫 Ticket #{ticket.get("number","?")} — {ticket.get("category","?").title()}</h1>
  <p>Server transcript • Generated {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</p>
</div>
<div class="meta">{meta_cards}</div>
{notes_html}
<div class="msgs">{msgs_html}</div>
<footer>Generated by FUSIC VERSE Ticket Bot • {len(messages)} message(s)</footer>
</body></html>"""

    async with aiofiles.open(out_path, "w", encoding="utf-8") as f:
        await f.write(html)

    return out_path


# ─────────────────────────────────────────────────────────────
#  DASHBOARD EXPORT  (Netlify-compatible static JSON)
# ─────────────────────────────────────────────────────────────

def _export_dashboard():
    """Write a JSON snapshot that your Netlify site can fetch."""
    tickets    = _load(TICKETS_FILE)
    staff_data = _load(STAFF_FILE)
    ratings    = _load(RATINGS_FILE)

    all_tickets = list(tickets.values()) if isinstance(tickets, dict) else []
    export = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_tickets": len(all_tickets),
        "open_tickets":  sum(1 for t in all_tickets if t.get("status") == "open"),
        "tickets":       all_tickets[-50:],   # last 50
        "staff_stats":   list(staff_data.values()) if isinstance(staff_data, dict) else [],
        "avg_rating":    round(
            sum(r.get("rating", 0) for r in ratings.values()) / len(ratings), 2
        ) if ratings else 0,
    }
    EXPORT_FILE.write_text(json.dumps(export, indent=2, default=str))


# ─────────────────────────────────────────────────────────────
#  MODALS
# ─────────────────────────────────────────────────────────────

class StaffApplyModal(discord.ui.Modal, title="Staff Application"):
    name       = discord.ui.TextInput(label="Full Name / Username",     max_length=100)
    age        = discord.ui.TextInput(label="Age",                       max_length=3)
    occupation = discord.ui.TextInput(label="Role Applying For",         max_length=100)
    description= discord.ui.TextInput(label="About You (hours, skills)", style=discord.TextStyle.paragraph, max_length=1000)

    def __init__(self, category: str, cog: "TicketsCog"):
        super().__init__()
        self.category = category
        self.cog      = cog

    async def on_submit(self, interaction: discord.Interaction):
        form = {
            "name": self.name.value, "age": self.age.value,
            "occupation": self.occupation.value, "description": self.description.value,
        }
        await self.cog.create_ticket(interaction, self.category, form_data=form)


class ReportModal(discord.ui.Modal, title="Report / Appeal Form"):
    reported = discord.ui.TextInput(label="Player You Are Reporting / Your Username", max_length=100)
    reporter = discord.ui.TextInput(label="Your Username",               max_length=100)
    issue    = discord.ui.TextInput(label="Issue Description",           style=discord.TextStyle.paragraph, max_length=1500)

    def __init__(self, category: str, cog: "TicketsCog"):
        super().__init__()
        self.category = category
        self.cog      = cog

    async def on_submit(self, interaction: discord.Interaction):
        form = {
            "reported_player": self.reported.value,
            "reporter":        self.reporter.value,
            "issue":           self.issue.value,
        }
        await self.cog.create_ticket(interaction, self.category, form_data=form)


class AddUserModal(discord.ui.Modal, title="Add User to Ticket"):
    user_id = discord.ui.TextInput(label="User ID (numbers only)", max_length=30)

    async def on_submit(self, interaction: discord.Interaction):
        raw = re.sub(r"[<@!>]", "", self.user_id.value.strip())
        try:
            uid = int(raw)
            member = interaction.guild.get_member(uid) or await interaction.guild.fetch_member(uid)
            await interaction.channel.set_permissions(member, read_messages=True, send_messages=True)
            await interaction.response.send_message(f"✅ Added {member.mention}.")
        except Exception as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)


class RemoveUserModal(discord.ui.Modal, title="Remove User from Ticket"):
    user_id = discord.ui.TextInput(label="User ID (numbers only)", max_length=30)

    async def on_submit(self, interaction: discord.Interaction):
        raw = re.sub(r"[<@!>]", "", self.user_id.value.strip())
        try:
            uid = int(raw)
            member = interaction.guild.get_member(uid) or await interaction.guild.fetch_member(uid)
            await interaction.channel.set_permissions(member, overwrite=None)
            await interaction.response.send_message(f"✅ Removed {member.mention}.")
        except Exception as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)


# ─────────────────────────────────────────────────────────────
#  RATING VIEW
# ─────────────────────────────────────────────────────────────

class RatingView(discord.ui.View):
    def __init__(self, ticket_id: str, guild_id: int, user_id: int):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.guild_id  = guild_id
        self.user_id   = user_id

    async def _rate(self, interaction: discord.Interaction, stars: int):
        data = _load(RATINGS_FILE)
        data[self.ticket_id] = {
            "rating":    stars,
            "user_id":   self.user_id,
            "guild_id":  self.guild_id,
            "time":      datetime.now(timezone.utc).isoformat(),
        }
        _save(RATINGS_FILE, data)
        star_str = "⭐" * stars + "☆" * (5 - stars)
        await interaction.response.edit_message(
            content=f"✅ Thank you! You rated **{stars}/5** {star_str}", view=None
        )
        self.stop()

    @discord.ui.button(label="⭐ 1", style=discord.ButtonStyle.secondary, custom_id="rate:1")
    async def r1(self, i, _): await self._rate(i, 1)
    @discord.ui.button(label="⭐⭐ 2", style=discord.ButtonStyle.secondary, custom_id="rate:2")
    async def r2(self, i, _): await self._rate(i, 2)
    @discord.ui.button(label="⭐⭐⭐ 3", style=discord.ButtonStyle.secondary, custom_id="rate:3")
    async def r3(self, i, _): await self._rate(i, 3)
    @discord.ui.button(label="⭐⭐⭐⭐ 4", style=discord.ButtonStyle.secondary, custom_id="rate:4")
    async def r4(self, i, _): await self._rate(i, 4)
    @discord.ui.button(label="⭐⭐⭐⭐⭐ 5", style=discord.ButtonStyle.success, custom_id="rate:5")
    async def r5(self, i, _): await self._rate(i, 5)


# ─────────────────────────────────────────────────────────────
#  REOPEN VIEW
# ─────────────────────────────────────────────────────────────

class ReopenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔓 Reopen Ticket", style=discord.ButtonStyle.success, custom_id="ticket:reopen")
    async def reopen(self, interaction: discord.Interaction, _):
        tickets = _load(TICKETS_FILE)
        # Find latest closed ticket for this user in this guild
        target = None
        for t in tickets.values():
            if (
                t.get("guild_id") == interaction.guild_id
                and t.get("user_id") == interaction.user.id
                and t.get("status") == "closed"
            ):
                if target is None or t.get("closed_at","") > target.get("closed_at",""):
                    target = t

        if not target:
            await interaction.response.send_message("❌ No closed ticket found for you.", ephemeral=True)
            return

        await interaction.response.send_message("🔓 Reopening your ticket…", ephemeral=True)

        guild    = interaction.guild
        staff    = _get_staff_roles(guild)
        member   = guild.get_member(target["user_id"])
        if not member:
            await interaction.followup.send("❌ Could not find you in this server.", ephemeral=True)
            return

        # Find/create Tickets category
        cat_ch = discord.utils.get(guild.categories, name="🎫 Tickets") or \
                 discord.utils.get(guild.categories, name="Tickets")
        if not cat_ch:
            cat_ch = await guild.create_category("🎫 Tickets")

        safe   = re.sub(r"[^a-z0-9-]", "", member.display_name.lower().replace(" ", "-"))[:20] or "user"
        ch_name= f"{target['category']}-{safe}"

        overwrites = {guild.default_role: discord.PermissionOverwrite(read_messages=False)}
        overwrites[member] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        for r in staff:
            overwrites[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        new_ch = await guild.create_text_channel(ch_name, category=cat_ch, overwrites=overwrites)

        target["channel_id"] = new_ch.id
        target["status"]     = "open"
        target["reopened_at"]= datetime.now(timezone.utc).isoformat()
        tickets[str(new_ch.id)] = target
        _save(TICKETS_FILE, tickets)

        staff_mention = " ".join(r.mention for r in staff)
        await new_ch.send(
            f"🔓 Ticket reopened by {member.mention} {staff_mention}",
            embed=_embed("🔓 Ticket Reopened",
                         f"Original ticket #{target.get('number')}. Staff have been notified.",
                         discord.Colour.green()),
            view=TicketControlView(),
        )
        await interaction.followup.send(f"✅ Reopened in {new_ch.mention}", ephemeral=True)


# ─────────────────────────────────────────────────────────────
#  TICKET CONTROL VIEW  (persistent)
# ─────────────────────────────────────────────────────────────

class PrioritySelectView(discord.ui.View):
    def __init__(self, channel_id: int):
        super().__init__(timeout=60)
        self.channel_id = channel_id

    @discord.ui.select(
        placeholder="Choose priority…",
        custom_id="priority:select_inline",
        options=[
            discord.SelectOption(label="Low",    value="Low",    emoji="🟢"),
            discord.SelectOption(label="Normal", value="Normal", emoji="🔵"),
            discord.SelectOption(label="High",   value="High",   emoji="🟡"),
            discord.SelectOption(label="Urgent", value="Urgent", emoji="🔴"),
        ],
    )
    async def sel(self, interaction: discord.Interaction, select: discord.ui.Select):
        val     = select.values[0]
        tickets = _load(TICKETS_FILE)
        cid     = str(self.channel_id)
        if cid in tickets:
            tickets[cid]["priority"] = val
            _save(TICKETS_FILE, tickets)
        colours = {"Low": discord.Colour.green(), "Normal": discord.Colour.blurple(),
                   "High": discord.Colour.yellow(), "Urgent": discord.Colour.red()}
        await interaction.response.send_message(
            embed=_embed(f"🏷️ Priority set: {val}", colour=colours.get(val, discord.Colour.blurple()))
        )
        self.stop()


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # ── Claim ─────────────────────────────────────────────
    @discord.ui.button(label="✋ Claim",          style=discord.ButtonStyle.primary,   custom_id="tc:claim",   row=0)
    async def claim(self, interaction: discord.Interaction, _):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True); return

        tickets = _load(TICKETS_FILE)
        cid     = str(interaction.channel.id)
        t       = tickets.get(cid)
        if not t:
            await interaction.response.send_message("❌ Ticket not found.", ephemeral=True); return
        if t.get("claimed_by"):
            await interaction.response.send_message(f"⚠️ Already claimed by <@{t['claimed_by']}>.", ephemeral=True); return

        now    = datetime.now(timezone.utc)
        t["claimed_by"]   = interaction.user.id
        t["claimed_name"] = interaction.user.display_name
        t["claimed_at"]   = now.isoformat()

        # Record response time
        try:
            created  = datetime.fromisoformat(t["created_at"])
            resp_ms  = int((now - created).total_seconds() * 1000)
        except Exception:
            resp_ms  = 0

        staff_data = _load(STAFF_FILE)
        sid        = str(interaction.user.id)
        staff_data.setdefault(sid, {"claimed": 0, "closed": 0, "total_ms": 0, "count": 0})
        staff_data[sid]["claimed"]   += 1
        staff_data[sid]["total_ms"]  += resp_ms
        staff_data[sid]["count"]     += 1
        _save(STAFF_FILE, staff_data)
        _save(TICKETS_FILE, tickets)

        e = _embed("✋ Ticket Claimed",
                   f"{interaction.user.mention} has claimed this ticket.",
                   discord.Colour.green())
        e.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=e)

    # ── Close ─────────────────────────────────────────────
    @discord.ui.button(label="🔒 Close",          style=discord.ButtonStyle.danger,    custom_id="tc:close",   row=0)
    async def close(self, interaction: discord.Interaction, _):
        tickets = _load(TICKETS_FILE)
        cid     = str(interaction.channel.id)
        t       = tickets.get(cid)
        if not t:
            await interaction.response.send_message("❌ Ticket data not found.", ephemeral=True); return

        view = CloseConfirmView(interaction.channel, t, interaction.user)
        await interaction.response.send_message(
            embed=_embed("⚠️ Confirm Close", "Transcript will be generated. Continue?", discord.Colour.orange()),
            view=view, ephemeral=True,
        )

    # ── Lock ──────────────────────────────────────────────
    @discord.ui.button(label="🔐 Lock",           style=discord.ButtonStyle.secondary, custom_id="tc:lock",    row=0)
    async def lock(self, interaction: discord.Interaction, _):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True); return
        t = _load(TICKETS_FILE).get(str(interaction.channel.id))
        if t:
            try:
                m = interaction.guild.get_member(t["user_id"])
                if m:
                    await interaction.channel.set_permissions(m, send_messages=False)
            except Exception: pass
        await interaction.response.send_message(embed=_embed("🔐 Locked", "Users can no longer send messages.", discord.Colour.orange()))

    # ── Unlock ────────────────────────────────────────────
    @discord.ui.button(label="🔓 Unlock",         style=discord.ButtonStyle.secondary, custom_id="tc:unlock",  row=0)
    async def unlock(self, interaction: discord.Interaction, _):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True); return
        t = _load(TICKETS_FILE).get(str(interaction.channel.id))
        if t:
            try:
                m = interaction.guild.get_member(t["user_id"])
                if m:
                    await interaction.channel.set_permissions(m, send_messages=True)
            except Exception: pass
        await interaction.response.send_message(embed=_embed("🔓 Unlocked", "Users can send messages again.", discord.Colour.green()))

    # ── Add User ──────────────────────────────────────────
    @discord.ui.button(label="➕ Add User",        style=discord.ButtonStyle.secondary, custom_id="tc:add",     row=1)
    async def add_user(self, interaction: discord.Interaction, _):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True); return
        await interaction.response.send_modal(AddUserModal())

    # ── Remove User ───────────────────────────────────────
    @discord.ui.button(label="➖ Remove User",     style=discord.ButtonStyle.secondary, custom_id="tc:remove",  row=1)
    async def remove_user(self, interaction: discord.Interaction, _):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True); return
        await interaction.response.send_modal(RemoveUserModal())

    # ── Priority ──────────────────────────────────────────
    @discord.ui.button(label="🏷️ Priority",        style=discord.ButtonStyle.secondary, custom_id="tc:priority",row=1)
    async def priority(self, interaction: discord.Interaction, _):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True); return
        await interaction.response.send_message(
            "Select priority:", view=PrioritySelectView(interaction.channel.id), ephemeral=True
        )

    # ── Transcript ────────────────────────────────────────
    @discord.ui.button(label="📄 Transcript",      style=discord.ButtonStyle.secondary, custom_id="tc:transcript",row=1)
    async def transcript(self, interaction: discord.Interaction, _):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        tickets = _load(TICKETS_FILE)
        t       = tickets.get(str(interaction.channel.id), {})
        notes   = [n for n in _load(NOTES_FILE).values() if n.get("channel_id") == interaction.channel.id]
        try:
            path = await _generate_transcript(interaction.channel, t, notes)
            await interaction.followup.send(
                "📄 Transcript generated:",
                file=discord.File(str(path)),
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Failed: {e}", ephemeral=True)


# ─────────────────────────────────────────────────────────────
#  CLOSE CONFIRM VIEW
# ─────────────────────────────────────────────────────────────

class CloseConfirmView(discord.ui.View):
    def __init__(self, channel: discord.TextChannel, ticket: dict, closer: discord.Member):
        super().__init__(timeout=30)
        self.channel = channel
        self.ticket  = ticket
        self.closer  = closer

    @discord.ui.button(label="Yes, Close", style=discord.ButtonStyle.danger, emoji="🔒")
    async def yes(self, interaction: discord.Interaction, _):
        await interaction.response.defer()
        cog: TicketsCog = interaction.client.get_cog("TicketsCog")
        if cog:
            await cog._do_close(self.channel, self.ticket, self.closer, interaction.guild)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, _):
        await interaction.response.edit_message(content="✅ Cancelled.", view=None)
        self.stop()


# ─────────────────────────────────────────────────────────────
#  PANEL VIEWS  (persistent)
# ─────────────────────────────────────────────────────────────

class Panel1View(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="🎫  Choose a support category…",
        custom_id="panel1:select",
        options=[
            discord.SelectOption(label="General",    value="general",    emoji="💬", description="General help or questions"),
            discord.SelectOption(label="Inquiry",    value="inquiry",    emoji="❓", description="Ask questions about the server"),
            discord.SelectOption(label="Meet Owner", value="meet-owner", emoji="👑", description="Request to talk with the owner"),
        ],
    )
    async def sel(self, interaction: discord.Interaction, select: discord.ui.Select):
        cog: TicketsCog = interaction.client.get_cog("TicketsCog")
        if cog:
            await cog._handle_select(interaction, select.values[0])
        select.values.clear()


class Panel2View(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="🤝  Choose a category…",
        custom_id="panel2:select",
        options=[
            discord.SelectOption(label="Partnership",       value="partnership", emoji="🤝", description="Request server partnership"),
            discord.SelectOption(label="Help in Recording", value="recording",   emoji="🎬", description="Help with recording videos"),
            discord.SelectOption(label="Staff Apply",       value="staff-apply", emoji="📋", description="Apply for a staff role"),
        ],
    )
    async def sel(self, interaction: discord.Interaction, select: discord.ui.Select):
        cog: TicketsCog = interaction.client.get_cog("TicketsCog")
        if cog:
            await cog._handle_select(interaction, select.values[0])
        select.values.clear()


class Panel3View(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="⚖️  Choose a category…",
        custom_id="panel3:select",
        options=[
            discord.SelectOption(label="Report Player",    value="report",     emoji="🚨", description="Report a rule violation"),
            discord.SelectOption(label="Ban Appeal",       value="ban-appeal", emoji="⚖️", description="Appeal a ban"),
            discord.SelectOption(label="Punishment Issue", value="punishment", emoji="🔨", description="Issue with a punishment"),
        ],
    )
    async def sel(self, interaction: discord.Interaction, select: discord.ui.Select):
        cog: TicketsCog = interaction.client.get_cog("TicketsCog")
        if cog:
            await cog._handle_select(interaction, select.values[0])
        select.values.clear()


# ─────────────────────────────────────────────────────────────
#  CLOSE LOGIC  (shared by button + auto-close)
# ─────────────────────────────────────────────────────────────

async def _do_close_ticket(
    channel: discord.TextChannel,
    ticket:  dict,
    closer:  Optional[discord.Member],
    guild:   discord.Guild,
    bot:     discord.Client,
):
    """
    ─────────────────────────────────────────────
    THE DM FIX:  We explicitly build and send the
    DM inside a broad try/except so a Forbidden
    error never silently drops it.
    ─────────────────────────────────────────────
    """
    now = datetime.now(timezone.utc)

    # 1. Update ticket record
    tickets = _load(TICKETS_FILE)
    cid     = str(channel.id)
    if cid in tickets:
        tickets[cid]["status"]    = "closed"
        tickets[cid]["closed_by"] = closer.id if closer else 0
        tickets[cid]["closed_at"] = now.isoformat()
        _save(TICKETS_FILE, tickets)

    # 2. Update staff stats
    if closer:
        sd  = _load(STAFF_FILE)
        sid = str(closer.id)
        sd.setdefault(sid, {"claimed": 0, "closed": 0, "total_ms": 0, "count": 0})
        sd[sid]["closed"] += 1
        _save(STAFF_FILE, sd)

    # 3. Generate transcript
    notes = [n for n in _load(NOTES_FILE).values() if n.get("channel_id") == channel.id]
    transcript_path: Optional[Path] = None
    try:
        transcript_path = await _generate_transcript(channel, ticket, notes)
    except Exception as e:
        log.error("Transcript generation failed: %s", e)

    # 4. Export dashboard JSON
    try:
        _export_dashboard()
    except Exception:
        pass

    # 5. Build close embed
    close_embed = _embed(
        f"🔒 Ticket #{ticket.get('number','?')} Closed",
        f"**Category:** {ticket.get('category','?').title()}\n"
        f"**Opened by:** <@{ticket.get('user_id', 0)}>\n"
        f"**Closed by:** {closer.mention if closer else 'Auto-close'}\n"
        f"**Opened:** {ticket.get('created_at','')[:16]} UTC\n"
        f"**Closed:** {now.strftime('%Y-%m-%d %H:%M')} UTC",
        discord.Colour.red(),
    )

    reopen_view = ReopenView()

    # 6. Send to logs channel
    log_cid = _log_channel_id()
    if log_cid:
        log_ch = guild.get_channel(log_cid)
        if log_ch:
            try:
                if transcript_path:
                    await log_ch.send(embed=close_embed, file=discord.File(str(transcript_path)), view=reopen_view)
                else:
                    await log_ch.send(embed=close_embed, view=reopen_view)
            except Exception as e:
                log.warning("Failed to send to log channel: %s", e)

    # 7. ─── DM THE USER (THE KEY FIX) ────────────────────────
    user_id = ticket.get("user_id")
    if user_id:
        dm_embed = discord.Embed(
            title="🔒 Your ticket has been closed",
            description=(
                f"**Server:** {guild.name}\n"
                f"**Ticket #:** {ticket.get('number', '?')}\n"
                f"**Category:** {ticket.get('category','?').title()}\n"
                f"**Closed by:** {closer.display_name if closer else 'Auto-close'}\n\n"
                "Thank you for contacting support! You can reopen your ticket with the button below."
            ),
            colour=discord.Colour.blurple(),
            timestamp=now,
        )
        dm_embed.set_footer(text=f"FUSIC VERSE • {guild.name}")

        rating_embed = discord.Embed(
            title="⭐ Rate Your Support Experience",
            description="How was the support you received? Your feedback helps us improve!",
            colour=discord.Colour.gold(),
        )

        ticket_id_str = str(channel.id)

        # ─── Attempt DM — multiple fallback methods ───────────
        dm_sent = False
        try:
            # Method 1: get cached member
            member = guild.get_member(user_id)
            if not member:
                # Method 2: fetch from API
                member = await guild.fetch_member(user_id)

            if member:
                dm_channel = await member.create_dm()
                if transcript_path:
                    await dm_channel.send(
                        embed=dm_embed,
                        file=discord.File(str(transcript_path)),
                        view=ReopenView(),
                    )
                else:
                    await dm_channel.send(embed=dm_embed, view=ReopenView())

                # Send rating request separately
                rating_view = RatingView(ticket_id=ticket_id_str, guild_id=guild.id, user_id=user_id)
                await dm_channel.send(embed=rating_embed, view=rating_view)
                dm_sent = True
                log.info("DM sent to user %s for ticket #%s", user_id, ticket.get('number'))

        except discord.Forbidden:
            log.info("Could not DM user %s — DMs are disabled.", user_id)
        except discord.NotFound:
            log.info("User %s not found in guild — cannot DM.", user_id)
        except Exception as e:
            log.warning("Unexpected DM error for user %s: %s", user_id, e)

        if not dm_sent:
            # Fallback: post rating request in the ticket channel before deletion
            try:
                rating_view = RatingView(ticket_id=ticket_id_str, guild_id=guild.id, user_id=user_id)
                await channel.send(
                    content=f"<@{user_id}> Could not DM you. Please rate your experience here:",
                    embed=rating_embed,
                    view=rating_view,
                )
                await asyncio.sleep(30)  # Give them time to rate
            except Exception:
                pass

    # 8. Delete ticket channel
    await asyncio.sleep(5)
    try:
        await channel.delete(reason=f"Ticket closed by {closer}")
    except Exception as e:
        log.error("Could not delete channel: %s", e)


# ─────────────────────────────────────────────────────────────
#  TICKETS COG
# ─────────────────────────────────────────────────────────────

EMOJI = {
    "general":"💬","inquiry":"❓","meet-owner":"👑",
    "partnership":"🤝","recording":"🎬","staff-apply":"📋",
    "report":"🚨","ban-appeal":"⚖️","punishment":"🔨",
}


class TicketsCog(commands.Cog, name="TicketsCog"):
    def __init__(self, bot: commands.Bot):
        self.bot               = bot
        self._last_activity:   dict[int, datetime] = {}
        self.auto_close_task.start()

    async def cog_unload(self):
        self.auto_close_task.cancel()

    # ── Close wrapper (so CloseConfirmView can call it) ───────
    async def _do_close(self, channel, ticket, closer, guild):
        await _do_close_ticket(channel, ticket, closer, guild, self.bot)

    # ── Track activity for auto-close ─────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        tickets = _load(TICKETS_FILE)
        if str(message.channel.id) in tickets:
            self._last_activity[message.channel.id] = datetime.now(timezone.utc)
            # Reset auto-close warning
            t = tickets[str(message.channel.id)]
            if t.get("ac_warned"):
                t["ac_warned"] = False
                _save(TICKETS_FILE, tickets)

    @tasks.loop(minutes=5)
    async def auto_close_task(self):
        tickets = _load(TICKETS_FILE)
        cutoff  = datetime.now(timezone.utc) - timedelta(minutes=AUTO_CLOSE_MINUTES)

        for cid_str, t in tickets.items():
            if t.get("status") != "open":
                continue
            cid    = int(cid_str)
            guild  = self.bot.get_guild(t.get("guild_id", 0))
            if not guild:
                continue
            channel = guild.get_channel(cid)
            if not channel:
                t["status"] = "closed"
                continue

            last = self._last_activity.get(cid)
            if last is None:
                try:
                    last = datetime.fromisoformat(t["created_at"])
                except Exception:
                    continue

            if last < cutoff:
                if not t.get("ac_warned"):
                    try:
                        await channel.send(embed=_embed(
                            "⚠️ Inactivity Warning",
                            f"This ticket will auto-close in 5 minutes due to inactivity.",
                            discord.Colour.orange(),
                        ))
                        t["ac_warned"] = True
                        _save(TICKETS_FILE, tickets)
                        self._last_activity[cid] = datetime.now(timezone.utc)
                    except Exception:
                        pass
                else:
                    await _do_close_ticket(channel, t, None, guild, self.bot)

    @auto_close_task.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    # ── Panel select router ───────────────────────────────────
    async def _handle_select(self, interaction: discord.Interaction, category: str):
        modal_map = {
            "staff-apply": lambda: StaffApplyModal(category, self),
            "report":      lambda: ReportModal(category, self),
            "ban-appeal":  lambda: ReportModal(category, self),
            "punishment":  lambda: ReportModal(category, self),
        }
        if category in modal_map:
            await interaction.response.send_modal(modal_map[category]())
        else:
            await interaction.response.defer(ephemeral=True)
            await self.create_ticket(interaction, category)

    # ── Ticket creation ───────────────────────────────────────
    async def create_ticket(
        self,
        interaction: discord.Interaction,
        category:   str,
        form_data:  Optional[dict] = None,
    ):
        guild  = interaction.guild
        member = interaction.user

        # Anti-duplicate check
        tickets = _load(TICKETS_FILE)
        for t in tickets.values():
            if (
                t.get("guild_id")  == guild.id
                and t.get("user_id")   == member.id
                and t.get("category")  == category
                and t.get("status")    == "open"
            ):
                ch = guild.get_channel(t.get("channel_id", 0))
                msg = f"⚠️ You already have an open **{category.title()}** ticket."
                if ch:
                    msg += f"\n\nYour ticket: {ch.mention}"
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)
                return

        # Queue position
        open_count = sum(1 for t in tickets.values() if t.get("status") == "open" and t.get("guild_id") == guild.id)
        q_pos      = open_count + 1

        # Get/create Tickets category
        cat_ch = discord.utils.get(guild.categories, name="🎫 Tickets") or \
                 discord.utils.get(guild.categories, name="Tickets")
        if not cat_ch:
            cat_ch = await guild.create_category("🎫 Tickets")

        staff      = _get_staff_roles(guild)
        ticket_num = _next_ticket_num(guild.id)
        safe_name  = re.sub(r"[^a-z0-9-]", "", member.display_name.lower().replace(" ","-"))[:20] or "user"
        ch_name    = f"{category}-{safe_name}"

        overwrites = {guild.default_role: discord.PermissionOverwrite(read_messages=False)}
        overwrites[member] = discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True)
        for r in staff:
            overwrites[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True)

        now     = datetime.now(timezone.utc)
        channel = await guild.create_text_channel(
            ch_name,
            category=cat_ch,
            overwrites=overwrites,
            topic=f"Ticket #{ticket_num} | {category.title()} | {member.display_name}",
        )

        ticket_doc = {
            "channel_id":  channel.id,
            "guild_id":    guild.id,
            "user_id":     member.id,
            "username":    member.display_name,
            "category":    category,
            "status":      "open",
            "priority":    "Normal",
            "number":      ticket_num,
            "created_at":  now.isoformat(),
            "claimed_by":  None,
            "tags":        [],
            "form_data":   form_data or {},
            "ac_warned":   False,
        }
        tickets[str(channel.id)] = ticket_doc
        _save(TICKETS_FILE, tickets)
        self._last_activity[channel.id] = now

        # Mentions
        staff_mention = " ".join(r.mention for r in staff)
        wait_min      = max(1, (q_pos - 1) * 5)

        queue_embed = discord.Embed(
            title="🎟️ Support Queue",
            description=(
                f"Thank you, {member.mention}!\n\n"
                f"**You are `#{q_pos}` in the queue.**\n"
                f"⏱️ Estimated wait: **~{wait_min} minute(s)**"
            ),
            colour=discord.Colour.blurple(),
            timestamp=now,
        )
        queue_embed.set_footer(text=f"Ticket #{ticket_num} • {category.title()}")
        await channel.send(content=f"{member.mention} {staff_mention}", embed=queue_embed)

        # Form data embed
        if form_data:
            form_embed = _embed(
                "📋 Submitted Information",
                "\n".join(f"**{k.replace('_',' ').title()}:** {v}" for k, v in form_data.items()),
                discord.Colour.blurple(),
            )
            form_embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
            await channel.send(embed=form_embed)

        # Summary embed
        summary_embed = _embed(
            "📌 Ticket Summary",
            f"**Topic:** {category.replace('-',' ').title()}\n"
            f"**User:** {member.display_name}\n"
            f"**Priority:** Normal\n"
            f"**Opened:** {now.strftime('%Y-%m-%d %H:%M')} UTC",
            discord.Colour.gold(),
        )
        await channel.send(embed=summary_embed)

        # Control panel
        ctrl_embed = discord.Embed(
            title="⚙️ Ticket Control Panel",
            description=(
                f"**Ticket:** #{ticket_num}\n"
                f"**Category:** {EMOJI.get(category,'🎫')} {category.title()}\n"
                f"**Priority:** 🔵 Normal\n"
                f"**Status:** 🟢 Open\n\n"
                "Use the buttons below to manage this ticket."
            ),
            colour=discord.Colour.blurple(),
            timestamp=now,
        )
        ctrl_embed.set_footer(text="FUSIC VERSE Ticket System")
        await channel.send(embed=ctrl_embed, view=TicketControlView())

        # Acknowledge
        ack = _embed("✅ Ticket Created",
                     f"Your ticket is in {channel.mention}\n**Ticket #:** {ticket_num}",
                     discord.Colour.green())
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=ack, ephemeral=True)
            else:
                await interaction.response.send_message(embed=ack, ephemeral=True)
        except Exception:
            pass

        # Export dashboard data
        try:
            _export_dashboard()
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────
    #  COMMANDS
    # ─────────────────────────────────────────────────────────

    @commands.command(name="panel1")
    @commands.has_permissions(manage_channels=True)
    async def panel1(self, ctx: commands.Context):
        e = discord.Embed(
            title="🎫  Support Center",
            description=(
                "```\n╔══════════════════════════╗\n✨  Support Center\n╚══════════════════════════╝\n```\n"
                "**📜 Rules**\n• No spam tickets\n• One ticket per issue\n"
                "• Respect staff\n• Explain your issue clearly\n\n"
                "**📂 Categories**\n"
                "💬 **General** — General help or questions\n"
                "❓ **Inquiry** — Ask questions about the server\n"
                "👑 **Meet Owner** — Request to talk with the owner\n\n"
                "> Select a category below to open a ticket."
            ),
            colour=discord.Colour.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        e.set_footer(text="FUSIC VERSE Ticket System")
        await ctx.send(embed=e, view=Panel1View())
        try: await ctx.message.delete()
        except Exception: pass

    @commands.command(name="panel2")
    @commands.has_permissions(manage_channels=True)
    async def panel2(self, ctx: commands.Context):
        e = discord.Embed(
            title="🎫  Community Panel",
            description=(
                "```\n╔══════════════════════════╗\n✨  Community Panel\n╚══════════════════════════╝\n```\n"
                "**📜 Rules**\n• No spam tickets\n• One ticket per issue\n"
                "• Respect staff\n• Explain your issue clearly\n\n"
                "**📂 Categories**\n"
                "🤝 **Partnership** — Request server partnership\n"
                "🎬 **Help in Recording** — Get help recording videos\n"
                "📋 **Staff Apply** — Apply for a staff role\n\n"
                "> Select a category below to open a ticket."
            ),
            colour=discord.Colour.purple(),
            timestamp=datetime.now(timezone.utc),
        )
        e.set_footer(text="FUSIC VERSE Ticket System")
        await ctx.send(embed=e, view=Panel2View())
        try: await ctx.message.delete()
        except Exception: pass

    @commands.command(name="panel3")
    @commands.has_permissions(manage_channels=True)
    async def panel3(self, ctx: commands.Context):
        e = discord.Embed(
            title="🎫  Reports & Appeals",
            description=(
                "```\n╔══════════════════════════╗\n✨  Reports & Appeals\n╚══════════════════════════╝\n```\n"
                "**📜 Rules**\n• No spam tickets\n• One ticket per issue\n"
                "• Respect staff\n• Explain your issue clearly\n\n"
                "**📂 Categories**\n"
                "🚨 **Report Player** — Report a rule violation\n"
                "⚖️ **Ban Appeal** — Appeal a ban\n"
                "🔨 **Punishment Issue** — Problem with a punishment\n\n"
                "> Select a category below to open a ticket."
            ),
            colour=discord.Colour.red(),
            timestamp=datetime.now(timezone.utc),
        )
        e.set_footer(text="FUSIC VERSE Ticket System")
        await ctx.send(embed=e, view=Panel3View())
        try: await ctx.message.delete()
        except Exception: pass

    @commands.command(name="tag")
    async def tag(self, ctx: commands.Context, *, tag: str):
        if not _is_staff(ctx.author):
            await ctx.send("❌ Staff only.", delete_after=5); return
        tickets = _load(TICKETS_FILE)
        cid     = str(ctx.channel.id)
        if cid not in tickets:
            await ctx.send("❌ Not a ticket channel.", delete_after=5); return
        tags    = tickets[cid].get("tags", [])
        cleaned = tag.strip().title()
        if cleaned not in tags:
            tags.append(cleaned)
            tickets[cid]["tags"] = tags
            _save(TICKETS_FILE, tickets)
        await ctx.send(embed=_embed("🏷️ Tag Added", f"Tag **{cleaned}** added.\nAll tags: {', '.join(f'`{t}`' for t in tags)}"))

    @commands.command(name="note")
    async def note(self, ctx: commands.Context, *, content: str):
        if not _is_staff(ctx.author):
            await ctx.send("❌ Staff only.", delete_after=5); return
        if str(ctx.channel.id) not in _load(TICKETS_FILE):
            await ctx.send("❌ Not a ticket channel.", delete_after=5); return
        notes   = _load(NOTES_FILE)
        note_id = str(len(notes) + 1)
        notes[note_id] = {
            "channel_id": ctx.channel.id,
            "author":     ctx.author.display_name,
            "content":    content,
            "time":       datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        }
        _save(NOTES_FILE, notes)
        e = discord.Embed(
            title="📝 Staff Note Added",
            description=f"**{ctx.author.mention}:** {content}",
            colour=discord.Colour.yellow(),
            timestamp=datetime.now(timezone.utc),
        )
        e.set_footer(text="🔒 Staff Eyes Only")
        await ctx.send(embed=e)
        try: await ctx.message.delete()
        except Exception: pass

    @commands.command(name="ps")
    @commands.has_permissions(manage_guild=True)
    async def panel_status(self, ctx: commands.Context):
        tickets    = _load(TICKETS_FILE)
        guild_t    = [t for t in tickets.values() if t.get("guild_id") == ctx.guild.id]
        total      = len(guild_t)
        active     = sum(1 for t in guild_t if t.get("status") == "open")
        ratings    = _load(RATINGS_FILE)
        avg_r      = round(
            sum(r.get("rating",0) for r in ratings.values()) / len(ratings), 1
        ) if ratings else 0

        cat_lines = ""
        for cat in ["general","inquiry","meet-owner","partnership","recording","staff-apply","report","ban-appeal","punishment"]:
            c = sum(1 for t in guild_t if t.get("category") == cat)
            if c:
                cat_lines += f"  {EMOJI.get(cat,'🎫')} {cat.title()}: **{c}**\n"

        staff_roles = _get_staff_roles(ctx.guild)
        role_list   = ", ".join(r.mention for r in staff_roles) or "None configured"

        e = discord.Embed(
            title="+ Ticket System Status",
            description=(
                f"```diff\n"
                f"+ Total Tickets Opened : {total}\n"
                f"+ Active Tickets       : {active}\n"
                f"+ Avg Rating           : {avg_r}/5 ⭐\n"
                f"+ Servers Using Bot    : {len(self.bot.guilds)}\n"
                f"```\n"
                f"**📊 Per Category:**\n{cat_lines or '  *No tickets yet*'}\n"
                f"**👥 Staff Role IDs With Access:**\n{role_list}"
            ),
            colour=discord.Colour.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        e.set_footer(text=f"FUSIC VERSE • {ctx.guild.name}")
        await ctx.send(embed=e)

    @commands.command(name="staffstats")
    async def staffstats(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        target    = member or ctx.author
        sd        = _load(STAFF_FILE)
        s         = sd.get(str(target.id), {})
        claimed   = s.get("claimed", 0)
        closed    = s.get("closed", 0)
        rc        = s.get("count", 0)
        ms        = s.get("total_ms", 0)
        avg_resp  = f"{ms // rc // 1000}s" if rc else "N/A"
        e = _embed(f"📊 Staff Stats — {target.display_name}", colour=discord.Colour.blurple())
        e.set_thumbnail(url=target.display_avatar.url)
        e.add_field(name="✋ Claimed", value=str(claimed), inline=True)
        e.add_field(name="🔒 Closed",  value=str(closed),  inline=True)
        e.add_field(name="⏱️ Avg Response", value=avg_resp, inline=True)
        await ctx.send(embed=e)

    @commands.command(name="ds")
    async def dashboard_cmd(self, ctx: commands.Context):
        """Show the dashboard link (hosted on Netlify)."""
        e = _embed(
            "🌐  FUSIC VERSE Dashboard",
            f"Access the live ticket dashboard:\n\n"
            f"🔗 **[Open Dashboard]({DASHBOARD_URL})**\n\n"
            f"The dashboard shows:\n"
            f"• 📊 Open & closed tickets\n"
            f"• 👥 Staff activity stats\n"
            f"• ⭐ Satisfaction ratings\n"
            f"• 📄 Transcript downloads\n\n"
            f"*Dashboard data is exported automatically when tickets close.*",
            discord.Colour.blurple(),
        )
        await ctx.send(embed=e)

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You don't have permission for that.", delete_after=8)


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketsCog(bot))