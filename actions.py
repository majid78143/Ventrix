"""
Vantrix - shared action logic.
Every real action (kick, ban, mute, warn, meme, ticket, ...) lives here as a
plain async function. Both the Discord slash commands (bot.py) and the
website's live command console (website.py) call the exact same functions,
so behaviour never drifts between the two surfaces.
"""
import datetime as dt
import random

import discord

import db

MEMES = [
    "https://i.imgur.com/6IWGRWl.png",
    "https://i.imgur.com/2X4bJ3d.png",
    "https://i.imgur.com/9y8Ffhd.png",
]
EIGHT_BALL_ANSWERS = [
    "Yes, definitely.", "No way.", "Ask again later.", "It is certain.",
    "Very doubtful.", "Absolutely.", "Cannot predict now.",
]


async def kick_member(member: discord.Member, reason: str = "No reason provided") -> str:
    await member.kick(reason=reason)
    return f"Kicked {member.display_name} — {reason}"


async def ban_member(member: discord.Member, reason: str = "No reason provided") -> str:
    await member.ban(reason=reason)
    return f"Banned {member.display_name} — {reason}"


async def mute_member(member: discord.Member, minutes: int = 10) -> str:
    await member.timeout(dt.timedelta(minutes=minutes))
    return f"Muted {member.display_name} for {minutes} minutes"


async def warn_member(guild_id: int, member: discord.Member, moderator_id: int, reason: str) -> str:
    db.add_warning(guild_id, member.id, moderator_id, reason)
    return f"Warned {member.display_name} — {reason}"


async def clear_messages(channel: discord.TextChannel, amount: int = 10) -> str:
    deleted = await channel.purge(limit=amount)
    return f"Cleared {len(deleted)} messages in #{channel.name}"


async def lock_channel(channel: discord.TextChannel) -> str:
    await channel.set_permissions(channel.guild.default_role, send_messages=False)
    return f"Locked #{channel.name}"


async def unlock_channel(channel: discord.TextChannel) -> str:
    await channel.set_permissions(channel.guild.default_role, send_messages=True)
    return f"Unlocked #{channel.name}"


async def send_meme(channel: discord.TextChannel) -> str:
    url = random.choice(MEMES)
    await channel.send(url)
    return url


async def eight_ball(question: str) -> str:
    return random.choice(EIGHT_BALL_ANSWERS)


def build_userinfo_embed(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(title=member.display_name, color=discord.Color.purple())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Joined", value=member.joined_at.strftime("%Y-%m-%d"))
    embed.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d"))
    embed.add_field(name="Roles", value=", ".join(r.name for r in member.roles[1:]) or "None")
    return embed


def build_serverinfo_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(title=guild.name, color=discord.Color.blue())
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="Members", value=guild.member_count)
    embed.add_field(name="Created", value=guild.created_at.strftime("%Y-%m-%d"))
    embed.add_field(name="Owner", value=str(guild.owner))
    return embed


async def open_ticket(guild: discord.Guild, user: discord.Member) -> discord.TextChannel:
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    channel = await guild.create_text_channel(f"ticket-{user.name}", overwrites=overwrites)
    db.create_ticket(guild.id, channel.id, user.id)
    return channel


async def close_ticket(channel: discord.TextChannel) -> None:
    db.close_ticket(channel.id)
    await channel.delete(delay=5)


def bind_reaction_role(guild_id: int, message_id: int, emoji: str, role_id: int) -> str:
    db.add_reaction_role(guild_id, message_id, emoji, role_id)
    return f"Bound {emoji} on message {message_id}"


def leaderboard_lines(guild: discord.Guild) -> list[str]:
    top = db.get_leaderboard(guild.id)
    lines = []
    for i, entry in enumerate(top, start=1):
        member = guild.get_member(entry["user_id"])
        name = member.display_name if member else f"User {entry['user_id']}"
        lines.append(f"#{i}  {name} — Level {entry['level']} ({entry['xp']} XP)")
    return lines


