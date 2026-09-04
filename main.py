import datetime
import os
import random
import re
import sqlite3
import time
from threading import Thread

import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask

# --- 0. KEEP-ALIVE WEB SERVER (FOR RENDER) ---
app = Flask("")


@app.route("/")
def home():
    return "Bot is alive and running!"


def run_flask():
    # Render automatically provides a PORT environment variable
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()


# --- 1. SETUP DATABASE ---
DB_FILE = "taskmaster.db"

with sqlite3.connect(DB_FILE) as conn:
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        points INTEGER DEFAULT 0,
        current_task TEXT DEFAULT NULL,
        current_bracket TEXT DEFAULT NULL,
        tasks_completed_today INTEGER DEFAULT 0,
        last_task_reset TEXT DEFAULT NULL,
        cooldown_until TEXT DEFAULT NULL,
        last_task_time INTEGER DEFAULT 0,
        combat_level INTEGER DEFAULT 0,
        total_level INTEGER DEFAULT 0,
        discord_name TEXT DEFAULT NULL
    )
    """)
    conn.commit()

# Automatic safety check to add any missing columns to an existing database
with sqlite3.connect(DB_FILE) as conn:
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(users)")
    existing_columns = [column[1] for column in cursor.fetchall()]

    if "current_bracket" not in existing_columns:
        cursor.execute(
            "ALTER TABLE users ADD COLUMN current_bracket TEXT DEFAULT NULL"
        )
    if "tasks_completed_today" not in existing_columns:
        cursor.execute(
            "ALTER TABLE users ADD COLUMN tasks_completed_today INTEGER DEFAULT"
            " 0"
        )
    if "last_task_reset" not in existing_columns:
        cursor.execute(
            "ALTER TABLE users ADD COLUMN last_task_reset TEXT DEFAULT NULL"
        )
    if "cooldown_until" not in existing_columns:
        cursor.execute(
            "ALTER TABLE users ADD COLUMN cooldown_until TEXT DEFAULT NULL"
        )
    if "last_task_time" not in existing_columns:
        cursor.execute(
            "ALTER TABLE users ADD COLUMN last_task_time INTEGER DEFAULT 0"
        )
    if "combat_level" not in existing_columns:
        cursor.execute(
            "ALTER TABLE users ADD COLUMN combat_level INTEGER DEFAULT 0"
        )
    if "total_level" not in existing_columns:
        cursor.execute(
            "ALTER TABLE users ADD COLUMN total_level INTEGER DEFAULT 0"
        )
    if "discord_name" not in existing_columns:
        cursor.execute(
            "ALTER TABLE users ADD COLUMN discord_name TEXT DEFAULT NULL"
        )
    conn.commit()


# --- 2. TASK LISTS ---
LOW_TASKS = [
    # Bosses & Monster Kills
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
    # Skilling Bosses & Minigames
    "Complete 35 rounds of Wintertodt (500+ points each)",
    "Subdue Tempoross and earn 120 Reward Permits",
    "Complete 25 rounds of Guardians of the Rift",
    "Complete 15 games of Pest Control (Novice, Intermediate, or Veteran)",
    "Complete 10 full runs (Waves 1-10) of Barbarian Assault",
    "Complete 100 laps of any Seers' Village or higher Agility Course",
    "Complete the Rogues outfit",
    # Quests, Clues & Diaries
    "Complete ANY 4 Quests of your choice",
    "Complete 15 Easy Clue Scrolls",
    "Complete 10 Medium Clue Scrolls",
    "Complete ANY 3 Easy Achievement Diaries",
    "Complete ANY 2 Medium Achievement Diaries",
    # Low Tier 250k XP Tasks
    "Gain 250,000 XP in Woodcutting",
    "Gain 250,000 XP in Mining",
    "Gain 250,000 XP in Fishing",
    "Gain 250,000 XP in Firemaking",
    "Gain 250,000 XP in Cooking",
    "Gain 250,000 XP in Thieving",
]

MID_TASKS = [
    # Bosses & Monster Kills
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
    # Skilling Bosses & Minigames
    "Complete 60 Barrows Chests",
    "Subdue Zalcano 35 times",
    "Complete 30 rounds of Guardians of the Rift",
    "Complete 5 High-Tier Barbarian Assault Wave 1-10 runs",
    # Quests, Clues & Diaries
    (
        "Complete 'Song of the Elves', 'Desert Treasure II', or 'Sins of the"
        " Father' (If complete, any Grandmaster Quest)"
    ),
    "Complete 8 Hard Clue Scrolls",
    "Complete 10 Medium Clue Scrolls",
    "Complete ANY 1 Hard Achievement Diary",
    # Mid Tier 250k XP Tasks (Non-Combat Skilling Only)
    "Gain 250,000 XP in Agility",
    "Gain 250,000 XP in Hunter",
    "Gain 250,000 XP in Crafting",
    "Gain 250,000 XP in Smithing",
    "Gain 250,000 XP in Fletching",
    "Gain 250,000 XP in Runecraft",
]

MED_TASKS = MID_TASKS  # Alias safety

HIGH_TASKS = [
    # Endgame Bosses
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
    # Raids
    "Complete 8 Chambers of Xeric (CoX)",
    "Complete 8 Tombs of Amascut (ToA - 300+ Invocation)",
    "Complete 5 Theatre of Blood (ToB)",
    # Clues & Diaries
    "Complete 3 Elite Clue Scrolls",
    "Complete 1 Master Clue Scroll",
    "Complete ANY 1 Elite Achievement Diary",
    # High Tier 250k XP Tasks (Non-Combat Skilling Only)
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
        print(f"Synced {len(synced)} command(s) successfully:")
        for cmd in synced:
            print(f" - /{cmd.name}")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


# Helper function to grab/create user profile
def get_user_data(user_id):
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row  # Enables column-name lookups
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()

        if row is None:
            cursor.execute(
                """
                INSERT INTO users (user_id, points, current_task, current_bracket, tasks_completed_today, last_task_reset, cooldown_until, last_task_time, combat_level, total_level, discord_name)
                VALUES (?, 0, NULL, NULL, 0, NULL, NULL, 0, 0, 0, NULL)
            """,
                (user_id,),
            )
            conn.commit()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()

        return row


# Helper function to assign task logic
async def assign_task(
    interaction: discord.Interaction,
    task_pool: list,
    tier_name: str,
    required_bracket: str,
):
    user_id = interaction.user.id
    user = get_user_data(user_id)
    user_dict = dict(user) if user else {}

    # 1. Enforce Bracket Match
    user_bracket = user_dict.get("current_bracket")
    if not user_bracket:
        await interaction.response.send_message(
            "❌ You haven't registered yet! Please run `/signup` first to get"
            " assigned to a bracket.",
            ephemeral=True,
        )
        return

    if user_bracket != required_bracket:
        await interaction.response.send_message(
            f"🚫 **Wrong Tier!** You are in the **{user_bracket}** bracket. "
            f"You cannot request tasks from **{tier_name}**.",
            ephemeral=True,
        )
        return

    # 2. Check for Active Task
    if user_dict.get("current_task"):
        await interaction.response.send_message(
            "❌ You already have an active task:"
            f" **{user_dict['current_task']}**!\nUse `/currenttask` to view it"
            " or `/taskcomplete` when finished.",
            ephemeral=True,
        )
        return

    # 3. Check 24-Hour Task Limit (Max 4 tasks per 24 hours)
    now = datetime.datetime.utcnow()
    last_reset_val = user_dict.get("last_task_reset")
    last_reset = (
        datetime.datetime.fromisoformat(last_reset_val)
        if last_reset_val
        else now
    )
    tasks_done_today = user_dict.get("tasks_completed_today", 0) or 0

    if (now - last_reset).total_seconds() >= 86400:
        tasks_done_today = 0
        last_reset = now

    if tasks_done_today >= 4:
        await interaction.response.send_message(
            "⏳ You have reached your max limit of **4 completed tasks per 24"
            " hours**! Try again later.",
            ephemeral=True,
        )
        return

    # 4. Check Cooldown Timer
    cooldown_val = user_dict.get("cooldown_until")
    if cooldown_val:
        cooldown_time = datetime.datetime.fromisoformat(cooldown_val)
        if now < cooldown_time:
            remaining = int((cooldown_time - now).total_seconds() // 60)
            await interaction.response.send_message(
                f"⏱️ You are on cooldown for another **{remaining} minutes**!"
                " Wait for it to expire or use `/skiptask`.",
                ephemeral=True,
            )
            return

    # 5. Assign Task
    assigned_task = random.choice(task_pool)

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE users 
            SET current_task = ?, tasks_completed_today = ?, last_task_reset = ?, last_task_time = ?
            WHERE user_id = ?
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
        f"**Rules:**\n"
        f"• PC users: Before & after screenshots with WOM / RuneLite"
        f" timestamp.\n"
        f"• Mobile users: Use the codeword for verification.\n\n"
        f"Good luck! Use `/currenttask` to view requirements or `/taskcomplete` to"
        f" submit proof."
    )

    await interaction.response.send_message(announcement)


# Validation function to ensure links are Imgur links
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
            "❌ **Submission Failed!** Both links must be valid **Imgur**"
            " URLs!\n*(Example format: `https://imgur.com/...` or"
            " `https://i.imgur.com/...`)*",
            ephemeral=True,
        )
        return

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE users 
            SET points = points + 1, current_task = NULL, tasks_completed_today = ? 
            WHERE user_id = ?
        """,
            (tasks_done_today + 1, user_id),
        )
        conn.commit()

    completion_msg = (
        f"🎉 **Task Completed!**\n\n"
        f"{interaction.user.mention} has completed their task:"
        f" **{current_task}**!\n\n"
        f"💰 Earned **1 point**! Current Balance: **{points + 1} points**.\n\n"
        f"📷 **Submitted Proof:**\n"
        f"• **Before:** {before_image}\n"
        f"• **After:** {after_image}"
    )

    await interaction.followup.send(completion_msg)


