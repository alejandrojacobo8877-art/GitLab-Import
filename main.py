import datetime
import os
import random
import re
import time
from threading import Thread

import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask
import psycopg2
import psycopg2.extras

# --- 0. KEEP-ALIVE WEB SERVER (FOR RENDER) ---
app = Flask("")


@app.route("/")
def home():
    return "Bot is alive and running!"


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()


# --- 1. SUPABASE POSTGRESQL DATABASE CONNECTION ---
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres.lrhcfqmykacliddytews:Foreveryoungmentally666@aws-0-eu-central-1.pooler.supabase.com:6543/postgres",
)


def get_db():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                points INT DEFAULT 0,
                current_task TEXT DEFAULT NULL,
                current_bracket TEXT DEFAULT NULL,
                tasks_completed_today INT DEFAULT 0,
                last_task_reset TEXT DEFAULT NULL,
                cooldown_until TEXT DEFAULT NULL,
                last_task_time BIGINT DEFAULT 0,
                combat_level INT DEFAULT 0,
                total_level INT DEFAULT 0,
                discord_name TEXT DEFAULT NULL
            );
            """)
            conn.commit()


init_db()


def get_user_data(user_id):
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(
                "SELECT * FROM users WHERE user_id = %s", (user_id,)
            )
            row = cursor.fetchone()

            if row is None:
                cursor.execute(
                    """
                    INSERT INTO users (user_id, points, current_task, current_bracket, tasks_completed_today, last_task_reset, cooldown_until, last_task_time, combat_level, total_level, discord_name)
                    VALUES (%s, 0, NULL, NULL, 0, NULL, NULL, 0, 0, 0, NULL)
                    RETURNING *;
                """,
                    (user_id,),
                )
                conn.commit()
                row = cursor.fetchone()

            return row


# --- 2. TASK LISTS ---
LOW_TASKS = [
    "Defeat Obor 25 times",
    "Defeat Bryophyta 25 times",
    "Defeat Scurrius 150 times",
    "Defeat Crazy Archaeologist 35 times",
    "Defeat Chaos Fanatic 35 times",
    "Defeat King Black Dragon 45 times",
    "Complete 100 Barrows Chests",
    "Defeat Deranged Archaeologist 35 times",
    "Defeat 60 Dagannoth Rex",
    "Defeat Chaos Elemental 25 times",
    "Defeat Giant Mole 100 times",
    "Defeat Royal Titans 50 times",
    "Obtain a Fire Cape or Kill Jad 2 times if you already have Fire Cape",
    "Defeat Brutus 120 times",
    "Complete 35 rounds of Wintertodt (500+ points each)",
    "Subdue Tempoross and earn 120 Reward Permits",
    "Complete 25 rounds of Guardians of the Rift",
    "Complete 15 games of Pest Control (Novice, Intermediate, or Veteran)",
    "Complete 10 full runs (Waves 1-10) of Barbarian Assault",
    "Complete 100 laps of any Seers' Village or higher Agility Course",
    "Complete the Rogues outfit",
    "Complete ANY 4 Quests of your choice",
    "Complete 15 Easy Clue Scrolls",
    "Complete 10 Medium Clue Scrolls",
    "Complete ANY 3 Easy Achievement Diaries",
    "Complete ANY 2 Medium Achievement Diaries",
    "Gain 250,000 XP in Woodcutting",
    "Gain 250,000 XP in Mining",
    "Gain 250,000 XP in Fishing",
    "Gain 250,000 XP in Firemaking",
    "Gain 250,000 XP in Cooking",
    "Gain 250,000 XP in Thieving",
]

MID_TASKS = [
    "Defeat Zulrah 60 times",
    "Defeat Vorkath 60 times",
    "Defeat Phantom Muspah 35 times",
    "Defeat Sarachnis 100 times",
    "Defeat Grotesque Guardians 30 times",
    "Defeat Abyssal Sire 25 times",
    "Defeat Dagannoth Kings 100 times (Total combined kills)",
    "Defeat Kalphite Queen 20 times",
    "Defeat 50 Moons of Peril bosses (Eclipse, Blue, or Blood)",
    "Defeat Cave Kraken 75 times",
    "Defeat The Mad Angel 50 times",
    "Defeat Scorpia 35 times",
    "Defeat Shellbane Griffon 100 times",
    "Defeat Skotizo 2 times",
    "Complete 60 Barrows Chests",
    "Subdue Zalcano 35 times",
    "Complete 30 rounds of Guardians of the Rift",
    "Complete 5 High-Tier Barbarian Assault Wave 1-10 runs",
    (
        "Complete 'Song of the Elves', 'Desert Treasure II', or 'Sins of the"
        " Father' (If complete, any Grandmaster Quest)"
    ),
    "Complete 8 Hard Clue Scrolls",
    "Complete 10 Medium Clue Scrolls",
    "Complete ANY 1 Hard Achievement Diary",
    "Gain 250,000 XP in Agility",
    "Gain 250,000 XP in Hunter",
    "Gain 250,000 XP in Crafting",
    "Gain 250,000 XP in Smithing",
    "Gain 250,000 XP in Fletching",
    "Gain 250,000 XP in Runecraft",
]

HIGH_TASKS = [
    "Defeat Vardorvis 20 times",
    "Defeat Duke Sucellus 50 times",
    "Defeat The Leviathan 25 times",
    "Defeat The Whisperer 20 times",
    "Defeat Nex 100 times",
    "Defeat Cerberus 25 times",
    "Complete Corrupted Gauntlet 20 times",
    "Defeat Alchemical Hydra 50 times",
    "Defeat Araxxor 25 times",
    "Defeat Corporeal Beast 15 times",
    "Defeat Phosani's Nightmare 10 times",
    "Defeat Colosseum (Sol Heredit) 1 time OR complete 25 Colosseum waves",
    "Complete 10 Doom of Cacaguates Delves",
    "Defeat Maggot King 50 times",
    "Defeat Yama 50 times",
    "Complete 8 Chambers of Xeric (CoX)",
    "Complete 8 Tombs of Amascut (ToA - 300+ Invocation)",
    "Complete 5 Theatre of Blood (ToB)",
    "Complete 3 Elite Clue Scrolls",
    "Complete 1 Master Clue Scroll",
    "Complete ANY 1 Elite Achievement Diary",
    "Gain 250,000 XP in Construction",
    "Gain 250,000 XP in Herblore",
    "Gain 250,000 XP in Farming",
    "Gain 250,000 XP in Slayer",
]

# --- 3. BOT INITIALIZATION ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s) successfully.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


async def assign_task(
    interaction: discord.Interaction,
    task_pool: list,
    tier_name: str,
    required_bracket: str,
):
    user_id = interaction.user.id
    user = get_user_data(user_id)

    user_bracket = user.get("current_bracket")
    if not user_bracket:
        await interaction.response.send_message(
            "❌ You haven't registered yet! Please run `/signup` first.",
            ephemeral=True,
        )
        return

    if user_bracket != required_bracket:
        await interaction.response.send_message(
            f"🚫 **Wrong Tier!** You are in **{user_bracket}**. You cannot"
            f" request tasks from **{tier_name}**.",
            ephemeral=True,
        )
        return

    if user.get("current_task"):
        await interaction.response.send_message(
            f"❌ You already have an active task: **{user['current_task']}**!",
            ephemeral=True,
        )
        return

    now = datetime.datetime.utcnow()
    last_reset_val = user.get("last_task_reset")
    last_reset = (
        datetime.datetime.fromisoformat(last_reset_val)
        if last_reset_val
        else now
    )
    tasks_done_today = user.get("tasks_completed_today", 0) or 0

    if (now - last_reset).total_seconds() >= 86400:
        tasks_done_today = 0
        last_reset = now

    if tasks_done_today >= 4:
        await interaction.response.send_message(
            "⏳ You have reached your max limit of **4 completed tasks per 24"
            " hours**!",
            ephemeral=True,
        )
        return

    cooldown_val = user.get("cooldown_until")
    if cooldown_val:
        cooldown_time = datetime.datetime.fromisoformat(cooldown_val)
        if now < cooldown_time:
            remaining = int((cooldown_time - now).total_seconds() // 60)
            await interaction.response.send_message(
                f"⏱️ You are on cooldown for another **{remaining} minutes**!",
                ephemeral=True,
            )
            return

    assigned_task = random.choice(task_pool)

    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE users 
                SET current_task = %s, tasks_completed_today = %s, last_task_reset = %s, last_task_time = %s
                WHERE user_id = %s
            """,
                (
                    assigned_task,
                    tasks_done_today,
                    last_reset.isoformat(),
                    int(time.time()),
                    user_id,
                ),
            )
            conn.commit()

    announcement = (
        f"🏆 **Emberborn Task Manager** 🏆\n\n"
        f"{interaction.user.mention} has been assigned a"
        f" **{required_bracket}** task!\n\n"
        f"📜 **Task:** {assigned_task}\n"
        f"🔥 **Bracket:** {required_bracket}\n\n"
        f"Good luck! Use `/currenttask` to view requirements or `/taskcomplete` to"
        f" submit proof."
    )

    await interaction.response.send_message(announcement)


