"""
Vantrix - Discord bot (discord.py).
Runs in the same process as the website (see main.py) so Flask routes can
read live guild/channel/role data straight from this bot's cache, and can
execute the exact same actions defined in actions.py.
"""
import random
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import actions
import config
import db

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix=config.BOT_PREFIX, intents=intents)

# in-memory anti-nuke trackers (per guild): recent destructive actions
_recent_actions: dict[int, list[float]] = {}
ANTI_NUKE_WINDOW_SECONDS = 10
ANTI_NUKE_THRESHOLD = 5


def module_enabled(guild_id: int, module: str) -> bool:
    s = db.get_settings(guild_id)
    return s.get("modules", {}).get(module, True)


@bot.event
async def on_ready():
    print(f"[Vantrix] Logged in as {bot.user} ({bot.user.id})")
    try:
        await bot.tree.sync()
    except Exception as e:
        print(f"[Vantrix] Slash sync failed: {e}")


# ---------- Leveling ----------
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    if module_enabled(message.guild.id, "leveling"):
        result = db.add_xp(message.guild.id, message.author.id, random.randint(5, 15))
        if result.get("leveled_up"):
            try:
                await message.channel.send(f"{message.author.mention} reached level {result['level']}.")
            except discord.Forbidden:
                pass
    if module_enabled(message.guild.id, "auto_responder"):
        s = db.get_settings(message.guild.id)
        content = message.content.lower()
        for trigger, reply in s.get("auto_responses", {}).items():
            if trigger.lower() in content:
                await message.channel.send(reply)
                break
    await bot.process_commands(message)


# ---------- Welcome / Goodbye ----------
@bot.event
async def on_member_join(member: discord.Member):
    if not module_enabled(member.guild.id, "welcome"):
        return
    s = db.get_settings(member.guild.id)
    channel_id = s.get("welcome_channel_id")
    if channel_id:
        channel = member.guild.get_channel(int(channel_id))
        if channel:
            msg = s.get("welcome_message", "Welcome {user}").format(user=member.mention, server=member.guild.name)
            await channel.send(msg)


@bot.event
async def on_member_remove(member: discord.Member):
    if not module_enabled(member.guild.id, "welcome"):
        return
    s = db.get_settings(member.guild.id)
    channel_id = s.get("goodbye_channel_id")
    if channel_id:
        channel = member.guild.get_channel(int(channel_id))
        if channel:
            msg = s.get("goodbye_message", "{user} left.").format(user=member.display_name, server=member.guild.name)
            await channel.send(msg)


# ---------- Anti-Nuke ----------
async def _flag_destructive_action(guild: discord.Guild, actor: discord.Member):
    if not module_enabled(guild.id, "anti_nuke"):
        return
    if str(actor.id) in config.OWNER_IDS or actor.id == guild.owner_id:
        return
    now = datetime.now(timezone.utc).timestamp()
    bucket = _recent_actions.setdefault(guild.id, [])
    bucket.append(now)
    _recent_actions[guild.id] = [t for t in bucket if now - t < ANTI_NUKE_WINDOW_SECONDS]
    if len(_recent_actions[guild.id]) >= ANTI_NUKE_THRESHOLD:
        try:
            await guild.ban(actor, reason="Vantrix Anti-Nuke: suspicious mass action detected")
        except discord.Forbidden:
            pass


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    async for entry in channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
        await _flag_destructive_action(channel.guild, entry.user)


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
        await _flag_destructive_action(guild, entry.user)


# ---------- Reaction Roles ----------
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.member is None or payload.member.bot:
        return
    if not module_enabled(payload.guild_id, "reaction_roles"):
        return
    rr = db.get_reaction_role(payload.guild_id, payload.message_id, str(payload.emoji))
    if rr:
        guild = bot.get_guild(payload.guild_id)
        role = guild.get_role(int(rr["role_id"]))
        if role:
            await payload.member.add_roles(role, reason="Vantrix reaction role")


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if not module_enabled(payload.guild_id, "reaction_roles"):
        return
    rr = db.get_reaction_role(payload.guild_id, payload.message_id, str(payload.emoji))
    if rr:
        guild = bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id)
        role = guild.get_role(int(rr["role_id"]))
        if member and role:
            await member.remove_roles(role, reason="Vantrix reaction role")


