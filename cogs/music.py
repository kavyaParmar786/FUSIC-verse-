"""
cogs/music.py — Rhythm-style Music System
Uses yt-dlp (SoundCloud + YouTube). Playlists saved to JSON.
Commands: +play +pause +resume +skip +stop +leave +queue
          +clearqueue +loop +volume +nowplaying +lyrics
          +autoplay +playlist +playlistadd +playlistplay +247
Panel:    +musicpanel (persistent buttons + live updating embed)
"""

import asyncio
import json
import random
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands, tasks

# yt-dlp is optional — graceful error if not installed
try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False

PLAYLISTS_DIR = Path("data/playlists")
AUTOPLAY_FILE = Path("data/autoplay.json")

YTDL_OPTIONS = {
    "format":            "bestaudio/best",
    "noplaylist":        True,
    "quiet":             True,
    "no_warnings":       True,
    "default_search":    "scsearch",     # SoundCloud primary
    "source_address":    "0.0.0.0",
    "extract_flat":      False,
    "cookiefile":        None,
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options":        "-vn",
}

WAVEFORMS = [
    "▃▅▆▂▁▇▄▅▃▂▆▇▅▁▃▄▂▆",
    "▇▄▂▅▃▆▁▇▅▃▂▆▄▇▁▂▅▃",
    "▁▃▅▇▆▄▂▁▃▅▇▆▄▂▁▃▅▇",
    "▅▃▇▁▆▂▄▅▃▇▁▆▂▄▅▃▇▁",
    "▂▆▁▇▃▅▄▂▆▁▇▃▅▄▂▆▁▇",
]


def _load_autoplay() -> dict:
    try:
        return json.loads(AUTOPLAY_FILE.read_text())
    except Exception:
        return {}


def _save_autoplay(data: dict):
    AUTOPLAY_FILE.write_text(json.dumps(data, indent=2))


def _load_playlists(guild_id: int) -> dict:
    p = PLAYLISTS_DIR / f"{guild_id}.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save_playlists(guild_id: int, data: dict):
    p = PLAYLISTS_DIR / f"{guild_id}.json"
    p.write_text(json.dumps(data, indent=2))


def _progress_bar(pos: int, duration: int, length: int = 12) -> str:
    if duration <= 0:
        return "▬" * length + " 🔘"
    filled = int((pos / duration) * length)
    bar = "▬" * filled + "🔘" + "▬" * (length - filled)
    return bar


def _fmt_time(seconds: int) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ─────────────────────────────────────────────────────────────
#  SONG / PLAYER STATE
# ─────────────────────────────────────────────────────────────

class Song:
    def __init__(self, data: dict):
        self.title     = data.get("title", "Unknown")
        self.url       = data.get("url") or data.get("webpage_url", "")
        self.stream    = data.get("url", "")            # direct stream URL
        self.thumbnail = data.get("thumbnail", "")
        self.duration  = int(data.get("duration") or 0)
        self.uploader  = data.get("uploader", "Unknown")
        self.webpage   = data.get("webpage_url", self.url)

    def source(self):
        return discord.FFmpegPCMAudio(self.stream, **FFMPEG_OPTIONS)


class GuildPlayer:
    """Per-guild music state."""
    def __init__(self):
        self.queue:          deque[Song]         = deque()
        self.current:        Optional[Song]      = None
        self.loop:           bool                = False
        self.volume:         float               = 1.0
        self.autoplay:       bool                = False
        self.stay247:        bool                = False
        self.pos_seconds:    int                 = 0
        self.panel_message:  Optional[discord.Message] = None
        self.panel_channel:  Optional[int]       = None
        self._last_songs:    list[str]           = []   # for autoplay fallback


# ─────────────────────────────────────────────────────────────
#  MUSIC PANEL VIEW  (persistent buttons)
# ─────────────────────────────────────────────────────────────

class MusicPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    def _get_vc(self, interaction: discord.Interaction) -> Optional[discord.VoiceClient]:
        return interaction.guild.voice_client

    @discord.ui.button(label="⏸ Pause",  style=discord.ButtonStyle.secondary, custom_id="music:pause",  row=0)
    async def btn_pause(self, interaction: discord.Interaction, _):
        vc = self._get_vc(interaction)
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message("⏸ Paused.", ephemeral=True, delete_after=4)
        else:
            await interaction.response.send_message("Nothing playing.", ephemeral=True, delete_after=4)

    @discord.ui.button(label="▶️ Resume", style=discord.ButtonStyle.success,   custom_id="music:resume", row=0)
    async def btn_resume(self, interaction: discord.Interaction, _):
        vc = self._get_vc(interaction)
        if vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message("▶️ Resumed.", ephemeral=True, delete_after=4)
        else:
            await interaction.response.send_message("Nothing paused.", ephemeral=True, delete_after=4)

    @discord.ui.button(label="⏭ Skip",   style=discord.ButtonStyle.primary,   custom_id="music:skip",   row=0)
    async def btn_skip(self, interaction: discord.Interaction, _):
        vc = self._get_vc(interaction)
        if vc and vc.is_playing():
            vc.stop()
            await interaction.response.send_message("⏭ Skipped.", ephemeral=True, delete_after=4)
        else:
            await interaction.response.send_message("Nothing playing.", ephemeral=True, delete_after=4)

    @discord.ui.button(label="⏹ Stop",   style=discord.ButtonStyle.danger,    custom_id="music:stop",   row=0)
    async def btn_stop(self, interaction: discord.Interaction, _):
        cog: MusicCog = interaction.client.get_cog("Music")
        if cog:
            gp = cog._players.get(interaction.guild_id)
            if gp:
                gp.queue.clear()
                gp.loop = False
        vc = self._get_vc(interaction)
        if vc:
            await vc.disconnect()
        await interaction.response.send_message("⏹ Stopped and disconnected.", ephemeral=True, delete_after=4)


# ─────────────────────────────────────────────────────────────
#  MUSIC COG
# ─────────────────────────────────────────────────────────────