def is_valid_imgur_link(url: str) -> bool:
    pattern = r"^https?://(i\.)?imgur\.com/.*"
    return bool(re.match(pattern, url.strip()))


# --- 4. SLASH COMMANDS ---


@bot.tree.command(name="givetasklow", description="Request a Low Bracket task")
async def givetasklow(interaction: discord.Interaction):
    await assign_task(interaction, LOW_TASKS, "Low Bracket", "Low Bracket")


@bot.tree.command(name="givetaskmed", description="Request a Mid Bracket task")
async def givetaskmed(interaction: discord.Interaction):
    await assign_task(interaction, MID_TASKS, "Mid Bracket", "Mid Bracket")


@bot.tree.command(
    name="givetaskhigh", description="Request a High Bracket task"
)
async def givetaskhigh(interaction: discord.Interaction):
    await assign_task(interaction, HIGH_TASKS, "High Bracket", "High Bracket")


@bot.tree.command(
    name="currenttask", description="Check your active task and point total"
)
async def currenttask(interaction: discord.Interaction):
    user = get_user_data(interaction.user.id)
    points = user["points"]
    task = user["current_task"]
    bracket = user["current_bracket"]

    task_text = f"{task} ({bracket})" if task else "None"
    await interaction.response.send_message(
        f"📋 **Emberborn Task Status for {interaction.user.display_name}**\n"
        f"• **Active Task:** {task_text}\n"
        f"• **Points Balance:** {points} pt(s)",
        ephemeral=True,
    )