# ---------- Moderation (slash commands, backed by actions.py) ----------
@bot.tree.command(name="kick", description="Kick a member")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    result = await actions.kick_member(member, reason)
    await interaction.response.send_message(result)


@bot.tree.command(name="ban", description="Ban a member")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    result = await actions.ban_member(member, reason)
    await interaction.response.send_message(result)


@bot.tree.command(name="mute", description="Timeout a member for N minutes")
@app_commands.checks.has_permissions(moderate_members=True)
async def mute(interaction: discord.Interaction, member: discord.Member, minutes: int = 10):
    result = await actions.mute_member(member, minutes)
    await interaction.response.send_message(result)


@bot.tree.command(name="warn", description="Warn a member")
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    result = await actions.warn_member(interaction.guild_id, member, interaction.user.id, reason)
    await interaction.response.send_message(result)


@bot.tree.command(name="clear", description="Bulk delete messages")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: int = 10):
    result = await actions.clear_messages(interaction.channel, amount)
    await interaction.response.send_message(result, ephemeral=True)


@bot.tree.command(name="lock", description="Lock the current channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def lock(interaction: discord.Interaction):
    result = await actions.lock_channel(interaction.channel)
    await interaction.response.send_message(result)


@bot.tree.command(name="unlock", description="Unlock the current channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def unlock(interaction: discord.Interaction):
    result = await actions.unlock_channel(interaction.channel)
    await interaction.response.send_message(result)


# ---------- Fun / Utility ----------
@bot.tree.command(name="meme", description="Get a random meme")
async def meme(interaction: discord.Interaction):
    url = random.choice(actions.MEMES)
    await interaction.response.send_message(url)


@bot.tree.command(name="8ball", description="Ask the magic 8-ball")
async def eight_ball(interaction: discord.Interaction, question: str):
    answer = await actions.eight_ball(question)
    await interaction.response.send_message(f"{question}\n{answer}")


@bot.tree.command(name="avatar", description="Show a member's avatar")
async def avatar(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    await interaction.response.send_message(member.display_avatar.url)


@bot.tree.command(name="userinfo", description="Show info about a member")
async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    await interaction.response.send_message(embed=actions.build_userinfo_embed(member))


@bot.tree.command(name="serverinfo", description="Show info about this server")
async def serverinfo(interaction: discord.Interaction):
    await interaction.response.send_message(embed=actions.build_serverinfo_embed(interaction.guild))


# ---------- Tickets ----------
class TicketView(discord.ui.View):
    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.green)
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = await actions.open_ticket(interaction.guild, interaction.user)
        await channel.send(f"{interaction.user.mention} welcome to your ticket.", view=CloseTicketView())
        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)


class CloseTicketView(discord.ui.View):
    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red)
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Closing ticket in 5 seconds.")
        await actions.close_ticket(interaction.channel)


@bot.tree.command(name="ticket-panel", description="Post the ticket panel")
@app_commands.checks.has_permissions(manage_guild=True)
async def ticket_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Need help?",
        description="Click the button below to open a support ticket.",
        color=discord.Color.dark_purple(),
    )
    await interaction.response.send_message(embed=embed, view=TicketView())


# ---------- Reaction role setup ----------
@bot.tree.command(name="reactionrole", description="Bind an emoji reaction on a message to a role")
@app_commands.checks.has_permissions(manage_roles=True)
async def reactionrole(interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role):
    result = actions.bind_reaction_role(interaction.guild_id, int(message_id), emoji, role.id)
    await interaction.response.send_message(result)


# ---------- Leaderboard ----------
@bot.tree.command(name="leaderboard", description="Show the XP leaderboard")
async def leaderboard(interaction: discord.Interaction):
    lines = actions.leaderboard_lines(interaction.guild)
    if not lines:
        await interaction.response.send_message("No XP data yet.")
        return
    embed = discord.Embed(title="XP Leaderboard", description="\n".join(lines), color=discord.Color.gold())
    await interaction.response.send_message(embed=embed)


def run_bot():
    if not config.DISCORD_BOT_TOKEN:
        print("[Vantrix] DISCORD_BOT_TOKEN missing — bot will not start.")
        return
    bot.run(config.DISCORD_BOT_TOKEN)
