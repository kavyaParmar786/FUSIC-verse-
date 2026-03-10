"""
cogs/moderation.py — Full Moderation System
Commands: +ban +unban +kick +timeout +clear +warn +warnings
          +mute +unmute +lock +unlock +slowmode +nick +role
Panel:    +modpanel (persistent dropdown)
Warnings saved to data/warnings.json
"""

import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

WARNINGS_FILE = Path("data/warnings.json")


# ── JSON helpers ─────────────────────────────────────────────
def _load_warnings() -> dict:
    try:
        return json.loads(WARNINGS_FILE.read_text())
    except Exception:
        return {}


def _save_warnings(data: dict):
    WARNINGS_FILE.write_text(json.dumps(data, indent=2))


# ── Embed helper ─────────────────────────────────────────────
def _embed(title: str, desc: str = "", colour=discord.Colour.red()) -> discord.Embed:
    e = discord.Embed(title=title, description=desc, colour=colour,
                      timestamp=datetime.now(timezone.utc))
    e.set_footer(text="FUSIC VERSE Moderation")
    return e


def _ok(msg: str) -> discord.Embed:
    return _embed("✅  Action Completed", msg, discord.Colour.green())


def _err(msg: str) -> discord.Embed:
    return _embed("❌  Error", msg, discord.Colour.red())


# ─────────────────────────────────────────────────────────────
#  MODALS
# ─────────────────────────────────────────────────────────────

class BanModal(discord.ui.Modal, title="Ban a User"):
    user_id = discord.ui.TextInput(label="User ID", placeholder="Discord User ID", max_length=30)
    reason  = discord.ui.TextInput(label="Reason", placeholder="Reason for ban", style=discord.TextStyle.paragraph, required=False)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = int(re.sub(r"[<@!>]", "", self.user_id.value.strip()))
            member = interaction.guild.get_member(uid) or await interaction.guild.fetch_member(uid)
            reason_text = self.reason.value or "No reason provided"
            await member.ban(reason=f"{interaction.user}: {reason_text}")
            await interaction.followup.send(embed=_ok(f"Banned {member.mention}\n**Reason:** {reason_text}"), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=_err(str(e)), ephemeral=True)


class KickModal(discord.ui.Modal, title="Kick a User"):
    user_id = discord.ui.TextInput(label="User ID", placeholder="Discord User ID", max_length=30)
    reason  = discord.ui.TextInput(label="Reason", placeholder="Reason for kick", style=discord.TextStyle.paragraph, required=False)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = int(re.sub(r"[<@!>]", "", self.user_id.value.strip()))
            member = interaction.guild.get_member(uid) or await interaction.guild.fetch_member(uid)
            reason_text = self.reason.value or "No reason provided"
            await member.kick(reason=f"{interaction.user}: {reason_text}")
            await interaction.followup.send(embed=_ok(f"Kicked {member.mention}\n**Reason:** {reason_text}"), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=_err(str(e)), ephemeral=True)


class TimeoutModal(discord.ui.Modal, title="Timeout a User"):
    user_id  = discord.ui.TextInput(label="User ID", placeholder="Discord User ID", max_length=30)
    duration = discord.ui.TextInput(label="Duration (minutes)", placeholder="e.g. 10", max_length=10)
    reason   = discord.ui.TextInput(label="Reason", placeholder="Reason", style=discord.TextStyle.paragraph, required=False)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = int(re.sub(r"[<@!>]", "", self.user_id.value.strip()))
            member = interaction.guild.get_member(uid) or await interaction.guild.fetch_member(uid)
            mins   = int(self.duration.value.strip())
            reason_text = self.reason.value or "No reason provided"
            until = datetime.now(timezone.utc) + timedelta(minutes=mins)
            await member.timeout(until, reason=f"{interaction.user}: {reason_text}")
            await interaction.followup.send(
                embed=_ok(f"Timed out {member.mention} for **{mins}m**\n**Reason:** {reason_text}"), ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(embed=_err(str(e)), ephemeral=True)


# ─────────────────────────────────────────────────────────────
#  MODPANEL VIEW  (persistent)
# ─────────────────────────────────────────────────────────────

class ModPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="⚙️  Choose a moderation action…",
        custom_id="modpanel:select",
        options=[
            discord.SelectOption(label="Ban User",       value="ban",       emoji="🔨", description="Permanently ban a user"),
            discord.SelectOption(label="Kick User",      value="kick",      emoji="👟", description="Kick a user from the server"),
            discord.SelectOption(label="Timeout User",   value="timeout",   emoji="⏱️", description="Temporarily mute a user"),
            discord.SelectOption(label="Clear Messages", value="clear",     emoji="🗑️", description="Usage: +clear <amount>"),
            discord.SelectOption(label="Warn User",      value="warn",      emoji="⚠️", description="Usage: +warn @user <reason>"),
            discord.SelectOption(label="View Warnings",  value="warnings",  emoji="📋", description="Usage: +warnings @user"),
            discord.SelectOption(label="Lock Channel",   value="lock",      emoji="🔒", description="Usage: +lock"),
            discord.SelectOption(label="Unlock Channel", value="unlock",    emoji="🔓", description="Usage: +unlock"),
            discord.SelectOption(label="Slowmode",       value="slowmode",  emoji="🐢", description="Usage: +slowmode <seconds>"),
            discord.SelectOption(label="Change Nickname", value="nick",     emoji="✏️", description="Usage: +nick @user <nickname>"),
            discord.SelectOption(label="Add/Remove Role", value="role",     emoji="🏷️", description="Usage: +role @user @role"),
        ],
    )
    async def panel_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        val = select.values[0]
        select.values.clear()

        modal_map = {"ban": BanModal, "kick": KickModal, "timeout": TimeoutModal}
        if val in modal_map:
            await interaction.response.send_modal(modal_map[val]())
            return

        usage = {
            "clear":    "**+clear <amount>**\nExample: `+clear 10`",
            "warn":     "**+warn @user <reason>**\nExample: `+warn @Sparky spamming`",
            "warnings": "**+warnings @user**\nExample: `+warnings @Sparky`",
            "lock":     "**+lock**\nLocks the current channel.",
            "unlock":   "**+unlock**\nUnlocks the current channel.",
            "slowmode": "**+slowmode <seconds>**\nExample: `+slowmode 5`",
            "nick":     "**+nick @user <nickname>**\nExample: `+nick @Sparky CoolName`",
            "role":     "**+role @user @role**\nExample: `+role @Sparky @DJ`",
        }
        e = _embed("⚙️  Command Usage", usage.get(val, "Unknown"), discord.Colour.blurple())
        await interaction.response.send_message(embed=e, ephemeral=True)