@bot.tree.command(
    name="taskcomplete", description="Complete your active task with proof links"
)
@app_commands.describe(
    before_image="Imgur link to your BEFORE screenshot",
    after_image="Imgur link to your AFTER screenshot",
)
async def taskcomplete(
    interaction: discord.Interaction, before_image: str, after_image: str
):
    await interaction.response.defer()

    user_id = interaction.user.id
    user = get_user_data(user_id)
    points = user["points"]
    current_task = user["current_task"]
    tasks_done_today = user["tasks_completed_today"] or 0

    if not current_task:
        await interaction.followup.send(
            "❌ You don't have an active task to complete!", ephemeral=True
        )
        return

    if not is_valid_imgur_link(before_image) or not is_valid_imgur_link(
        after_image
    ):
        await interaction.followup.send(
            "❌ **Submission Failed!** Both links must be valid **Imgur** URLs!",
            ephemeral=True,
        )
        return

    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE users 
                SET points = points + 1, current_task = NULL, tasks_completed_today = %s 
                WHERE user_id = %s
            """,
                (tasks_done_today + 1, user_id),
            )
            conn.commit()

    completion_msg = (
        f"🎉 **Task Completed!**\n\n"
        f"{interaction.user.mention} completed: **{current_task}**!\n\n"
        f"💰 Earned **1 point**! Balance: **{points + 1} points**.\n\n"
        f"📷 **Proof:**\n• Before: {before_image}\n• After: {after_image}"
    )

    await interaction.followup.send(completion_msg)


@bot.tree.command(
    name="skiptask", description="Spend 1 point to drop active task"
)
async def skiptask(interaction: discord.Interaction):
    user_id = interaction.user.id
    user = get_user_data(user_id)
    points = user["points"]

    if points < 1:
        await interaction.response.send_message(
            "❌ You need at least 1 point to skip!", ephemeral=True
        )
        return

    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE users 
                SET points = points - 1, current_task = NULL, cooldown_until = NULL, last_task_time = 0 
                WHERE user_id = %s
            """,
                (user_id,),
            )
            conn.commit()

    await interaction.response.send_message(
        f"⏩ **Task Skipped!** {interaction.user.mention} spent 1 point."
        f" Balance: **{points - 1} points**."
    )


