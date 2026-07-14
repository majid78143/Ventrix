"""
Vantrix - companion website (Flask).
Runs in the same process as the bot (see main.py), so it can read live
guild/channel/role/member data directly from the bot's in-memory cache,
and can execute the same actions the bot's slash commands use (actions.py).
"""
import asyncio
from functools import wraps

import requests
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

import actions
import config
import db

app = Flask(__name__)
app.secret_key = config.SESSION_SECRET

_bot = None  # injected by main.py once the bot logs in


def attach_bot(bot_instance):
    global _bot
    _bot = bot_instance


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def api_login_required(f):
    """Same as login_required, but returns JSON 401 instead of a redirect —
    a redirect breaks fetch()'s response.json() and fails silently."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return jsonify({"ok": False, "error": "not authenticated"}), 401
        return f(*args, **kwargs)
    return wrapper


def manageable_guilds():
    """Guilds the logged-in user administers AND the bot is also in."""
    if not _bot or "guilds" not in session:
        return []
    bot_guild_ids = {g.id for g in _bot.guilds}
    result = []
    for g in session["guilds"]:
        gid = int(g["id"])
        is_admin = (int(g["permissions"]) & 0x8) == 0x8
        if is_admin and gid in bot_guild_ids:
            bot_guild = _bot.get_guild(gid)
            result.append({"id": gid, "name": bot_guild.name, "icon": bot_guild.icon.url if bot_guild.icon else None})
    return result


def guild_is_manageable(guild_id: int) -> bool:
    return guild_id in {g["id"] for g in manageable_guilds()}


def run_on_bot_loop(coro, timeout=15):
    """Schedule a coroutine on the bot's asyncio loop from this Flask thread
    and block until it finishes (or raises)."""
    future = asyncio.run_coroutine_threadsafe(coro, _bot.loop)
    return future.result(timeout=timeout)


# ---------------- Public pages ----------------
@app.route("/")
def home():
    stats = {
        "servers": len(_bot.guilds) if _bot else 0,
        "users": sum(g.member_count for g in _bot.guilds) if _bot else 0,
        "commands": len(actions.COMMANDS),
        "uptime": "99.9%",
    }
    showcase = []
    if _bot:
        for gid in db.get_all_showcase_guild_ids():
            g = _bot.get_guild(gid)
            if g:
                showcase.append({"name": g.name, "icon": g.icon.url if g.icon else None, "members": g.member_count})
    developers = list(db.developers_col.find()) if db.developers_col is not None else []
    for dev in developers:
        if _bot:
            user = _bot.get_user(int(dev["discord_id"]))
            if user:
                dev["username"] = str(user)
                dev["avatar"] = user.display_avatar.url
    return render_template("home.html", stats=stats, showcase=showcase, developers=developers,
                            client_id=config.DISCORD_CLIENT_ID)


@app.route("/commands")
def commands_page():
    return render_template("commands.html", commands=actions.COMMANDS)


# ---------------- OAuth ----------------
@app.route("/login")
def login():
    url = (
        f"https://discord.com/api/oauth2/authorize?client_id={config.DISCORD_CLIENT_ID}"
        f"&redirect_uri={config.DISCORD_REDIRECT_URI}&response_type=code"
        f"&scope={config.DISCORD_OAUTH_SCOPE.replace(' ', '%20')}"
    )
    return redirect(url)


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return redirect(url_for("home"))
    token_res = requests.post(
        f"{config.DISCORD_API_BASE}/oauth2/token",
        data={
            "client_id": config.DISCORD_CLIENT_ID,
            "client_secret": config.DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.DISCORD_REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    ).json()
    access_token = token_res.get("access_token")
    if not access_token:
        return redirect(url_for("home"))
    headers = {"Authorization": f"Bearer {access_token}"}
    user = requests.get(f"{config.DISCORD_API_BASE}/users/@me", headers=headers).json()
    guilds = requests.get(f"{config.DISCORD_API_BASE}/users/@me/guilds", headers=headers).json()
    session["user"] = user
    session["guilds"] = guilds
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


# ---------------- Dashboard ----------------
@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", user=session["user"], guilds=manageable_guilds())


@app.route("/dashboard/<int:guild_id>", methods=["GET", "POST"])
@login_required
def guild_settings(guild_id):
    if not guild_is_manageable(guild_id):
        return "Forbidden", 403
    if request.method == "POST":
        modules = {key.split("module_")[1]: True for key in request.form if key.startswith("module_")}
        for mod in db.DEFAULT_MODULES:
            modules.setdefault(mod, False)
        db.update_settings(guild_id, {
            "modules": modules,
            "welcome_message": request.form.get("welcome_message", ""),
            "goodbye_message": request.form.get("goodbye_message", ""),
            "showcase_opt_in": "showcase_opt_in" in request.form,
        })
    settings = db.get_settings(guild_id)
    guild = _bot.get_guild(guild_id) if _bot else None
    channels = [{"id": c.id, "name": c.name} for c in guild.text_channels] if guild else []
    return render_template("guild_settings.html", settings=settings, guild=guild, channels=channels,
                            modules=db.DEFAULT_MODULES)


@app.route("/leaderboard/<int:guild_id>")
def leaderboard_page(guild_id):
    top = db.get_leaderboard(guild_id, limit=25)
    guild = _bot.get_guild(guild_id) if _bot else None
    entries = []
    for i, e in enumerate(top, start=1):
        member = guild.get_member(e["user_id"]) if guild else None
        entries.append({
            "rank": i,
            "name": member.display_name if member else f"User {e['user_id']}",
            "avatar": member.display_avatar.url if member else None,
            "level": e["level"],
            "xp": e["xp"],
        })
    return render_template("leaderboard.html", entries=entries, guild=guild)


# ---------------- Live command console ----------------
@app.route("/dashboard/<int:guild_id>/commands")
@login_required
def commands_console(guild_id):
    if not guild_is_manageable(guild_id):
        return "Forbidden", 403
    guild = _bot.get_guild(guild_id) if _bot else None
    return render_template("commands_console.html", guild=guild, commands=actions.COMMANDS)


@app.route("/api/guild/<int:guild_id>/run-command", methods=["POST"])
@api_login_required
def run_command_api(guild_id):
    if not guild_is_manageable(guild_id):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    guild = _bot.get_guild(guild_id) if _bot else None
    if not guild:
        return jsonify({"ok": False, "error": "guild not found"}), 404
    data = request.get_json(force=True)
    name = data.get("name")
    params = data.get("params", {})
    moderator_id = int(session["user"]["id"])
    try:
        result = run_on_bot_loop(actions.run_command(guild, name, params, moderator_id))
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


# ---------------- Embed builder ----------------
@app.route("/dashboard/<int:guild_id>/embed-builder")
@login_required
def embed_builder(guild_id):
    if not guild_is_manageable(guild_id):
        return "Forbidden", 403
    return render_template("embed_builder.html", guild_id=guild_id)


@app.route("/api/guild/<int:guild_id>/mentionables")
@api_login_required
def mentionables(guild_id):
    guild = _bot.get_guild(guild_id) if _bot else None
    if not guild:
        return jsonify({"users": [], "roles": [], "channels": []})
    return jsonify({
        "users": [{"id": m.id, "name": m.display_name} for m in guild.members][:200],
        "roles": [{"id": r.id, "name": r.name, "color": str(r.color)} for r in guild.roles],
        "channels": [{"id": c.id, "name": c.name} for c in guild.text_channels],
    })


@app.route("/api/guild/<int:guild_id>/send-embed", methods=["POST"])
@api_login_required
def send_embed(guild_id):
    import discord
    if not guild_is_manageable(guild_id):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    guild = _bot.get_guild(guild_id) if _bot else None
    data = request.get_json(force=True)
    channel = guild.get_channel(int(data["channel_id"])) if guild else None
    if not channel:
        return jsonify({"ok": False, "error": "channel not found"}), 400
    embed = discord.Embed(
        title=data.get("title") or None,
        description=data.get("description") or None,
        color=int(data.get("color", "#8b5cf6").lstrip("#"), 16),
    )
    if data.get("image_url"):
        embed.set_image(url=data["image_url"])
    if data.get("footer"):
        embed.set_footer(text=data["footer"])
    if data.get("author"):
        embed.set_author(name=data["author"])
    for field in data.get("fields", []):
        embed.add_field(name=field["name"], value=field["value"], inline=field.get("inline", False))
    try:
        run_on_bot_loop(channel.send(embed=embed))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