# ---------------------------------------------------------------------------
# Command registry: single source of truth for the public /commands page,
# the slash commands, AND the website's live command console.
# ---------------------------------------------------------------------------
COMMANDS = [
    {"name": "kick", "description": "Kick a member", "category": "Moderation",
     "params": [{"name": "member_id", "label": "Member", "type": "user"},
                {"name": "reason", "label": "Reason", "type": "text", "required": False}]},
    {"name": "ban", "description": "Ban a member", "category": "Moderation",
     "params": [{"name": "member_id", "label": "Member", "type": "user"},
                {"name": "reason", "label": "Reason", "type": "text", "required": False}]},
    {"name": "mute", "description": "Timeout a member", "category": "Moderation",
     "params": [{"name": "member_id", "label": "Member", "type": "user"},
                {"name": "minutes", "label": "Minutes", "type": "number", "required": False}]},
    {"name": "warn", "description": "Warn a member", "category": "Moderation",
     "params": [{"name": "member_id", "label": "Member", "type": "user"},
                {"name": "reason", "label": "Reason", "type": "text"}]},
    {"name": "clear", "description": "Bulk delete messages", "category": "Moderation",
     "params": [{"name": "channel_id", "label": "Channel", "type": "channel"},
                {"name": "amount", "label": "Amount", "type": "number", "required": False}]},
    {"name": "lock", "description": "Lock a channel", "category": "Moderation",
     "params": [{"name": "channel_id", "label": "Channel", "type": "channel"}]},
    {"name": "unlock", "description": "Unlock a channel", "category": "Moderation",
     "params": [{"name": "channel_id", "label": "Channel", "type": "channel"}]},
    {"name": "meme", "description": "Post a random meme", "category": "Fun",
     "params": [{"name": "channel_id", "label": "Channel", "type": "channel"}]},
    {"name": "8ball", "description": "Ask the magic 8-ball", "category": "Fun",
     "params": [{"name": "question", "label": "Question", "type": "text"}]},
    {"name": "userinfo", "description": "Show info about a member", "category": "Utility",
     "params": [{"name": "member_id", "label": "Member", "type": "user"}]},
    {"name": "serverinfo", "description": "Show info about this server", "category": "Utility", "params": []},
    {"name": "leaderboard", "description": "Show the XP leaderboard", "category": "Leveling", "params": []},
    {"name": "ticket-open", "description": "Open a support ticket for a member", "category": "Tickets",
     "params": [{"name": "member_id", "label": "Member", "type": "user"}]},
    {"name": "reactionrole", "description": "Bind an emoji reaction to a role", "category": "Reaction Roles",
     "params": [{"name": "message_id", "label": "Message ID", "type": "text"},
                {"name": "emoji", "label": "Emoji", "type": "text"},
                {"name": "role_id", "label": "Role", "type": "role"}]},
]


async def run_command(guild: discord.Guild, name: str, params: dict, moderator_id: int) -> str:
    """Dispatch a registry command by name, used by the website live console."""
    def _member(key="member_id"):
        return guild.get_member(int(params[key]))

    def _channel(key="channel_id"):
        return guild.get_channel(int(params[key]))

    if name == "kick":
        return await kick_member(_member(), params.get("reason") or "No reason provided")
    if name == "ban":
        return await ban_member(_member(), params.get("reason") or "No reason provided")
    if name == "mute":
        return await mute_member(_member(), int(params.get("minutes") or 10))
    if name == "warn":
        return await warn_member(guild.id, _member(), moderator_id, params.get("reason", ""))
    if name == "clear":
        return await clear_messages(_channel(), int(params.get("amount") or 10))
    if name == "lock":
        return await lock_channel(_channel())
    if name == "unlock":
        return await unlock_channel(_channel())
    if name == "meme":
        return await send_meme(_channel())
    if name == "8ball":
        answer = await eight_ball(params.get("question", ""))
        await _channel().send(answer) if params.get("channel_id") else None
        return answer
    if name == "userinfo":
        member = _member()
        return f"{member.display_name} — joined {member.joined_at.strftime('%Y-%m-%d')}"
    if name == "serverinfo":
        return f"{guild.name} — {guild.member_count} members"
    if name == "leaderboard":
        return "\n".join(leaderboard_lines(guild)) or "No XP data yet."
    if name == "ticket-open":
        channel = await open_ticket(guild, _member())
        return f"Ticket opened: #{channel.name}"
    if name == "reactionrole":
        return bind_reaction_role(guild.id, int(params["message_id"]), params["emoji"], int(params["role_id"]))
    raise ValueError(f"Unknown command: {name}")
