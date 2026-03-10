"""
main.py — FUSIC VERSE Bot Entry Point
─────────────────────────────────────────────────────────────
Loads all cogs, registers persistent views, reads token from .env
"""

import asyncio
import logging
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fusicbot")

# ── Create data dirs ─────────────────────────────────────────
for d in ("data", "data/playlists", "data/transcripts"):
    Path(d).mkdir(parents=True, exist_ok=True)

# ── Initialise JSON files if missing ────────────────────────
import json

def _init_json(path: str, default):
    p = Path(path)
    if not p.exists():
        p.write_text(json.dumps(default, indent=2))

_init_json("data/warnings.json",   {})
_init_json("data/tickets.json",    {})
_init_json("data/settings.json",   {})
_init_json("data/autoplay.json",   {})

TOKEN  = os.getenv("DISCORD_TOKEN", "")
PREFIX = os.getenv("PREFIX", "+")

COGS = [
    "cogs.info",
    "cogs.moderation",
    "cogs.music",
    "cogs.tickets",
]


class FusicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members          = True
        intents.guilds           = True
        intents.voice_states     = True

        super().__init__(
            command_prefix=commands.when_mentioned_or(PREFIX),
            intents=intents,
            help_command=None,
            case_insensitive=True,
            strip_after_prefix=True,
        )

    async def setup_hook(self):
        log.info("Loading cogs…")
        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info("  ✅  %s", cog)
            except Exception as e:
                log.error("  ❌  %s — %s", cog, e, exc_info=True)

        # ── Register ALL persistent views here so they survive restarts ──
        # Tickets
        from cogs.tickets import (
            Panel1View, Panel2View, Panel3View,
            TicketControlView, ReopenView, RatingView,
        )
        self.add_view(Panel1View())
        self.add_view(Panel2View())
        self.add_view(Panel3View())
        self.add_view(TicketControlView())
        self.add_view(ReopenView())
        self.add_view(RatingView(ticket_id="", guild_id=0, user_id=0))

        # Moderation panel
        from cogs.moderation import ModPanelView
        self.add_view(ModPanelView())

        # Music panel
        from cogs.music import MusicPanelView
        self.add_view(MusicPanelView())

        log.info("All persistent views registered.")

    async def on_ready(self):
        log.info("=" * 55)
        log.info("  🤖  %s (ID %s)", self.user, self.user.id)
        log.info("  📡  %d guild(s)  |  prefix: %s", len(self.guilds), PREFIX)
        log.info("=" * 55)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="🚀 FUSE OS • Next-Gen Discord System",
            )
        )

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You don't have permission for that.", delete_after=8)
            return
        if isinstance(error, commands.BotMissingPermissions):
            await ctx.send(f"❌ I'm missing permissions: `{error.missing_permissions}`", delete_after=8)
            return
        log.error("Command error: %s", error, exc_info=error)


async def main():
    if not TOKEN:
        log.critical("DISCORD_TOKEN not set in .env!")
        return
    bot = FusicBot()
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())