@bot.tree.command(
    name="skiptask",
    description="Spend 1 point to drop your active task & reset cooldown",
)
async def skiptask(interaction: discord.Interaction):
    user_id = interaction.user.id
    user = get_user_data(user_id)
    points = user["points"]

    if points < 1:
        await interaction.response.send_message(
            "❌ You need at least **1 point** to skip a task/cooldown!",
            ephemeral=True,
        )
        return

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE users 
            SET points = points - 1, current_task = NULL, cooldown_until = NULL, last_task_time = 0 
            WHERE user_id = ?
        """,
            (user_id,),
        )
        conn.commit()

    await interaction.response.send_message(
        f"⏩ **Task Skipped!** {interaction.user.mention} spent 1 point."
        f" Balance remaining: **{points - 1} points**.\nActive task and"
        " cooldown cleared—you can grab a new task now!"
    )


@bot.tree.command(
    name="leaderboard", description="View the top Task Master point earners"
)
async def leaderboard(interaction: discord.Interaction):
    await interaction.response.defer()

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, points FROM users WHERE points > 0 ORDER BY"
            " points DESC LIMIT 10"
        )
        top_users = cursor.fetchall()

    if not top_users:
        await interaction.followup.send(
            "🏆 No one has earned any points yet! Get out there and complete"
            " some tasks!"
        )
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
    name="checkplayers",
    description="Display full event roster details for all registered players.",
)
async def checkplayers(interaction: discord.Interaction):
    await interaction.response.defer()

    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM users WHERE current_bracket IS NOT NULL"
        )
        registered_users = cursor.fetchall()

    if not registered_users:
        await interaction.followup.send(
            "⚠️ No players have signed up for the event yet!", ephemeral=True
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

        for row in chunk:
            user_dict = dict(row)

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


@bot.tree.command(
    name="resetuser", description="[Admin Only] Reset a user's task and points"
)
@app_commands.checks.has_permissions(administrator=True)
async def resetuser(interaction: discord.Interaction, member: discord.Member):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE user_id = ?", (member.id,))
        conn.commit()

    await interaction.response.send_message(
        "🧹 **Admin Reset:** Successfully cleared all tasks, cooldowns, and"
        f" points for {member.mention}.",
        ephemeral=True,
    )


# --- ADMIN POINT MANAGEMENT COMMANDS ---


@bot.tree.command(
    name="addpoints", description="[Admin] Add points to a clan member"
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    member="The user to receive points", amount="Number of points to add"
)
async def addpoints(
    interaction: discord.Interaction, member: discord.Member, amount: int
):
    if amount <= 0:
        await interaction.response.send_message(
            "❌ Amount must be greater than 0!", ephemeral=True
        )
        return

    get_user_data(member.id)

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET points = points + ? WHERE user_id = ?",
            (amount, member.id),
        )
        conn.commit()
        cursor.execute(
            "SELECT points FROM users WHERE user_id = ?", (member.id,)
        )
        new_total = cursor.fetchone()[0]

    await interaction.response.send_message(
        f"✅ **Points Added:** Added **{amount} point(s)** to"
        f" {member.mention}.\n💰 New Balance: **{new_total} point(s)**."
    )


@bot.tree.command(
    name="removepoints", description="[Admin] Deduct points from a clan member"
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    member="The user to deduct points from",
    amount="Number of points to remove",
)
async def removepoints(
    interaction: discord.Interaction, member: discord.Member, amount: int
):
    if amount <= 0:
        await interaction.response.send_message(
            "❌ Amount must be greater than 0!", ephemeral=True
        )
        return

    user = get_user_data(member.id)
    points = user["points"]
    new_total = max(0, points - amount)

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET points = ? WHERE user_id = ?",
            (new_total, member.id),
        )
        conn.commit()

    await interaction.response.send_message(
        f"🧹 **Points Removed:** Removed **{amount} point(s)** from"
        f" {member.mention}.\n💰 New Balance: **{new_total} point(s)**."
    )


@bot.tree.command(
    name="setpoints",
    description="[Admin] Set a clan member's points to a specific amount",
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    member="The user to edit", amount="Exact point total to set"
)
async def setpoints(
    interaction: discord.Interaction, member: discord.Member, amount: int
):
    if amount < 0:
        await interaction.response.send_message(
            "❌ Point total cannot be negative!", ephemeral=True
        )
        return

    get_user_data(member.id)

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET points = ? WHERE user_id = ?", (amount, member.id)
        )
        conn.commit()

    await interaction.response.send_message(
        f"⚙️ **Points Updated:** Set {member.mention}'s point balance directly"
        f" to **{amount} point(s)**."
    )


# --- SIGNUP MODAL & COMMANDS ---


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
                "❌ Please enter valid numbers for Combat and Total Level!",
                ephemeral=True,
            )
            return

        # Determine Bracket
        if cb >= 115 or total >= 1750:
            bracket_name = "High Bracket"
        elif cb >= 105 or total >= 1540:
            bracket_name = "Mid Bracket"
        else:
            bracket_name = "Low Bracket"

        # Assign Discord Role
        guild = interaction.guild
        role = discord.utils.get(guild.roles, name=bracket_name)

        if role:
            try:
                await interaction.user.add_roles(role)
            except discord.Forbidden:
                await interaction.response.send_message(
                    f"⚠️ Registered for **{bracket_name}**, but I couldn't"
                    " assign the role! Check bot role permissions.",
                    ephemeral=True,
                )
                return

        # Ensure user exists in database & update profile details
        get_user_data(interaction.user.id)
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE users 
                SET current_bracket = ?, combat_level = ?, total_level = ?, discord_name = ?
                WHERE user_id = ?
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
            f"✅ **Registration Complete!**\n\n📊 **Submitted Stats:** {cb}"
            f" Combat | {total} Total Level\n🔥 **Assigned Bracket:**"
            f" **{bracket_name}**\n\nYou have been granted the"
            f" **@{bracket_name}** role and can now request tasks!",
            ephemeral=True,
        )


@bot.tree.command(
    name="signup",
    description=(
        "Register for Trials of the Flame and get your bracket role!"
    ),
)
async def signup(interaction: discord.Interaction):
    await interaction.response.send_modal(SignupModal())


@bot.tree.command(
    name="adminaddplayer",
    description="[Admin] Manually register a player into the event roster.",
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    member="The Discord user to register",
    combat_level="Player's Combat Level",
    total_level="Player's Total Level",
)
async def adminaddplayer(
    interaction: discord.Interaction,
    member: discord.Member,
    combat_level: int,
    total_level: int,
):
    if combat_level >= 115 or total_level >= 1750:
        bracket_name = "High Bracket"
    elif combat_level >= 105 or total_level >= 1540:
        bracket_name = "Mid Bracket"
    else:
        bracket_name = "Low Bracket"

    guild = interaction.guild
    role = discord.utils.get(guild.roles, name=bracket_name)
    role_status = ""

    if role:
        try:
            await member.add_roles(role)
            role_status = f" and assigned the **@{bracket_name}** role"
        except discord.Forbidden:
            role_status = " (⚠️ Failed to assign role—check bot permissions)"

    get_user_data(member.id)
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE users 
            SET current_bracket = ?, combat_level = ?, total_level = ?, discord_name = ?
            WHERE user_id = ?
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
        f"They are now fully listed in the event roster for `/checkplayers`!",
        ephemeral=True,
    )


# Handle missing administrator permissions gracefully
@addpoints.error
@removepoints.error
@setpoints.error
@resetuser.error
@adminaddplayer.error
async def admin_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message(
            "🔒 **Access Denied:** You must be a Server Administrator to use"
            " this command!",
            ephemeral=True,
        )


# --- 5. START UP KEEP-ALIVE SERVER AND RUN BOT ---
if __name__ == "__main__":
    keep_alive()
    bot.run(
        os.environ.get(
            "DISCORD_TOKEN",
            "MTU0Mzg1NTI2MTEzODE1NzY1OQ.G8VENk.iUx8kWB-Z9TKgsx6wOF7XF_y3NfpJ0aBlwzF9I",
        )
    )