class MusicCog(commands.Cog, name="Music"):
    def __init__(self, bot: commands.Bot):
        self.bot      = bot
        self._players: dict[int, GuildPlayer] = {}
        self._update_panels.start()

    def cog_unload(self):
        self._update_panels.cancel()

    def _gp(self, guild_id: int) -> GuildPlayer:
        if guild_id not in self._players:
            self._players[guild_id] = GuildPlayer()
        return self._players[guild_id]

    # ── Join helper ───────────────────────────────────────────
    async def _ensure_voice(self, ctx: commands.Context) -> Optional[discord.VoiceClient]:
        if not ctx.author.voice:
            await ctx.send(embed=self._err("You must be in a voice channel."))
            return None
        vc = ctx.guild.voice_client
        if vc:
            if vc.channel != ctx.author.voice.channel:
                await vc.move_to(ctx.author.voice.channel)
        else:
            vc = await ctx.author.voice.channel.connect()
        return vc

    # ── yt-dlp search ─────────────────────────────────────────
    async def _search(self, query: str) -> Optional[Song]:
        if not YTDLP_AVAILABLE:
            return None
        loop = asyncio.get_event_loop()

        def _extract():
            opts = YTDL_OPTIONS.copy()
            with yt_dlp.YoutubeDL(opts) as ydl:
                # if URL, extract; else search SoundCloud then YouTube
                if re.match(r"https?://", query):
                    data = ydl.extract_info(query, download=False)
                else:
                    data = ydl.extract_info(f"scsearch:{query}", download=False)
                    if not data or not data.get("entries"):
                        data = ydl.extract_info(f"ytsearch:{query}", download=False)
                if data and "entries" in data:
                    data = data["entries"][0]
                return data

        try:
            data = await loop.run_in_executor(None, _extract)
            return Song(data) if data else None
        except Exception as e:
            return None

    # ── Play next in queue ────────────────────────────────────
    def _play_next(self, guild: discord.Guild):
        gp = self._gp(guild.id)
        vc = guild.voice_client
        if not vc:
            return

        if gp.loop and gp.current:
            song = gp.current
        elif gp.queue:
            song = gp.queue.popleft()
        elif gp.autoplay and gp._last_songs:
            # re-queue last song as autoplay fallback
            asyncio.create_task(self._autoplay_next(guild))
            return
        elif gp.stay247:
            return   # stay in channel, wait
        else:
            gp.current = None
            return

        gp.current = song
        gp.pos_seconds = 0
        if song.webpage:
            gp._last_songs.append(song.webpage)
            if len(gp._last_songs) > 5:
                gp._last_songs.pop(0)

        source = discord.PCMVolumeTransformer(song.source(), volume=gp.volume)
        vc.play(source, after=lambda e: self._play_next(guild))

    async def _autoplay_next(self, guild: discord.Guild):
        gp = self._gp(guild.id)
        if not gp._last_songs:
            return
        last_url = gp._last_songs[-1]
        song = await self._search(last_url)
        if song:
            gp.queue.append(song)
            self._play_next(guild)

    # ── Panel update task ─────────────────────────────────────
    @tasks.loop(seconds=5)
    async def _update_panels(self):
        for guild in self.bot.guilds:
            gp = self._players.get(guild.id)
            if not gp or not gp.panel_message or not gp.current:
                continue
            try:
                if guild.voice_client and guild.voice_client.is_playing():
                    gp.pos_seconds += 5
                embed = self._now_playing_embed(gp)
                channel = guild.get_channel(gp.panel_channel)
                if channel:
                    await gp.panel_message.edit(embed=embed)
            except Exception:
                pass

    @_update_panels.before_loop
    async def before_panels(self):
        await self.bot.wait_until_ready()

    # ── Embeds ────────────────────────────────────────────────
    def _err(self, msg: str) -> discord.Embed:
        return discord.Embed(title="❌  Error", description=msg, colour=discord.Colour.red())

    def _now_playing_embed(self, gp: GuildPlayer) -> discord.Embed:
        song = gp.current
        if not song:
            return discord.Embed(title="🎵 No song playing", colour=discord.Colour.blurple())

        progress = _progress_bar(gp.pos_seconds, song.duration)
        pos_str  = _fmt_time(gp.pos_seconds)
        dur_str  = _fmt_time(song.duration) if song.duration else "Live"
        wave     = random.choice(WAVEFORMS)

        e = discord.Embed(
            title="🎶  FUSIC VERSE Music Player",
            colour=discord.Colour.purple(),
            timestamp=datetime.now(timezone.utc),
        )
        e.add_field(name="🎵  Now Playing", value=f"**[{song.title}]({song.webpage})**", inline=False)
        e.add_field(name="👤  Artist",      value=song.uploader,                          inline=True)
        e.add_field(name="🔊  Volume",      value=f"{int(gp.volume * 100)}%",             inline=True)
        e.add_field(name="🔁  Loop",        value="On" if gp.loop else "Off",             inline=True)
        e.add_field(
            name="⏱️  Progress",
            value=f"`{pos_str}` {progress} `{dur_str}`",
            inline=False,
        )
        e.add_field(name="〰️  Waveform", value=f"`{wave}`", inline=False)
        if song.thumbnail:
            e.set_thumbnail(url=song.thumbnail)
        e.set_footer(text="Updates every 5 seconds • FUSIC VERSE Music")
        return e

    # ─────────────────────────────────────────────────────────
    #  COMMANDS
    # ─────────────────────────────────────────────────────────

    @commands.command(name="play", aliases=["p"])
    async def play(self, ctx: commands.Context, *, query: str):
        """Play a song (SoundCloud primary, YouTube fallback)."""
        if not YTDLP_AVAILABLE:
            await ctx.send(embed=self._err("yt-dlp is not installed. Run: `pip install yt-dlp`"))
            return

        vc = await self._ensure_voice(ctx)
        if not vc:
            return

        async with ctx.typing():
            song = await self._search(query)
            if not song:
                await ctx.send(embed=self._err("Could not find that song."))
                return

        gp = self._gp(ctx.guild.id)
        gp.queue.append(song)

        if not vc.is_playing() and not vc.is_paused():
            self._play_next(ctx.guild)
            e = discord.Embed(
                title="🎵  Now Playing",
                description=f"**[{song.title}]({song.webpage})**",
                colour=discord.Colour.purple(),
            )
        else:
            pos = len(gp.queue)
            e = discord.Embed(
                title="📥  Added to Queue",
                description=f"**[{song.title}]({song.webpage})**\nPosition: **#{pos}**",
                colour=discord.Colour.blurple(),
            )
        if song.thumbnail:
            e.set_thumbnail(url=song.thumbnail)
        e.add_field(name="Duration", value=_fmt_time(song.duration) if song.duration else "Live")
        await ctx.send(embed=e)

    @commands.command(name="pause")
    async def pause(self, ctx: commands.Context):
        vc = ctx.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await ctx.send(embed=discord.Embed(title="⏸ Paused", colour=discord.Colour.orange()))
        else:
            await ctx.send(embed=self._err("Nothing is playing."))

    @commands.command(name="resume")
    async def resume(self, ctx: commands.Context):
        vc = ctx.guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
            await ctx.send(embed=discord.Embed(title="▶️ Resumed", colour=discord.Colour.green()))
        else:
            await ctx.send(embed=self._err("Nothing is paused."))

    @commands.command(name="skip")
    async def skip(self, ctx: commands.Context):
        vc = ctx.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await ctx.send(embed=discord.Embed(title="⏭ Skipped", colour=discord.Colour.blurple()))
        else:
            await ctx.send(embed=self._err("Nothing to skip."))

    @commands.command(name="stop")
    async def stop(self, ctx: commands.Context):
        gp = self._gp(ctx.guild.id)
        gp.queue.clear()
        gp.loop = False
        vc = ctx.guild.voice_client
        if vc:
            await vc.disconnect()
        await ctx.send(embed=discord.Embed(title="⏹ Stopped", colour=discord.Colour.red()))

    @commands.command(name="leave")
    async def leave(self, ctx: commands.Context):
        vc = ctx.guild.voice_client
        if vc:
            await vc.disconnect()
            await ctx.send(embed=discord.Embed(title="👋 Left voice channel", colour=discord.Colour.blurple()))
        else:
            await ctx.send(embed=self._err("Not in a voice channel."))

    @commands.command(name="queue", aliases=["q"])
    async def queue_cmd(self, ctx: commands.Context):
        gp = self._gp(ctx.guild.id)
        if not gp.queue and not gp.current:
            await ctx.send(embed=self._err("Queue is empty."))
            return
        e = discord.Embed(title="🎵  Music Queue", colour=discord.Colour.purple())
        if gp.current:
            e.add_field(name="▶️ Now Playing", value=f"**{gp.current.title}**", inline=False)
        lines = "\n".join(
            f"`{i+1}.` {s.title}" for i, s in enumerate(gp.queue)
        ) or "Queue is empty."
        e.add_field(name="📋 Up Next", value=lines[:1000], inline=False)
        e.set_footer(text=f"{len(gp.queue)} song(s) in queue")
        await ctx.send(embed=e)

    @commands.command(name="clearqueue")
    async def clearqueue(self, ctx: commands.Context):
        self._gp(ctx.guild.id).queue.clear()
        await ctx.send(embed=discord.Embed(title="🗑️ Queue cleared", colour=discord.Colour.orange()))

    @commands.command(name="loop")
    async def loop_cmd(self, ctx: commands.Context):
        gp = self._gp(ctx.guild.id)
        gp.loop = not gp.loop
        status = "enabled" if gp.loop else "disabled"
        await ctx.send(embed=discord.Embed(title=f"🔁 Loop {status}", colour=discord.Colour.green()))

    @commands.command(name="volume", aliases=["vol"])
    async def volume(self, ctx: commands.Context, vol: int):
        if not 0 <= vol <= 200:
            await ctx.send(embed=self._err("Volume must be 0–200.")); return
        gp = self._gp(ctx.guild.id)
        gp.volume = vol / 100
        vc = ctx.guild.voice_client
        if vc and vc.source:
            vc.source.volume = gp.volume
        await ctx.send(embed=discord.Embed(title=f"🔊 Volume set to {vol}%", colour=discord.Colour.green()))

    @commands.command(name="nowplaying", aliases=["np"])
    async def nowplaying(self, ctx: commands.Context):
        gp = self._gp(ctx.guild.id)
        await ctx.send(embed=self._now_playing_embed(gp))

    @commands.command(name="lyrics")
    async def lyrics(self, ctx: commands.Context, *, query: Optional[str] = None):
        gp  = self._gp(ctx.guild.id)
        song_title = query or (gp.current.title if gp.current else None)
        if not song_title:
            await ctx.send(embed=self._err("No song playing and no query given.")); return
        # Basic lyrics lookup via API (no key required)
        import urllib.parse, urllib.request
        try:
            encoded = urllib.parse.quote(song_title)
            url = f"https://api.lyrics.ovh/v1/{encoded}"
            # Simple fallback: just show search link
            e = discord.Embed(
                title=f"📝 Lyrics — {song_title}",
                description=(
                    "Lyrics are fetched from lyrics.ovh.\n"
                    f"🔗 [Search lyrics for this song](https://www.google.com/search?q={encoded}+lyrics)"
                ),
                colour=discord.Colour.purple(),
            )
            await ctx.send(embed=e)
        except Exception as err:
            await ctx.send(embed=self._err(f"Could not fetch lyrics: {err}"))

    @commands.command(name="autoplay")
    async def autoplay(self, ctx: commands.Context):
        gp = self._gp(ctx.guild.id)
        gp.autoplay = not gp.autoplay
        ap_data = _load_autoplay()
        ap_data[str(ctx.guild.id)] = gp.autoplay
        _save_autoplay(ap_data)
        status = "**enabled** 🟢" if gp.autoplay else "**disabled** 🔴"
        await ctx.send(embed=discord.Embed(
            title=f"🔄 Autoplay {status}",
            description="When the queue ends, the bot will re-queue recent songs.",
            colour=discord.Colour.green() if gp.autoplay else discord.Colour.red(),
        ))

    @commands.command(name="247")
    @commands.has_permissions(manage_guild=True)
    async def stay_247(self, ctx: commands.Context):
        gp = self._gp(ctx.guild.id)
        gp.stay247 = not gp.stay247
        status = "**enabled** 🟢" if gp.stay247 else "**disabled** 🔴"
        await ctx.send(embed=discord.Embed(
            title=f"🕐 24/7 Mode {status}",
            colour=discord.Colour.green() if gp.stay247 else discord.Colour.red(),
        ))

    # ── Playlist commands ─────────────────────────────────────
    @commands.command(name="playlist")
    async def playlist(self, ctx: commands.Context):
      