# ─────────────────────────────────────────────────────────────
#  MODERATION COG
# ─────────────────────────────────────────────────────────────

class ModerationCog(commands.Cog, name="Moderation"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _check_staff(self, ctx: commands.Context) -> bool:
        return (
            ctx.author.guild_permissions.ban_members
            or ctx.author.guild_permissions.kick_members
            or ctx.author.guild_permissions.administrator
        )

    # ── +modpanel ─────────────────────────────────────────────
    @commands.command(name="modpanel")
    @commands.has_permissions(manage_guild=True)
    async def modpanel(self, ctx: commands.Context):
        """Send the moderation control panel."""
        e = discord.Embed(
            title="```\n⚙️  Moderation Control Panel\n```",
            description=(
                "Select an action from the dropdown below.\n"
                "Some actions will open a form — others will show command usage.\n\n"
                "```diff\n"
                "+ Ban / Kick / Timeout  →  opens a modal form\n"
                "+ All other actions     →  shows command usage\n"
                "```"
            ),
            colour=discord.Colour.red(),
            timestamp=datetime.now(timezone.utc),
        )
        e.set_footer(text="FUSIC VERSE Moderation Panel • Persistent")
        await ctx.send(embed=e, view=ModPanelView())
        try:
            await ctx.message.delete()
        except Exception:
            pass

    # ── +ban ──────────────────────────────────────────────────
    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        await member.ban(reason=f"{ctx.author}: {reason}")
        await ctx.send(embed=_ok(f"Banned **{member}**\n**Reason:** {reason}"))

    # ── +unban ────────────────────────────────────────────────
    @commands.command(name="unban")
    @commands.has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, user_id: int):
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user)
            await ctx.send(embed=_ok(f"Unbanned **{user}**"))
        except Exception as e:
            await ctx.send(embed=_err(str(e)))

    # ── +kick ─────────────────────────────────────────────────
    @commands.command(name="kick")
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        await member.kick(reason=f"{ctx.author}: {reason}")
        await ctx.send(embed=_ok(f"Kicked **{member}**\n**Reason:** {reason}"))

    # ── +timeout ──────────────────────────────────────────────
    @commands.command(name="timeout")
    @commands.has_permissions(moderate_members=True)
    async def timeout(self, ctx: commands.Context, member: discord.Member, minutes: int = 10, *, reason: str = "No reason"):
        until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        await member.timeout(until, reason=f"{ctx.author}: {reason}")
        await ctx.send(embed=_ok(f"Timed out **{member}** for **{minutes}m**\n**Reason:** {reason}"))

    # ── +clear ────────────────────────────────────────────────
    @commands.command(name="clear", aliases=["purge"])
    @commands.has_permissions(manage_messages=True)
    async def clear(self, ctx: commands.Context, amount: int = 10):
        await ctx.message.delete()
        deleted = await ctx.channel.purge(limit=min(amount, 200))
        msg = await ctx.send(embed=_ok(f"Deleted **{len(deleted)}** messages."))
        await msg.delete(delay=4)

    # ── +warn ─────────────────────────────────────────────────
    @commands.command(name="warn")
    @commands.has_permissions(kick_members=True)
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason"):
        data = _load_warnings()
        gid  = str(ctx.guild.id)
        uid  = str(member.id)
        data.setdefault(gid, {}).setdefault(uid, [])
        entry = {
            "reason":      reason,
            "mod":         str(ctx.author.id),
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        }
        data[gid][uid].append(entry)
        _save_warnings(data)
        count = len(data[gid][uid])
        await ctx.send(embed=_ok(f"⚠️ Warned **{member}** (warning #{count})\n**Reason:** {reason}"))

    # ── +warnings ─────────────────────────────────────────────
    @commands.command(name="warnings")
    async def warnings(self, ctx: commands.Context, member: discord.Member):
        data  = _load_warnings()
        warns = data.get(str(ctx.guild.id), {}).get(str(member.id), [])
        if not warns:
            await ctx.send(embed=_embed(f"📋 Warnings for {member}", "No warnings.", discord.Colour.green()))
            return
        lines = "\n".join(
            f"**#{i+1}** — {w['reason']} *(by <@{w['mod']}>)*"
            for i, w in enumerate(warns)
        )
        e = _embed(f"📋 Warnings for {member}", lines, discord.Colour.orange())
        e.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=e)

    # ── +mute ─────────────────────────────────────────────────
    @commands.command(name="mute")
    @commands.has_permissions(manage_roles=True)
    async def mute(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason"):
        muted_role = discord.utils.get(ctx.guild.roles, name="Muted")
        if not muted_role:
            muted_role = await ctx.guild.create_role(name="Muted")
            for channel in ctx.guild.channels:
                try:
                    await channel.set_permissions(muted_role, send_messages=False, speak=False)
                except Exception:
                    pass
        await member.add_roles(muted_role, reason=f"{ctx.author}: {reason}")
        await ctx.send(embed=_ok(f"🔇 Muted **{member}**\n**Reason:** {reason}"))

    # ── +unmute ───────────────────────────────────────────────
    @commands.command(name="unmute")
    @commands.has_permissions(manage_roles=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member):
        muted_role = discord.utils.get(ctx.guild.roles, name="Muted")
        if muted_role and muted_role in member.roles:
            await member.remove_roles(muted_role)
            await ctx.send(embed=_ok(f"🔊 Unmuted **{member}**"))
        else:
            await ctx.send(embed=_err("That member is not muted."))

    # ── +lock ─────────────────────────────────────────────────
    @commands.command(name="lock")
    @commands.has_permissions(manage_channels=True)
    async def lock(self, ctx: commands.Context):
        await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False)
        await ctx.send(embed=_ok(f"🔒 **#{ctx.channel.name}** locked."))

    # ── +unlock ───────────────────────────────────────────────
    @commands.command(name="unlock")
    @commands.has_permissions(manage_channels=True)
    async def unlock(self, ctx: commands.Context):
        await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=None)
        await ctx.send(embed=_ok(f"🔓 **#{ctx.channel.name}** unlocked."))

    # ── +slowmode ─────────────────────────────────────────────
    @commands.command(name="slowmode")
    @commands.has_permissions(manage_channels=True)
    async def slowmode(self, ctx: commands.Context, seconds: int = 0):
        await ctx.channel.edit(slowmode_delay=seconds)
        msg = f"Slowmode set to **{seconds}s**." if seconds else "Slowmode **disabled**."
        await ctx.send(embed=_ok(msg))

    # ── +nick ─────────────────────────────────────────────────
    @commands.command(name="nick")
    @commands.has_permissions(manage_nicknames=True)
    async def nick(self, ctx: commands.Context, member: discord.Member, *, nickname: Optional[str] = None):
        old = member.display_name
        await member.edit(nick=nickname)
        await ctx.send(embed=_ok(f"Nickname changed: **{old}** → **{nickname or member.name}**"))

    # ── +role ─────────────────────────────────────────────────
    @commands.command(name="role")
    @commands.has_permissions(manage_roles=True)
    async def role(self, ctx: commands.Context, member: discord.Member, role: discord.Role):
        if role in member.roles:
            await member.remove_roles(role)
            await ctx.send(embed=_ok(f"Removed role **{role.name}** from **{member}**"))
        else:
            await member.add_roles(role)
            await ctx.send(embed=_ok(f"Added role **{role.name}** to **{member}**"))

    async def cog_command_error(self, ctx, error):
        if isinstance(error, (commands.MissingPermissions, commands.BotMissingPermissions)):
            await ctx.send(embed=_err(str(error)), delete_after=8)
        elif isinstance(error, commands.BadArgument):
            await ctx.send(embed=_err("Bad argument. Check your command usage."), delete_after=8)


async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))
    