@bot.tree.command(
    name="leaderboard", description="View the top Task Master point earners"
)
async def leaderboard(interaction: discord.Interaction):
    await interaction.response.defer()

    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT user_id, points FROM users WHERE points > 0 ORDER BY"
                " points DESC LIMIT 10"
            )
            top_users = cursor.fetchall()

    if not top_users:
        await interaction.followup.send("🏆 No points earned yet!")
        return

    leaderboard_msg = "🏆 **Emberborn Task Master Leaderboard** 🏆\n\n"
    for rank, (user_id, points) in enumerate(top_users, start=1):
        try:
            user = await bot.fetch_user(user_id)
            user_name = user.display_name if user else f"User ID {user_id}"
        except Exception:
            user_name = f"User ID {user_id}"

        medal = (
            "🥇"
            if rank == 1
            else "🥈"
            if rank == 2
            else "🥉"
            if rank == 3
            else f"`#{rank}`"
        )
        leaderboard_msg += f"{medal} **{user_name}** — {points} pt(s)\n"

    await interaction.followup.send(leaderboard_msg)


@bot.tree.command(
    name="checkplayers", description="Display full event roster details"
)
async def checkplayers(interaction: discord.Interaction):
    await interaction.response.defer()

    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(
                "SELECT * FROM users WHERE current_bracket IS NOT NULL"
            )
            registered_users = cursor.fetchall()

    if not registered_users:
        await interaction.followup.send(
            "⚠️ No players have signed up yet!", ephemeral=True
        )
        return

    for i in range(0, len(registered_users), 15):
        chunk = registered_users[i : i + 15]
        embed = discord.Embed(
            title="🏆 Event Roster & Player Overview",
            description=(
                f"Total Signed Up Participants: **{len(registered_users)}**"
            ),
            color=discord.Color.gold(),
        )

        for user_dict in chunk:
            discord_name = (
                user_dict.get("discord_name")
                or f"<@{user_dict['user_id']}>"
            )
            bracket = user_dict.get("current_bracket") or "Unassigned"
            combat_lvl = user_dict.get("combat_level") or 0
            total_lvl = user_dict.get("total_level") or 0
            points = user_dict.get("points") or 0
            task = user_dict.get("current_task") or "None"

            field_value = (
                f"**Bracket:** {bracket}\n"
                f"**Combat level:** {combat_lvl}\n"
                f"**Total level:** {total_lvl}\n"
                f"**Points:** {points}\n"
                f"**Current task:** {task}"
            )
            embed.add_field(
                name=f"👤 {discord_name}", value=field_value, inline=False
            )

        await interaction.followup.send(embed=embed)


# --- ADMIN COMMANDS ---


