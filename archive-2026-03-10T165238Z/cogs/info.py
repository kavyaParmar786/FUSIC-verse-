"""
cogs/info.py — Info Commands
+ao  +as  +ayt  +help
"""

import os
from datetime import datetime, timezone

import discord
from discord.ext import commands


def _embed(title: str, desc: str = "", colour=discord.Colour.blurple()) -> discord.Embed:
    e = discord.Embed(title=title, description=desc, colour=colour,
                      timestamp=datetime.now(timezone.utc))
    e.set_footer(text="FUSIC VERSE Bot")
    return e


class YouTubeView(discord.ui.View):
    """Persistent button linking to the YouTube channel."""
    def __init__(self):
        super().__init__(timeout=None)
        url  = os.getenv("YOUTUBE_URL", "https://youtube.com")
        name = os.getenv("YOUTUBE_NAME", "FUSIC VERSE")
        self.add_item(discord.ui.Button(
            label=f"🎬 Visit {name}",
            url=url,
            style=discord.ButtonStyle.link,
        ))


class InfoCog(commands.Cog, name="Info"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── +ao ──────────────────────────────────────────────────
    @commands.command(name="ao", aliases=["aboutowner"])
    async def about_owner(self, ctx: commands.Context):
        """About the bot owner."""
        owner_name = os.getenv("OWNER_NAME", "Unknown")
        owner_id   = os.getenv("OWNER_ID", "0")

        e = _embed(
            "👑  About the Owner",
            colour=discord.Colour.gold(),
        )
        e.add_field(name="Name",       value=owner_name, inline=True)
        e.add_field(name="Discord ID", value=f"`{owner_id}`", inline=True)
        e.add_field(
            name="Role",
            value="Founder & Developer of FUSIC VERSE",
            inline=False,
        )
        e.add_field(
            name="About",
            value=(
                "The owner is the founder of FUSIC VERSE — a community "
                "built around music, creativity, and good vibes. "
                "Feel free to open a ticket if you need to reach them!"
            ),
            inline=False,
        )
        # Try to set avatar from owner user object
        try:
            owner = await self.bot.fetch_user(int(owner_id))
            e.set_thumbnail(url=owner.display_avatar.url)
        except Exception:
            pass

        await ctx.send(embed=e)

    # ── +as ──────────────────────────────────────────────────
    @commands.command(name="as", aliases=["aboutserver"])
    async def about_server(self, ctx: commands.Context):
        """About this server."""
        g = ctx.guild
        if not g:
            await ctx.send("This command must be used in a server.")
            return

        e = _embed(
            f"🎵  About {g.name}",
            colour=discord.Colour.purple(),
        )
        if g.icon:
            e.set_thumbnail(url=g.icon.url)
        if g.banner:
            e.set_image(url=g.banner.url)

        e.add_field(name="Owner",      value=f"<@{g.owner_id}>",        inline=True)
        e.add_field(name="Members",    value=f"{g.member_count:,}",      inline=True)
        e.add_field(name="Created",    value=g.created_at.strftime("%d %b %Y"), inline=True)
        e.add_field(name="Channels",   value=str(len(g.channels)),       inline=True)
        e.add_field(name="Roles",      value=str(len(g.roles)),          inline=True)
        e.add_field(name="Boosts",     value=str(g.premium_subscription_count), inline=True)

        desc_text = g.description or "A community built around music and creativity."
        e.add_field(name="Description", value=desc_text, inline=False)

        await ctx.send(embed=e)

    # ── +ayt ─────────────────────────────────────────────────
    @commands.command(name="ayt", aliases=["youtube", "yt"])
    async def about_youtube(self, ctx: commands.Context):
        """About the YouTube channel."""
        yt_name = os.getenv("YOUTUBE_NAME", "FUSIC VERSE")
        yt_url  = os.getenv("YOUTUBE_URL", "https://youtube.com")

        e = _embed(
            f"🎬  {yt_name} on YouTube",
            f"Check out our YouTube channel for exclusive music, beats, and content!\n\n"
            f"🔗 **{yt_url}**\n\n"
            "Hit the button below to visit the channel and don't forget to **Subscribe**! 🔔",
            colour=discord.Colour.red(),
        )
        e.set_thumbnail(url="https://www.youtube.com/favicon.ico")
        await ctx.send(embed=e, view=YouTubeView())

    # ── +help ─────────────────────────────────────────────────
    @commands.command(name="help")
    async def help_cmd(self, ctx: commands.Context):
        """Show all commands."""
        p = "+"
        e = discord.Embed(
            title="📖  FUSIC VERSE Bot — Help",
            colour=discord.Colour.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        e.set_footer(text=f"Prefix: {p}")

        e.add_field(name="ℹ️  Info", value=(
            f"`{p}ao` About Owner\n"
            f"`{p}as` About Server\n"
            f"`{p}ayt` YouTube Channel"
        ), inline=True)

        e.add_field(name="🛡️  Moderation", value=(
            f"`{p}ban` `{p}unban` `{p}kick`\n"
            f"`{p}timeout` `{p}clear` `{p}warn`\n"
            f"`{p}warnings` `{p}mute` `{p}unmute`\n"
            f"`{p}lock` `{p}unlock` `{p}slowmode`\n"
            f"`{p}nick` `{p}role` `{p}modpanel`"
        ), inline=True)

        e.add_field(name="🎵  Music", value=(
            f"`{p}play` `{p}pause` `{p}resume`\n"
            f"`{p}skip` `{p}stop` `{p}leave`\n"
            f"`{p}queue` `{p}clearqueue` `{p}loop`\n"
            f"`{p}volume` `{p}nowplaying`\n"
            f"`{p}lyrics` `{p}autoplay` `{p}247`\n"
            f"`{p}playlist` `{p}playlistadd` `{p}playlistplay`\n"
            f"`{p}musicpanel`"
        ), inline=True)

        e.add_field(name="🎫  Tickets", value=(
            f"`{p}panel1` `{p}panel2` `{p}panel3`\n"
            f"`{p}tag` `{p}note`\n"
            f"`{p}ps` `{p}staffstats`"
        ), inline=True)

        await ctx.send(embed=e)


async def setup(bot: commands.Bot):
    await bot.add_cog(InfoCog(bot))