@bot.tree.command(
    name="adminaddplayer",
    description="[Admin] Manually register a player into the event roster.",
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    member="The Discord user to register",
    combat_level="Player's Combat Level",
    total_level="Player's Total Level",
    override_bracket="Force assign a specific bracket (Optional)",
)
@app_commands.choices(
    override_bracket=[
        app_commands.Choice(name="Low Bracket", value="Low Bracket"),
        app_commands.Choice(name="Mid Bracket", value="Mid Bracket"),
        app_commands.Choice(name="High Bracket", value="High Bracket"),
    ]
)
async def adminaddplayer(
    interaction: discord.Interaction,
    member: discord.Member,
    combat_level: int,
    total_level: int,
    override_bracket: app_commands.Choice[str] = None,
):
    if override_bracket:
        bracket_name = override_bracket.value
    else:
        if combat_level >= 115 or total_level >= 1750:
            bracket_name = "High Bracket"
        elif combat_level >= 105 or total_level >= 1540:
            bracket_name = "Mid Bracket"
        else:
            bracket_name = "Low Bracket"

    guild = interaction.guild
    for old_role_name in ["Low Bracket", "Mid Bracket", "High Bracket"]:
        old_role = discord.utils.get(guild.roles, name=old_role_name)
        if old_role and old_role in member.roles:
            try:
                await member.remove_roles(old_role)
            except discord.Forbidden:
                pass

    role = discord.utils.get(guild.roles, name=bracket_name)
    role_status = ""

    if role:
        try:
            await member.add_roles(role)
            role_status = f" and assigned the **@{bracket_name}** role"
        except discord.Forbidden:
            role_status = " (⚠️ Failed to assign role)"

    get_user_data(member.id)
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE users 
                SET current_bracket = %s, combat_level = %s, total_level = %s, discord_name = %s
                WHERE user_id = %s
            """,
                (
                    bracket_name,
                    combat_level,
                    total_level,
                    member.display_name,
                    member.id,
                ),
            )
            conn.commit()

    await interaction.response.send_message(
        f"✅ **Manually Registered {member.mention}!**\n"
        f"• **Combat Level:** {combat_level}\n"
        f"• **Total Level:** {total_level}\n"
        f"• **Bracket Assigned:** **{bracket_name}**{role_status}\n\n"
        f"They are now listed in `/checkplayers`!",
        ephemeral=True,
    )


class SignupModal(discord.ui.Modal, title="Trials of the Flame Registration"):
    combat_level = discord.ui.TextInput(
        label="Combat Level",
        placeholder="e.g. 105",
        min_length=1,
        max_length=3,
        required=True,
    )
    total_level = discord.ui.TextInput(
        label="Total Level",
        placeholder="e.g. 1450",
        min_length=1,
        max_length=4,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            cb = int(self.combat_level.value)
            total = int(self.total_level.value)
        except ValueError:
            await interaction.response.send_message(
                "❌ Enter valid numbers!", ephemeral=True
            )
            return

        if cb >= 115 or total >= 1750:
            bracket_name = "High Bracket"
        elif cb >= 105 or total >= 1540:
            bracket_name = "Mid Bracket"
        else:
            bracket_name = "Low Bracket"

        guild = interaction.guild
        role = discord.utils.get(guild.roles, name=bracket_name)
        if role:
            try:
                await interaction.user.add_roles(role)
            except discord.Forbidden:
                pass

        get_user_data(interaction.user.id)
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE users 
                    SET current_bracket = %s, combat_level = %s, total_level = %s, discord_name = %s
                    WHERE user_id = %s
                """,
                    (
                        bracket_name,
                        cb,
                        total,
                        interaction.user.display_name,
                        interaction.user.id,
                    ),
                )
                conn.commit()

        await interaction.response.send_message(
            f"✅ **Registered {interaction.user.mention}!** Assigned:"
            f" **{bracket_name}**.",
            ephemeral=True,
        )


@bot.tree.command(name="signup", description="Register for event")
async def signup(interaction: discord.Interaction):
    await interaction.response.send_modal(SignupModal())


# --- 5. START SERVER & BOT ---
if __name__ == "__main__":
    keep_alive()
    bot.run(
        os.environ.get(
            "DISCORD_TOKEN",
            "MTU0Mzg1NTI2MTEzODE1NzY1OQ.GK9r1k.B-D_PLpPkSb4Eaex4dEkjQcOeDxgjLyPbY4f44",
        )
    )
