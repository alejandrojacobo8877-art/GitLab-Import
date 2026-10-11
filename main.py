# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportOptionalSubscript=false, reportIndexIssue=false, reportArgumentType=false, reportCallIssue=false, reportOperatorIssue=false
# ^ Silences VS Code (Pylance) type-hint warnings caused by supabase-py's loose JSON typing.
#   These are editor-only; they never affect the bot at runtime. Real errors (syntax, undefined names) still show.
"""
Task Manager / Weekly Challenges Discord Bot
============================================
FILE LAYOUT (search for the "PART" numbers to jump around):
  PART 1   Imports
  PART 2   Configuration & constants
  PART 3   Keep-alive web server (Render)
  PART 4   Databases (Postgres + Supabase client)
  PART 5   Legacy task pools (reference only)
  PART 6   Helper functions
  PART 7   Bot instance & on_ready
  PART 8   UI: views & modals
  PART 9   Core logic (assign_task)
  PART 10  Permission checks
  PART 11  Player commands
  PART 12  Point store commands
  PART 13  Staff commands
  PART 14  Launcher
"""



# ==============================================================================
# PART 1: IMPORTS
# ==============================================================================


import asyncio
import datetime
import os
import random
import re
import time
import urllib.parse
from threading import Thread
from typing import Optional
from collections import defaultdict, Counter

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask
import psycopg2
import psycopg2.extras
from supabase import create_client, Client


# ==============================================================================
# PART 2: CONFIGURATION & CONSTANTS
# Edit IDs, role names, and store prices here.
# ==============================================================================


# --- Credentials / connection strings ---
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres.lrhcfqmykacliddytews:Foreveryoungmentally666@aws-0-eu-central-1.pooler.supabase.com:6543/postgres",
)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://lrhcfqmykacliddytews.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "sb_secret_ak8yFEvJ1_Ye7y0SYjDiOQ_4MFc4I4y")
DISCORD_TOKEN = os.environ.get(
    "DISCORD_TOKEN",
    "MTU0Mzg1NTI2MTEzODE1NzY1OQ.GK9r1k.B-D_PLpPkSb4Eaex4dEkjQcOeDxgjLyPbY4f44",
)


# --- Hosting mode ---
# Keep-alive web server (Part 3). Leave False when running on your own PC.
# Set to True when hosting on Render or a similar service that needs a web port.
# You can also switch it without editing code:  ENABLE_KEEP_ALIVE=true
ENABLE_KEEP_ALIVE = os.environ.get("ENABLE_KEEP_ALIVE", "false").strip().lower() in ("1", "true", "yes", "on")

# --- System state ---
TASKS_LOCKED = True
LOCK_TIMER_TASK = None


# --- Discord IDs & roles ---
AUTHORIZED_ROLE_NAMES = ["Owner", "Admin", "Aegis Warrior"]
ADMIN_FEEDBACK_CHANNEL_ID = 1548871829060321372


# --- Point store config (costs / limits) ---
STORE_STAFF_CHANNEL_ID = 1340799931220496505

# Create a Discord role (e.g. "⭐ Store Star"), then paste its role ID here
COSMETIC_ROLE_ID = 0  # <-- REPLACE with your real role ID

STORE_ITEMS = {
    "bond": {
        "name": "Bond Redemption",
        "cost": 25,
        "description": "Redeem for a bond (staff fulfills). **1 per calendar month.**",
        "limit": "monthly",
    },
    "raffle": {
        "name": "Extra Raffle Entry",
        "cost": 2,
        "description": "Adds 1 bonus raffle entry for the current event. No limit.",
        "limit": "none",
    },
    "free_skip": {
        "name": "Free Task Skip",
        "cost": 0,
        "description": "Skip your current task without the normal skip cost. **1 per week.**",
        "limit": "weekly",
    },
    "double_buff": {
        "name": "Double Points (Next Task)",
        "cost": 1,
        "description": "Your next verified task awards **double points**. **1 per week.**",
        "limit": "weekly",
    },
    "cosmetic": {
        "name": "Cosmetic Role (7 days)",
        "cost": 100000,
        "description": "Temporary flex role next to your name for 7 days. No gameplay boost.",
        "limit": "none",
    },
}


# ==============================================================================
# PART 3: KEEP-ALIVE WEB SERVER (RENDER)
# ==============================================================================


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


# ==============================================================================
# PART 4: DATABASES
# 4A = Postgres (psycopg2), 4B = Supabase API client.
# ==============================================================================


# --- 4A. Postgres: connection, schema, user data, lock state ---
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
                slayer_level INT DEFAULT 1,
                discord_name TEXT DEFAULT NULL,
                low_tier_streak INT DEFAULT 0
            );
            """)
            cursor.execute("""
            ALTER TABLE users ADD COLUMN IF NOT EXISTS low_tier_streak INT DEFAULT 0;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS slayer_level INT DEFAULT 1;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS discord_name TEXT DEFAULT NULL;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS store_double_buff BOOLEAN DEFAULT FALSE;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS cosmetic_role_until TIMESTAMPTZ;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS active_bless_buff BOOLEAN DEFAULT FALSE;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS bless_uses_this_week INT DEFAULT 0;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS last_reset_event_id TEXT DEFAULT NULL;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS leader_perks_available INT DEFAULT 0;
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                id INT PRIMARY KEY,
                tasks_locked BOOLEAN DEFAULT FALSE
            );
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_task_offers (
                user_id TEXT PRIMARY KEY,
                discord_name TEXT,
                task_id_1 TEXT,
                task_id_2 TEXT,
                task_id_3 TEXT
            );
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS store_purchases (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                item_key TEXT NOT NULL,
                cost INT NOT NULL,
                event_id TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """)
            cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_store_purchases_user_item
            ON store_purchases (user_id, item_key, created_at);
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS store_item_costs (
                item_key TEXT PRIMARY KEY,
                cost INT NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                updated_by TEXT
            );
            """)
            conn.commit()

def get_user_data(user_id):
    init_db()
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()

            if row is None:
                cursor.execute(
                    """
                    INSERT INTO users (user_id, points, current_task, current_bracket, tasks_completed_today, last_task_reset, cooldown_until, last_task_time, combat_level, total_level, slayer_level, discord_name, low_tier_streak)
                    VALUES (%s, 0, NULL, NULL, 0, NULL, NULL, 0, 0, 0, 1, NULL, 0)
                    RETURNING *;
                """,
                    (user_id,),
                )
                conn.commit()
                row = cursor.fetchone()

            return row

def is_tasks_locked() -> bool:
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT tasks_locked FROM bot_settings WHERE id = 1;")
            result = cursor.fetchone()
            if result is not None:
                return result[0]
            return False

def set_tasks_locked(locked: bool):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO bot_settings (id, tasks_locked)
                VALUES (1, %s)
                ON CONFLICT (id) DO UPDATE SET tasks_locked = EXCLUDED.tasks_locked;
                """,
                (locked,),
            )
            conn.commit()


# --- 4B. Supabase API client ---
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ==============================================================================
# PART 5: LEGACY TASK POOLS (REFERENCE ONLY)
# Not used by the bot at runtime - live tasks come from the Supabase `tasks` table.
# ==============================================================================


LOW_TASKS = {
    # ================= BOSSES & MONSTERS =================
    "Defeat Obor": {
        "slayer": 1, "quest": "None", "desc": "KC ONLY COUNTS IF YOU OPEN CHEST",
        "tiers": [
            {"kc": 30, "points": 1, "weight": 20},
            {"kc": 45, "points": 2, "weight": 20},
            {"kc": 60, "points": 3, "weight": 20},
        ]
    },
    "Defeat Bryophyta": {
        "slayer": 1, "quest": "None", "desc": "KC ONLY COUNTS IF YOU OPEN CHEST",
        "tiers": [
            {"kc": 30, "points": 1, "weight": 16},
            {"kc": 45, "points": 2, "weight": 16},
            {"kc": 60, "points": 3, "weight": 16},
        ]
    },
    "Defeat Scurrius": {
        "slayer": 1, "quest": "None", "desc": "RIGHT CLICK PRIVATE FOR SOLO SCURRI",
        "tiers": [
            {"kc": 100, "points": 1, "weight": 35},
            {"kc": 150, "points": 2, "weight": 35},
            {"kc": 200, "points": 3, "weight": 35},
        ]
    },
    "Defeat Crazy Archaeologist": {
        "slayer": 1, "quest": "None", "desc": "WILDERNESS!",
        "tiers": [
            {"kc": 100, "points": 1, "weight": 5},
            {"kc": 150, "points": 2, "weight": 5},
            {"kc": 200, "points": 3, "weight": 5},
        ]
    },
    "Defeat Chaos Fanatic": {
        "slayer": 1, "quest": "None", "desc": "WILDERNESS!",
        "tiers": [
            {"kc": 50, "points": 1, "weight": 5},
            {"kc": 75, "points": 2, "weight": 5},
            {"kc": 100, "points": 3, "weight": 5},
        ]
    },
    "Defeat King Black Dragon": {
        "slayer": 1, "quest": "None", "desc": "TEMP WILDERNESS!",
        "tiers": [
            {"kc": 50, "points": 1, "weight": 10},
            {"kc": 75, "points": 2, "weight": 10},
            {"kc": 100, "points": 3, "weight": 10},
        ]
    },
    "Defeat Deranged Archaeologist": {
        "slayer": 1, "quest": "Bone Voyage", "desc": "",
        "tiers": [
            {"kc": 100, "points": 1, "weight": 15},
            {"kc": 150, "points": 2, "weight": 15},
            {"kc": 200, "points": 3, "weight": 15},
        ]
    },
    "Defeat Dagannoth Rex": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 75, "points": 1, "weight": 15},
            {"kc": 110, "points": 2, "weight": 15},
            {"kc": 150, "points": 3, "weight": 15},
        ]
    },
    "Defeat Chaos Elemental": {
        "slayer": 1, "quest": "None", "desc": "WILDERNESS!",
        "tiers": [
            {"kc": 10, "points": 1, "weight": 5},
            {"kc": 20, "points": 2, "weight": 5},
            {"kc": 30, "points": 3, "weight": 5},
        ]
    },
    "Defeat Giant Mole": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 100, "points": 1, "weight": 15},
            {"kc": 150, "points": 2, "weight": 15},
            {"kc": 200, "points": 3, "weight": 15},
        ]
    },
    "Defeat Royal Titans": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 50, "points": 1, "weight": 25},
            {"kc": 75, "points": 2, "weight": 25},
            {"kc": 100, "points": 3, "weight": 25},
        ]
    },
    "Obtain Fire Cape or Kill Jad": {
        "slayer": 1, "quest": "None", "desc": "Kill Jad if you already have Fire Cape",
        "tiers": [
            {"kc": 2/1, "points": 1, "weight": 35},
            {"kc": 3, "points": 2, "weight": 35},
            {"kc": 4, "points": 3, "weight": 35},
        ]
    },
    "Defeat Brutus": {
        "slayer": 1, "quest": "The Ides of Milk", "desc": "",
        "tiers": [
            {"kc": 120, "points": 1, "weight": 35},
            {"kc": 180, "points": 2, "weight": 35},
            {"kc": 240, "points": 3, "weight": 35},
        ]
    },

    # ================= SLAYER TASKS =================
    "Complete slayer tasks": {
        "slayer": 1, "quest": "None", "desc": "No Turael skipping, use your highest slayer master for your level, must show proof of streak in SS",
        "tiers": [
            {"kc": 5, "points": 1, "weight": 35},
            {"kc": 8, "points": 2, "weight": 35},
            {"kc": 10, "points": 3, "weight": 35},
        ]
    },
    "Slay Cave Crawlers": {
        "slayer": 10, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 100, "points": 1, "weight": 20},
            {"kc": 150, "points": 2, "weight": 20},
            {"kc": 250, "points": 3, "weight": 20},
        ]
    },
    "Slay Crawling Hands": {
        "slayer": 15, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 75, "points": 1, "weight": 20},
            {"kc": 175, "points": 2, "weight": 20},
            {"kc": 350, "points": 3, "weight": 20},
        ]
    },
    "Slay Banshees": {
        "slayer": 15, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 100, "points": 1, "weight": 20},
            {"kc": 150, "points": 2, "weight": 20},
            {"kc": 250, "points": 3, "weight": 20},
        ]
    },
    "Slay Rockslugs": {
        "slayer": 20, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 150, "points": 1, "weight": 20},
            {"kc": 200, "points": 2, "weight": 20},
            {"kc": 250, "points": 3, "weight": 20},
        ]
    },
    "Slay Cockatrice": {
        "slayer": 25, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 100, "points": 1, "weight": 20},
            {"kc": 150, "points": 2, "weight": 20},
            {"kc": 250, "points": 3, "weight": 20},
        ]
    },
    "Slay Pyrefiends": {
        "slayer": 30, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 150, "points": 1, "weight": 20},
            {"kc": 200, "points": 2, "weight": 20},
            {"kc": 250, "points": 3, "weight": 20},
        ]
    },
    "Slay Basilisks": {
        "slayer": 40, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 75, "points": 1, "weight": 20},
            {"kc": 125, "points": 2, "weight": 20},
            {"kc": 250, "points": 3, "weight": 20},
        ]
    },
    "Slay Brine Rats": {
        "slayer": 47, "quest": "Olaf's Quest", "desc": "",
        "tiers": [
            {"kc": 200, "points": 1, "weight": 20},
            {"kc": 275, "points": 2, "weight": 20},
            {"kc": 450, "points": 3, "weight": 20},
        ]
    },
    "Slay Bloodvelds": {
        "slayer": 50, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 125, "points": 1, "weight": 20},
            {"kc": 175, "points": 2, "weight": 20},
            {"kc": 225, "points": 3, "weight": 20},
        ]
    },
    "Slay Shellbane Gryphons": {
        "slayer": 53, "quest": "At First Light", "desc": "",
        "tiers": [
            {"kc": 50, "points": 1, "weight": 20},
            {"kc": 75, "points": 2, "weight": 20},
            {"kc": 100, "points": 3, "weight": 20},
        ]
    },
    "Slay Turoths": {
        "slayer": 55, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 100, "points": 1, "weight": 20},
            {"kc": 150, "points": 2, "weight": 20},
            {"kc": 200, "points": 3, "weight": 20},
        ]
    },
    "Slay Cave Horrors": {
        "slayer": 58, "quest": "Cabin Fever", "desc": "",
        "tiers": [
            {"kc": 100, "points": 1, "weight": 20},
            {"kc": 150, "points": 2, "weight": 20},
            {"kc": 200, "points": 3, "weight": 20},
        ]
    },
    "Slay Warped Creatures": {
        "slayer": 56, "quest": "The Path of Glouphrie", "desc": "",
        "tiers": [
            {"kc": 100, "points": 1, "weight": 20},
            {"kc": 150, "points": 2, "weight": 20},
            {"kc": 200, "points": 3, "weight": 20},
        ]
    },
    "Slay Vyres": {
        "slayer": 1, "quest": "Sins of the Father", "desc": "",
        "tiers": [
            {"kc": 100, "points": 1, "weight": 20},
            {"kc": 150, "points": 2, "weight": 20},
            {"kc": 200, "points": 3, "weight": 20},
        ]
    },

    # ================= MINIGAMES =================
    "Complete Barrows Chests": {
        "slayer": 1, "quest": "Priest in Peril", "desc": "",
        "tiers": [
            {"kc": 100, "points": 1, "weight": 25},
            {"kc": 150, "points": 2, "weight": 25},
            {"kc": 200, "points": 3, "weight": 25},
        ]
    },
    "Complete rounds of Wintertodt": {
        "slayer": 1, "quest": "None", "desc": "500+ points each",
        "tiers": [
            {"kc": 25, "points": 1, "weight": 15},
            {"kc": 40, "points": 2, "weight": 15},
            {"kc": 50, "points": 3, "weight": 15},
        ]
    },
    "Subdue Tempoross": {
        "slayer": 1, "quest": "Sea Slug", "desc": "Earn Reward Permits",
        "tiers": [
            {"kc": 80, "points": 1, "weight": 15},
            {"kc": 120, "points": 2, "weight": 15},
            {"kc": 160, "points": 3, "weight": 15},
        ]
    },
    "Complete rounds of Guardians of the Rift": {
        "slayer": 1, "quest": "Temple of the Eye", "desc": "",
        "tiers": [
            {"kc": 25, "points": 1, "weight": 15},
            {"kc": 40, "points": 2, "weight": 15},
            {"kc": 50, "points": 3, "weight": 15},
        ]
    },
    "Complete games of Pest Control": {
        "slayer": 1, "quest": "Priest in Peril", "desc": "Novice, Intermediate, or Veteran",
        "tiers": [
            {"kc": 25, "points": 1, "weight": 25},
            {"kc": 40, "points": 2, "weight": 25},
            {"kc": 50, "points": 3, "weight": 25},
        ]
    },
    "Barbarian Assault Waves 1-10 / Fighter Torso": {
        "slayer": 1, "quest": "None", "desc": "Complete full runs or obtain Fighter Torso",
        "tiers": [
            {"kc": 5/1, "points": 1, "weight": 15},
            {"kc": 8, "points": 2, "weight": 15},
            {"kc": 10, "points": 3, "weight": 15},
        ]
    },
    "Complete the Rogues outfit": {
        "slayer": 1, "quest": "The Feud / Devious Minds", "desc": "",
        "tiers": [
            {"kc": 1, "points": 1, "weight": 25},
        ]
    },

    # ================= QUESTS, CLUES, DIARIES, AGILITY & EXP =================
    "Complete Quests of your choice": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 5, "points": 1, "weight": 30},
            {"kc": 8, "points": 2, "weight": 30},
            {"kc": 10, "points": 3, "weight": 30},
        ]
    },
    "Complete Easy Clue Scrolls": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 10, "points": 1, "weight": 25},
            {"kc": 15, "points": 2, "weight": 25},
            {"kc": 20, "points": 3, "weight": 25},
        ]
    },
    "Complete Medium Clue Scrolls": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 5, "points": 1, "weight": 25},
            {"kc": 10, "points": 2, "weight": 25},
            {"kc": 15, "points": 3, "weight": 25},
        ]
    },
    "Complete Easy Achievement Diaries": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 3, "points": 1, "weight": 15},
            {"kc": 5, "points": 2, "weight": 15},
            {"kc": 7, "points": 3, "weight": 15},
        ]
    },
    "Complete Medium Achievement Diaries": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 2, "points": 1, "weight": 5},
            {"kc": 4, "points": 2, "weight": 5},
            {"kc": 6, "points": 3, "weight": 5},
        ]
    },
    "Complete Laps (Seers' Village or higher)": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 200, "points": 1, "weight": 25},
            {"kc": 300, "points": 2, "weight": 25},
            {"kc": 400, "points": 3, "weight": 25},
        ]
    },
    "Gain XP in Woodcutting": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 150000, "points": 1, "weight": 15},
            {"kc": 250000, "points": 2, "weight": 15},
            {"kc": 350000, "points": 3, "weight": 15},
        ]
    },
    "Gain XP in Mining": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 150000, "points": 1, "weight": 15},
            {"kc": 250000, "points": 2, "weight": 15},
            {"kc": 350000, "points": 3, "weight": 15},
        ]
    },
    "Gain XP in Fishing": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 150000, "points": 1, "weight": 20},
            {"kc": 250000, "points": 2, "weight": 20},
            {"kc": 350000, "points": 3, "weight": 20},
        ]
    },
    "Gain XP in Firemaking": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 150000, "points": 1, "weight": 25},
            {"kc": 250000, "points": 2, "weight": 25},
            {"kc": 350000, "points": 3, "weight": 25},
        ]
    },
    "Gain XP in Cooking": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 150000, "points": 1, "weight": 25},
            {"kc": 250000, "points": 2, "weight": 25},
            {"kc": 350000, "points": 3, "weight": 25},
        ]
    },
    "Gain XP in Thieving": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 150000, "points": 1, "weight": 15},
            {"kc": 250000, "points": 2, "weight": 15},
            {"kc": 350000, "points": 3, "weight": 15},
        ]
    },
}

MID_TASKS = {
    # ================= BOSSES & MONSTERS =================
    "Defeat Zulrah": {
        "slayer": 1, "quest": "Regicide", "desc": "",
        "tiers": [
            {"kc": 50, "points": 1, "weight": 15},
            {"kc": 100, "points": 2, "weight": 15},
            {"kc": 135, "points": 3, "weight": 15},
        ]
    },
    "Defeat Vorkath": {
        "slayer": 1, "quest": "Dragon Slayer II", "desc": "",
        "tiers": [
            {"kc": 50, "points": 1, "weight": 20},
            {"kc": 100, "points": 2, "weight": 20},
            {"kc": 135, "points": 3, "weight": 20},
        ]
    },
    "Defeat Phantom Muspah": {
        "slayer": 1, "quest": "Secrets of the North", "desc": "",
        "tiers": [
            {"kc": 50, "points": 1, "weight": 15},
            {"kc": 75, "points": 2, "weight": 15},
            {"kc": 100, "points": 3, "weight": 15},
        ]
    },
    "Defeat Sarachnis": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 125, "points": 1, "weight": 25},
            {"kc": 175, "points": 2, "weight": 25},
            {"kc": 225, "points": 3, "weight": 25},
        ]
    },
    "Defeat Dagannoth Kings": {
        "slayer": 1, "quest": "None", "desc": "Total combined kills",
        "tiers": [
            {"kc": 125, "points": 1, "weight": 20},
            {"kc": 175, "points": 2, "weight": 20},
            {"kc": 225, "points": 3, "weight": 20},
        ]
    },
    "Defeat Kalphite Queen": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 20, "points": 1, "weight": 10},
            {"kc": 35, "points": 2, "weight": 10},
            {"kc": 50, "points": 3, "weight": 10},
        ]
    },
    "Defeat Moons of Peril bosses": {
        "slayer": 1, "quest": "Perilous Moons", "desc": "SS OF YOUR KC aka CHEST OPENED",
        "tiers": [
            {"kc": 50, "points": 1, "weight": 20},
            {"kc": 75, "points": 2, "weight": 20},
            {"kc": 100, "points": 3, "weight": 20},
        ]
    },
    "Defeat The Mad Angel": {
        "slayer": 1, "quest": "Fallen From Grace", "desc": "",
        "tiers": [
            {"kc": 50, "points": 1, "weight": 15},
            {"kc": 75, "points": 2, "weight": 15},
            {"kc": 100, "points": 3, "weight": 15},
        ]
    },
    "Defeat Scorpia": {
        "slayer": 1, "quest": "None", "desc": "WILDERNESS!",
        "tiers": [
            {"kc": 50, "points": 1, "weight": 20},
            {"kc": 75, "points": 2, "weight": 20},
            {"kc": 150, "points": 3, "weight": 20},
        ]
    },
    "Defeat Skotizo": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 1, "points": 1, "weight": 15},
            {"kc": 3, "points": 2, "weight": 15},
            {"kc": 6, "points": 3, "weight": 15},
        ]
    },
    "Defeat Revenant Orks": {
        "slayer": 1, "quest": "None", "desc": "WILDERNESS!",
        "tiers": [
            {"kc": 225, "points": 1, "weight": 15},
            {"kc": 265, "points": 2, "weight": 15},
            {"kc": 325, "points": 3, "weight": 15},
        ]
    },
    "Defeat Revenant Dark Beast": {
        "slayer": 1, "quest": "None", "desc": "WILDERNESS!",
        "tiers": [
            {"kc": 200, "points": 1, "weight": 15},
            {"kc": 245, "points": 2, "weight": 15},
            {"kc": 305, "points": 3, "weight": 15},
        ]
    },

    # ================= SLAYER TASKS =================
    "Slay Cave Horrors (Mid)": {
        "slayer": 58, "quest": "Cabin Fever", "desc": "",
        "tiers": [
            {"kc": 150, "points": 1, "weight": 20},
            {"kc": 225, "points": 2, "weight": 20},
            {"kc": 300, "points": 3, "weight": 20},
        ]
    },
    "Slay Aberrant Spectres": {
        "slayer": 60, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 150, "points": 1, "weight": 20},
            {"kc": 225, "points": 2, "weight": 20},
            {"kc": 300, "points": 3, "weight": 20},
        ]
    },
    "Slay Basilisk Knights": {
        "slayer": 60, "quest": "The Fremennik Exiles", "desc": "",
        "tiers": [
            {"kc": 150, "points": 1, "weight": 15},
            {"kc": 200, "points": 2, "weight": 15},
            {"kc": 250, "points": 3, "weight": 15},
        ]
    },
    "Slay Wyrms or Strykewyrms": {
        "slayer": 62, "quest": "Tail of Two Cats / Dragon Slayer II", "desc": "",
        "tiers": [
            {"kc": 150, "points": 1, "weight": 20},
            {"kc": 225, "points": 2, "weight": 20},
            {"kc": 300, "points": 3, "weight": 20},
        ]
    },
    "Slay Spiritual Warriors": {
        "slayer": 63, "quest": "Troll Stronghold / Eadgar's Ruse", "desc": "",
        "tiers": [
            {"kc": 150, "points": 1, "weight": 15},
            {"kc": 225, "points": 2, "weight": 15},
            {"kc": 300, "points": 3, "weight": 15},
        ]
    },
    "Slay Dust Devils": {
        "slayer": 65, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 150, "points": 1, "weight": 20},
            {"kc": 225, "points": 2, "weight": 20},
            {"kc": 300, "points": 3, "weight": 20},
        ]
    },
    "Slay Spiritual Rangers": {
        "slayer": 63, "quest": "Troll Stronghold / Eadgar's Ruse", "desc": "",
        "tiers": [
            {"kc": 250, "points": 1, "weight": 15},
            {"kc": 350, "points": 2, "weight": 15},
            {"kc": 450, "points": 3, "weight": 15},
        ]
    },
    "Slay Fossil Island Wyverns": {
        "slayer": 66, "quest": "Bone Voyage", "desc": "",
        "tiers": [
            {"kc": 150, "points": 1, "weight": 15},
            {"kc": 200, "points": 2, "weight": 15},
            {"kc": 250, "points": 3, "weight": 15},
        ]
    },
    "Slay Skeletal Wyverns": {
        "slayer": 72, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 150, "points": 1, "weight": 15},
            {"kc": 200, "points": 2, "weight": 15},
            {"kc": 250, "points": 3, "weight": 15},
        ]
    },
    "Slay Gargoyles OR Grotesque Guardians": {
        "slayer": 75, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 150/50, "points": 1, "weight": 15},
            {"kc": 225/120, "points": 2, "weight": 15},
            {"kc": 300/150, "points": 3, "weight": 15},
        ]
    },
    "Slay Mutated Custodian Stalkers": {
        "slayer": 77, "quest": "Children of the Sun", "desc": "",
        "tiers": [
            {"kc": 125, "points": 1, "weight": 15},
            {"kc": 175, "points": 2, "weight": 15},
            {"kc": 225, "points": 3, "weight": 15},
        ]
    },
    "Slay Venators": {
        "slayer": 80, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 125, "points": 1, "weight": 15},
            {"kc": 175, "points": 2, "weight": 15},
            {"kc": 225, "points": 3, "weight": 15},
        ]
    },

    # ================= MINIGAMES =================
    "Complete Barrows Chests (Mid)": {
        "slayer": 1, "quest": "Priest in Peril", "desc": "",
        "tiers": [
            {"kc": 125, "points": 1, "weight": 25},
            {"kc": 175, "points": 2, "weight": 25},
            {"kc": 225, "points": 3, "weight": 25},
        ]
    },
    "Guardians of the Rift (Mid)": {
        "slayer": 1, "quest": "Temple of the Eye", "desc": "",
        "tiers": [
            {"kc": 35, "points": 1, "weight": 25},
            {"kc": 50, "points": 2, "weight": 25},
            {"kc": 70, "points": 3, "weight": 25},
        ]
    },
    "Barbarian Assault Wave 1-10 (High-Tier)": {
        "slayer": 1, "quest": "None", "desc": "Must take picture of each run to confirm",
        "tiers": [
            {"kc": 5, "points": 1, "weight": 25},
            {"kc": 8, "points": 2, "weight": 25},
            {"kc": 12, "points": 3, "weight": 25},
        ]
    },

    # ================= QUESTS, CLUES, DIARIES & EXP =================
    "Complete Grandmaster Quest": {
        "slayer": 1, "quest": "Respective Quest Line Requirements", "desc": "",
        "tiers": [
            {"kc": 1, "points": 1, "weight": 30},
            {"kc": 2, "points": 2, "weight": 30},
        ]
    },
    "Complete Hard Clue Scrolls": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 5, "points": 1, "weight": 10},
            {"kc": 10, "points": 2, "weight": 10},
            {"kc": 15, "points": 3, "weight": 10},
        ]
    },
    "Complete Medium Clue Scrolls (Mid)": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 10, "points": 1, "weight": 20},
            {"kc": 15, "points": 2, "weight": 20},
            {"kc": 20, "points": 3, "weight": 20},
        ]
    },
    "Complete Hard Achievement Diary": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 1, "points": 1, "weight": 5},
            {"kc": 2, "points": 2, "weight": 5},
            {"kc": 3, "points": 3, "weight": 5},
        ]
    },
    "Gain XP in Agility": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 125000, "points": 1, "weight": 15},
            {"kc": 200000, "points": 2, "weight": 15},
            {"kc": 300000, "points": 3, "weight": 15},
        ]
    },
    "Gain XP in Hunter": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 150000, "points": 1, "weight": 15},
            {"kc": 250000, "points": 2, "weight": 15},
            {"kc": 350000, "points": 3, "weight": 15},
        ]
    },
    "Gain XP in Crafting": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 150000, "points": 1, "weight": 25},
            {"kc": 250000, "points": 2, "weight": 25},
            {"kc": 350000, "points": 3, "weight": 25},
        ]
    },
    "Gain XP in Smithing": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 150000, "points": 1, "weight": 25},
            {"kc": 250000, "points": 2, "weight": 25},
            {"kc": 350000, "points": 3, "weight": 25},
        ]
    },
    "Gain XP in Fletching": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 150000, "points": 1, "weight": 20},
            {"kc": 250000, "points": 2, "weight": 20},
            {"kc": 350000, "points": 3, "weight": 20},
        ]
    },
    "Gain XP in Runecraft": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 100000, "points": 1, "weight": 15},
            {"kc": 175000, "points": 2, "weight": 15},
            {"kc": 250000, "points": 3, "weight": 15},
        ]
    },
}

HIGH_TASKS = {
    # ================= BOSSES & MONSTERS =================
    "Defeat Vardorvis": {
        "slayer": 1, "quest": "Desert Treasure II", "desc": "",
        "tiers": [
            {"kc": 20, "points": 1, "weight": 5},
            {"kc": 40, "points": 2, "weight": 5},
            {"kc": 60, "points": 3, "weight": 5},
        ]
    },
    "Defeat Duke Sucellus": {
        "slayer": 1, "quest": "Desert Treasure II", "desc": "",
        "tiers": [
            {"kc": 50, "points": 1, "weight": 25},
            {"kc": 75, "points": 2, "weight": 25},
            {"kc": 125, "points": 3, "weight": 25},
        ]
    },
    "Defeat The Leviathan": {
        "slayer": 1, "quest": "Desert Treasure II", "desc": "",
        "tiers": [
            {"kc": 25, "points": 1, "weight": 10},
            {"kc": 40, "points": 2, "weight": 10},
            {"kc": 60, "points": 3, "weight": 10},
        ]
    },
    "Defeat The Whisperer": {
        "slayer": 1, "quest": "Desert Treasure II", "desc": "",
        "tiers": [
            {"kc": 20, "points": 1, "weight": 5},
            {"kc": 40, "points": 2, "weight": 5},
            {"kc": 60, "points": 3, "weight": 5},
        ]
    },
    "Defeat Nex": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 250, "points": 1, "weight": 30},
            {"kc": 350, "points": 2, "weight": 30},
            {"kc": 500, "points": 3, "weight": 30},
        ]
    },
    "Defeat Corporeal Beast": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 35, "points": 1, "weight": 10},
            {"kc": 55, "points": 2, "weight": 10},
            {"kc": 75, "points": 3, "weight": 10},
        ]
    },
    "Defeat Phosani's Nightmare": {
        "slayer": 1, "quest": "Sins of the Father", "desc": "",
        "tiers": [
            {"kc": 2, "points": 1, "weight": 5},
            {"kc": 5, "points": 2, "weight": 5},
            {"kc": 8, "points": 3, "weight": 5},
        ]
    },
    "Defeat Maggot King": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 30, "points": 1, "weight": 10},
            {"kc": 50, "points": 2, "weight": 10},
            {"kc": 75, "points": 3, "weight": 10},
        ]
    },
    "Defeat Yama": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 30, "points": 1, "weight": 10},
            {"kc": 50, "points": 2, "weight": 10},
            {"kc": 75, "points": 3, "weight": 10},
        ]
    },
    "Defeat Revenant Knights": {
        "slayer": 1, "quest": "None", "desc": "WILDERNESS!",
        "tiers": [
            {"kc": 200, "points": 1, "weight": 35},
            {"kc": 275, "points": 2, "weight": 35},
            {"kc": 350, "points": 3, "weight": 35},
        ]
    },
    "Defeat Revenant Dragons": {
        "slayer": 1, "quest": "None", "desc": "WILDERNESS!",
        "tiers": [
            {"kc": 150, "points": 1, "weight": 30},
            {"kc": 225, "points": 2, "weight": 30},
            {"kc": 275, "points": 3, "weight": 30},
        ]
    },
    "Defeat Vorkath (High)": {
        "slayer": 1, "quest": "Dragon Slayer II", "desc": "",
        "tiers": [
            {"kc": 75, "points": 1, "weight": 25},
            {"kc": 105, "points": 2, "weight": 25},
            {"kc": 135, "points": 3, "weight": 25},
        ]
    },
    "Defeat Zulrah (High)": {
        "slayer": 1, "quest": "Regicide", "desc": "",
        "tiers": [
            {"kc": 75, "points": 1, "weight": 25},
            {"kc": 105, "points": 2, "weight": 25},
            {"kc": 135, "points": 3, "weight": 25},
        ]
    },
    "Defeat Wilderness Bosses": {
        "slayer": 1, "quest": "None", "desc": "Any of the 3 main wilderness bosses",
        "tiers": [
            {"kc": 50, "points": 1, "weight": 30},
            {"kc": 85, "points": 2, "weight": 30},
            {"kc": 115, "points": 3, "weight": 30},
        ]
    },
    "Subdue Zalcano": {
        "slayer": 1, "quest": "Song of the Elves", "desc": "",
        "tiers": [
            {"kc": 50, "points": 1, "weight": 20},
            {"kc": 80, "points": 2, "weight": 20},
            {"kc": 120, "points": 3, "weight": 20},
        ]
    },

    # ================= SLAYER TASKS =================
    "Slay Hellhounds OR Cerberus": {
        "slayer": 91, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 250/50, "points": 1, "weight": 15},
            {"kc": 350/100, "points": 2, "weight": 15},
            {"kc": 450/150, "points": 3, "weight": 15},
        ]
    },
    "Slay Gargoyles OR Grotesque Guardians (High)": {
        "slayer": 75, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 150/50, "points": 1, "weight": 15},
            {"kc": 250/100, "points": 2, "weight": 15},
            {"kc": 350/150, "points": 3, "weight": 15},
        ]
    },
    "Slay Brutal Black Dragons": {
        "slayer": 77, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 100, "points": 1, "weight": 15},
            {"kc": 150, "points": 2, "weight": 15},
            {"kc": 200, "points": 3, "weight": 15},
        ]
    },
    "Slay Venators (High)": {
        "slayer": 80, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 125, "points": 1, "weight": 15},
            {"kc": 200, "points": 2, "weight": 15},
            {"kc": 275, "points": 3, "weight": 15},
        ]
    },
    "Slay Spiritual Mages": {
        "slayer": 83, "quest": "Troll Stronghold / Eadgar's Ruse", "desc": "",
        "tiers": [
            {"kc": 250, "points": 1, "weight": 15},
            {"kc": 350, "points": 2, "weight": 15},
            {"kc": 450, "points": 3, "weight": 15},
        ]
    },
    "Slay Abyssal Demons OR Sire": {
        "slayer": 85, "quest": "Priest in Peril", "desc": "",
        "tiers": [
            {"kc": 150/50, "points": 1, "weight": 20},
            {"kc": 250/100, "points": 2, "weight": 20},
            {"kc": 350/150, "points": 3, "weight": 20},
        ]
    },
    "Slay Cave Kraken": {
        "slayer": 87, "quest": "Swan Song", "desc": "KRAKEN BOSS ONLY",
        "tiers": [
            {"kc": 100, "points": 1, "weight": 15},
            {"kc": 150, "points": 2, "weight": 15},
            {"kc": 200, "points": 3, "weight": 15},
        ]
    },
    "Slay Nechryaels": {
        "slayer": 80, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 150, "points": 1, "weight": 15},
            {"kc": 225, "points": 2, "weight": 15},
            {"kc": 300, "points": 3, "weight": 15},
        ]
    },
    "Slay Ancient Wyverns": {
        "slayer": 82, "quest": "Bone Voyage", "desc": "",
        "tiers": [
            {"kc": 150, "points": 1, "weight": 15},
            {"kc": 225, "points": 2, "weight": 15},
            {"kc": 300, "points": 3, "weight": 15},
        ]
    },
    "Slay Drakes": {
        "slayer": 84, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 150, "points": 1, "weight": 15},
            {"kc": 225, "points": 2, "weight": 15},
            {"kc": 300, "points": 3, "weight": 15},
        ]
    },
    "Slay Dark Beasts": {
        "slayer": 90, "quest": "Mourning's End Part II", "desc": "",
        "tiers": [
            {"kc": 150, "points": 1, "weight": 15},
            {"kc": 225, "points": 2, "weight": 15},
            {"kc": 300, "points": 3, "weight": 15},
        ]
    },
    "Slay Araxytes OR Araxxors": {
        "slayer": 92, "quest": "A Soul's Bane", "desc": "",
        "tiers": [
            {"kc": 250/50, "points": 1, "weight": 15},
            {"kc": 350/100, "points": 2, "weight": 15},
            {"kc": 450/150, "points": 3, "weight": 15},
        ]
    },
    "Slay Smoke Devils OR Thermonuclear": {
        "slayer": 93, "quest": "Mourning's End Part I", "desc": "",
        "tiers": [
            {"kc": 150/50, "points": 1, "weight": 15},
            {"kc": 225/100, "points": 2, "weight": 15},
            {"kc": 300/150, "points": 3, "weight": 15},
        ]
    },
    "Slay Hydras OR Alchemical Hydras": {
        "slayer": 95, "quest": "Dragon Slayer II", "desc": "200 Hydras OR 50 Alchemical Hydras",
        "tiers": [
            {"kc": 200/50, "points": 1, "weight": 15},
            {"kc": 300/100, "points": 2, "weight": 15},
            {"kc": 400/150, "points": 3, "weight": 15},
        ]
    },
    "Slay Elder Custodian Stalkers": {
        "slayer": 1, "quest": "Children of the Sun", "desc": "",
        "tiers": [
            {"kc": 150, "points": 1, "weight": 15},
            {"kc": 225, "points": 2, "weight": 15},
            {"kc": 300, "points": 3, "weight": 15},
        ]
    },
    "Slay Aquanites": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 200, "points": 1, "weight": 15},
            {"kc": 300, "points": 2, "weight": 15},
            {"kc": 400, "points": 3, "weight": 15},
        ]
    },

    # ================= END GAME & RAIDS =================
    "Complete Corrupted Gauntlet": {
        "slayer": 1, "quest": "Song of the Elves", "desc": "",
        "tiers": [
            {"kc": 15, "points": 1, "weight": 5},
            {"kc": 25, "points": 2, "weight": 5},
            {"kc": 40, "points": 3, "weight": 5},
        ]
    },
    "Defeat Colosseum (Sol Heredit)": {
        "slayer": 1, "quest": "Children of the Sun", "desc": "1 time OR reach Hero Glory rank",
        "tiers": [
            {"kc": 1, "points": 1, "weight": 5},
            {"kc": 2, "points": 2, "weight": 5},
        ]
    },
    "Complete Doom of Cacaguates Delves": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 10, "points": 1, "weight": 5},
            {"kc": 25, "points": 2, "weight": 5},
            {"kc": 40, "points": 3, "weight": 5},
        ]
    },
    "Complete Chambers of Xeric (CoX)": {
        "slayer": 1, "quest": "Client of Kourend", "desc": "",
        "tiers": [
            {"kc": 10, "points": 1, "weight": 15},
            {"kc": 18, "points": 2, "weight": 15},
            {"kc": 25, "points": 3, "weight": 15},
        ]
    },
    "Complete Tombs of Amascut": {
        "slayer": 1, "quest": "Beneath Cursed Sands", "desc": "Must be normal mode (150+)",
        "tiers": [
            {"kc": 8, "points": 1, "weight": 15},
            {"kc": 15, "points": 2, "weight": 15},
            {"kc": 25, "points": 3, "weight": 15},
        ]
    },
    "Complete Theatre of Blood (ToB)": {
        "slayer": 1, "quest": "A Night at the Theatre", "desc": "Must be normal mode",
        "tiers": [
            {"kc": 1, "points": 1, "weight": 10},
            {"kc": 3, "points": 2, "weight": 10},
            {"kc": 5, "points": 3, "weight": 10},
        ]
    },

    # ================= CLUES, DIARIES & EXP =================
    "Complete Elite Clue Scrolls": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 5, "points": 1, "weight": 15},
            {"kc": 8, "points": 2, "weight": 15},
            {"kc": 12, "points": 3, "weight": 15},
        ]
    },
    "Complete Master Clue Scroll": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 2, "points": 1, "weight": 5},
            {"kc": 4, "points": 2, "weight": 5},
            {"kc": 6, "points": 3, "weight": 5},
        ]
    },
    "Complete Elite Achievement Diary": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 1, "points": 1, "weight": 5},
            {"kc": 2, "points": 3, "weight": 5},
        ]
    },
    "Gain XP in Construction": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 250000, "points": 1, "weight": 15},
            {"kc": 400000, "points": 2, "weight": 15},
            {"kc": 600000, "points": 3, "weight": 15},
        ]
    },
    "Gain XP in Herblore": {
        "slayer": 1, "quest": "Druidic Ritual", "desc": "",
        "tiers": [
            {"kc": 250000, "points": 1, "weight": 15},
            {"kc": 400000, "points": 2, "weight": 15},
            {"kc": 600000, "points": 3, "weight": 15},
        ]
    },
    "Gain XP in Farming": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 100000, "points": 1, "weight": 10},
            {"kc": 200000, "points": 2, "weight": 10},
            {"kc": 300000, "points": 3, "weight": 10},
        ]
    },
    "Gain XP in Slayer": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 100000, "points": 1, "weight": 10},
            {"kc": 200000, "points": 2, "weight": 10},
            {"kc": 300000, "points": 3, "weight": 10},
        ]
    },
    "Gain XP in Agility (High)": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 125000, "points": 1, "weight": 10},
            {"kc": 250000, "points": 2, "weight": 10},
            {"kc": 375000, "points": 3, "weight": 10},
        ]
    },
    "Gain XP in Runecraft (High)": {
        "slayer": 1, "quest": "None", "desc": "",
        "tiers": [
            {"kc": 125000, "points": 1, "weight": 10},
            {"kc": 250000, "points": 2, "weight": 10},
            {"kc": 375000, "points": 3, "weight": 10},
        ]
    },
}


# ==============================================================================
# PART 6: HELPER FUNCTIONS
# Events, store limits, pending offers, assignments, store catalog.
# ==============================================================================


def get_active_event():
    """
    Pick the event the bot should treat as "current".

    Order of preference:
      1. An event whose start/end window contains right now (is_active is ignored,
         so several future events can be queued without confusing the bot).
      2. Otherwise the most recently started event flagged is_active (keeps
         /leaderboard, /points etc. working between windows).
      3. Otherwise None.
    """
    event_res = supabase.table("events").select("*").execute()
    events = event_res.data or []
    if not events:
        return None

    now = datetime.datetime.now(datetime.timezone.utc)

    def start_key(ev):
        return str(ev.get("start_time") or "")

    in_window = [ev for ev in events if is_event_within_time_window(ev, now)]
    if in_window:
        return sorted(in_window, key=start_key, reverse=True)[0]

    flagged = [ev for ev in events if ev.get("is_active")]
    if flagged:
        # Not inside any window - log why, so a time/timezone mismatch is easy to spot.
        print(f"[events] No event window contains now={now.isoformat()}. Events seen:")
        for ev in events:
            print(f"   id={ev.get('id')} active={ev.get('is_active')} "
                  f"start={ev.get('start_time')} end={ev.get('end_time')}")
        return sorted(flagged, key=start_key, reverse=True)[0]
    return None


def is_event_within_time_window(event, now=None):
    """Return True only when an event has valid start/end times and now is within them."""
    if not event:
        return False

    def parse_event_time(value):
        if not value:
            return None
        try:
            parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            # Treat timezone-naive database values as UTC so comparisons are consistent.
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            return parsed.astimezone(datetime.timezone.utc)
        except (TypeError, ValueError):
            return None

    start_time = parse_event_time(event.get("start_time"))
    end_time = parse_event_time(event.get("end_time"))
    now = now or datetime.datetime.now(datetime.timezone.utc)

    # Fail closed if either boundary is missing or malformed.
    return start_time is not None and end_time is not None and start_time <= now <= end_time

def parse_target_kc(description: str) -> int:
    """
    Extracts target integer KC from task_description.
    Returns 1 if no KC pattern is found (e.g., single-completion tasks).
    """
    match = re.search(r'\((\d+)\s*KC\)', description, re.IGNORECASE)
    return int(match.group(1)) if match else 1


def _store_limit_ok(user_id: int, item_key: str, limit: str) -> tuple:
    """Returns (allowed, error_message)."""
    if limit == "none":
        return True, ""

    now = datetime.datetime.now(datetime.timezone.utc)
    if limit == "monthly":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        msg = "You already redeemed a **bond** this month."
    elif limit == "weekly":
        start = (now - datetime.timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        msg = "Weekly limit reached for this item."
    else:
        return True, ""

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM store_purchases
                WHERE user_id = %s AND item_key = %s AND created_at >= %s
                """,
                (user_id, item_key, start),
            )
            row = cur.fetchone()
            count = (row[0] if row else 0) or 0

    if count >= 1:
        return False, f"🔒 {msg}"
    return True, ""


def _log_store_purchase(user_id: int, item_key: str, cost: int, event_id=None):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO store_purchases (user_id, item_key, cost, event_id)
                VALUES (%s, %s, %s, %s)
                """,
                (user_id, item_key, cost, str(event_id) if event_id else None),
            )
            conn.commit()


def get_pending_task_offer(user_id: str):
    """Read the pending task offer from Supabase, which is the task-offer source of truth."""
    result = (
        supabase.table("pending_task_offers")
        .select("user_id, discord_name, task_id_1, task_id_2, task_id_3")
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def clear_pending_task_offer(user_id: str):
    """Clear a user's pending offer in Supabase and the legacy local cache."""
    uid = str(user_id)
    # Do not swallow Supabase errors here. If this delete fails, /givetask would
    # keep serving the same offer and the bug would silently return.
    supabase.table("pending_task_offers").delete().eq("user_id", uid).execute()

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM pending_task_offers WHERE user_id = %s",
                    (uid,),
                )
                conn.commit()
    except Exception as e:
        print(f"Local pending_task_offers cleanup warning for {uid}: {e}", flush=True)


def get_active_supabase_assignment(user_id: str, event_id: str):
    """Return the newest assigned/submitted assignment for this user/event."""
    result = (
        supabase.table("user_event_tasks")
        .select("*, tasks(task_description, points)")
        .eq("event_id", str(event_id))
        .eq("user_id", str(user_id))
        .in_("status", ["assigned", "submitted"])
        .order("assigned_at", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def get_store_catalog() -> dict:
    """STORE_ITEMS defaults merged with any live cost overrides from the DB."""
    catalog = {k: dict(v) for k, v in STORE_ITEMS.items()}
    try:
        init_db()
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT item_key, cost FROM store_item_costs")
                for item_key, cost in cur.fetchall() or []:
                    if item_key in catalog and cost is not None:
                        catalog[item_key]["cost"] = int(cost)
    except Exception as e:
        print(f"get_store_catalog warning: {e}", flush=True)
    return catalog


def set_store_item_cost(item_key: str, cost: int, updated_by: Optional[str] = None):
    init_db()
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO store_item_costs (item_key, cost, updated_at, updated_by)
                VALUES (%s, %s, NOW(), %s)
                ON CONFLICT (item_key) DO UPDATE
                SET cost = EXCLUDED.cost,
                    updated_at = NOW(),
                    updated_by = EXCLUDED.updated_by
                """,
                (item_key, cost, updated_by),
            )
            conn.commit()


def is_valid_proof_link(url: str) -> bool:
    if not url:
        return False
    url = url.strip()
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


# ==============================================================================
# PART 7: BOT INSTANCE & on_ready
# ==============================================================================


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}", flush=True)

    # Global slash-command sync only (avoids duplicates)
    try:
        synced = await bot.tree.sync()
        print(f"Global sync OK: {len(synced)} command(s)", flush=True)
    except Exception as e:
        print(f"Command sync failed: {e}", flush=True)

    # Refresh local users.current_task from active Supabase assignments
    try:
        active_event = get_active_event()
        if not active_event:
            print("Task Sync: No active event – skipped.", flush=True)
            return

        event_id = active_event["id"]
        assignment_res = (
            supabase.table("user_event_tasks")
            .select("id, user_id, tasks(task_description)")
            .eq("event_id", event_id)
            .eq("status", "assigned")
            .execute()
        )

        if not assignment_res.data:
            print("Task Sync: No active assignments to refresh.", flush=True)
            return

        updated_count = 0
        with get_db() as conn:
            with conn.cursor() as cursor:
                for row in assignment_res.data:
                    user_id = row.get("user_id")
                    task_info = row.get("tasks") or {}
                    new_desc = task_info.get("task_description")
                    if not user_id or not new_desc:
                        continue
                    cursor.execute(
                        """
                        UPDATE users
                        SET current_task = %s
                        WHERE user_id = %s
                          AND (current_task IS DISTINCT FROM %s)
                        """,
                        (new_desc, int(user_id), new_desc),
                    )
                    if cursor.rowcount > 0:
                        updated_count += 1
                conn.commit()

        print(f"Task Sync Complete: {updated_count} local task descriptions refreshed.", flush=True)
    except Exception as e:
        print(f"Task Sync error in on_ready: {e}", flush=True)


# ==============================================================================
# PART 8: UI - VIEWS & MODALS
# ==============================================================================


# --- Task difficulty pickers ---
class DifficultySelectView(discord.ui.View):
    def __init__(
        self,
        interaction: discord.Interaction,
        task_options: list,
        event_id,
        tier_key,
        user_id,
        user_name,
        bracket_name,
    ):
        super().__init__(timeout=120)
        self.interaction = interaction
        self.task_options = task_options
        self.event_id = event_id
        self.tier_key = tier_key
        self.user_id = user_id
        self.user_name = user_name
        self.bracket_name = bracket_name

        for task in task_options:
            points = task.get("points", 1)
            label = f"{points} Point{'s' if points > 1 else ''}"

            button = discord.ui.Button(
                label=label,
                style=(
                    discord.ButtonStyle.primary
                    if points == 1
                    else (
                        discord.ButtonStyle.secondary
                        if points == 2
                        else discord.ButtonStyle.success
                    )
                ),
                custom_id=str(task["id"]),
            )
            button.callback = self.make_callback(task)
            self.add_item(button)

    def make_callback(self, selected_task):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.interaction.user.id:
                await interaction.response.send_message(
                    "This is not your task selection!", ephemeral=True
                )
                return

            await interaction.response.defer()

            # Disable all buttons on the ephemeral message
            for item in self.children:
                item.disabled = True
            await interaction.edit_original_response(view=self)

            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            task_desc = selected_task.get("task_description")

            try:
                supabase.table("user_event_tasks").insert(
                    {
                        "event_id": self.event_id,
                        "user_id": self.user_id,
                        "user_name": self.user_name,
                        "tier": self.tier_key,
                        "task_id": selected_task["id"],
                        "status": "assigned",
                        "assigned_at": now_iso,
                    }
                ).execute()

                # The user selected a difficulty, so the pending offer is consumed.
                clear_pending_task_offer(self.user_id)
            except Exception as e:
                await interaction.followup.send(
                    f"❌ Failed to assign task: `{e}`", ephemeral=True
                )
                return

            now_timestamp = int(time.time())
            with get_db() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE users 
                        SET current_task = %s, current_bracket = %s, last_task_time = %s 
                        WHERE user_id = %s
                        """,
                        (
                            task_desc,
                            self.bracket_name,
                            now_timestamp,
                            interaction.user.id,
                        ),
                    )
                    conn.commit()

            # Public announcement embed (Everyone can see this)
            embed = discord.Embed(
                title="📜 Challenge Assigned!", color=discord.Color.gold()
            )
            embed.add_field(
                name="Player", value=interaction.user.mention, inline=True
            )
            embed.add_field(
                name="Bracket", value=self.bracket_name, inline=True
            )
            embed.add_field(
                name="Your Challenge", value=f"**{task_desc}**", inline=False
            )
            embed.set_footer(
                text="Complete it and use /taskcomplete to submit proof!"
            )

            # Public announcement must be sent via a valid messageable target.
            # Some guild channels (like CategoryChannel and ForumChannel) do not
            # support direct .send(), so fall back to the interaction follow-up.
            if isinstance(interaction.channel, discord.abc.Messageable):
                await interaction.channel.send(embed=embed)
            else:
                await interaction.followup.send(embed=embed)

        return callback

class SwitchDifficultyView(discord.ui.View):
    def __init__(
        self,
        interaction: discord.Interaction,
        task_options: list,
        assignment_id: str,
        bracket_name: str,
    ):
        super().__init__(timeout=120)
        self.interaction = interaction
        self.task_options = task_options
        self.assignment_id = assignment_id
        self.bracket_name = bracket_name

        for task in task_options:
            points = task.get("points", 1)
            label = f"{points} Point{'s' if points > 1 else ''}"

            button = discord.ui.Button(
                label=label,
                style=(
                    discord.ButtonStyle.primary
                    if points == 1
                    else (
                        discord.ButtonStyle.secondary
                        if points == 2
                        else discord.ButtonStyle.success
                    )
                ),
                custom_id=str(task["id"]),
            )
            button.callback = self.make_callback(task)
            self.add_item(button)

    def make_callback(self, selected_task):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.interaction.user.id:
                await interaction.response.send_message(
                    "This is not your menu!", ephemeral=True
                )
                return

            await interaction.response.defer()

            # Disable buttons on ephemeral message
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True
            await interaction.edit_original_response(view=self)

            task_desc = selected_task.get("task_description")

            # Update Supabase record with the new task_id
            try:
                supabase.table("user_event_tasks").update(
                    {"task_id": selected_task["id"]}
                ).eq("id", self.assignment_id).execute()
            except Exception as e:
                await interaction.followup.send(
                    f"❌ Failed to update task difficulty: `{e}`", ephemeral=True
                )
                return

            # Update local users table with new description
            with get_db() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE users SET current_task = %s WHERE user_id = %s",
                        (task_desc, interaction.user.id),
                    )
                    conn.commit()

            embed = discord.Embed(
                title="🔄 Task Difficulty Updated!", color=discord.Color.green()
            )
            embed.add_field(
                name="Player", value=interaction.user.mention, inline=True
            )
            embed.add_field(
                name="New Challenge", value=f"**{task_desc}**", inline=False
            )
            embed.set_footer(
                text="Complete it and use /taskcomplete to submit proof!"
            )

            await interaction.channel.send(embed=embed)

        return callback


# --- Modals ---
class SignupModal(discord.ui.Modal, title="Weekly Challenges Signups"):
    combat_level = discord.ui.TextInput(label="Combat Level", placeholder="e.g. 105", min_length=1, max_length=3, required=True)
    total_level = discord.ui.TextInput(label="Total Level", placeholder="e.g. 1540", min_length=1, max_length=4, required=True)
    slayer_level = discord.ui.TextInput(label="Slayer Level", placeholder="e.g. 85", min_length=1, max_length=2, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            cb = int(self.combat_level.value)
            total = int(self.total_level.value)
            slayer = int(self.slayer_level.value)
        except ValueError:
            await interaction.response.send_message("❌ Enter valid numbers!", ephemeral=True)
            return

        if cb >= 115 or total >= 1750:
            bracket_name = "High Bracket"
        elif cb >= 105 or total >= 1540:
            bracket_name = "Mid Bracket"
        else:
            bracket_name = "Low Bracket"

        guild = interaction.guild
        if guild:
            all_bracket_names = ["Low Bracket", "Mid Bracket", "High Bracket"]
            bracket_role = discord.utils.get(guild.roles, name=bracket_name)
            event_role = discord.utils.get(guild.roles, name="Weekly Challenge")

            roles_to_remove = [r for r in interaction.user.roles if r.name in all_bracket_names and r.name != bracket_name]
            roles_to_add = []
            if bracket_role and bracket_role not in interaction.user.roles:
                roles_to_add.append(bracket_role)
            if event_role and event_role not in interaction.user.roles:
                roles_to_add.append(event_role)

            try:
                if roles_to_remove:
                    await interaction.user.remove_roles(*roles_to_remove)
                if roles_to_add:
                    await interaction.user.add_roles(*roles_to_add)
            except Exception:
                pass

        get_user_data(interaction.user.id)
        try:
            with get_db() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE users 
                        SET current_bracket = %s, combat_level = %s, total_level = %s, slayer_level = %s, discord_name = %s
                        WHERE user_id = %s
                        """,
                        (bracket_name, cb, total, slayer, interaction.user.display_name, interaction.user.id),
                    )
                    conn.commit()
            await interaction.response.send_message(
                f"✅ **Registered!** Assigned: **{bracket_name}** (Slayer {slayer})",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Database error: `{e}`", ephemeral=True)

class FeedbackModal(discord.ui.Modal, title="Submit Anonymous Feedback"):
    combat_level = discord.ui.TextInput(
        label="Combat Level",
        placeholder="e.g. 126",
        required=True,
        max_length=4,
    )
    total_level = discord.ui.TextInput(
        label="Total Level",
        placeholder="e.g. 2000",
        required=True,
        max_length=5,
    )
    feedback_text = discord.ui.TextInput(
        label="Your Feedback",
        style=discord.TextStyle.paragraph,
        placeholder="Type your suggestions, thoughts, or issues here...",
        required=True,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "✅ **Thank you!** Your feedback has been anonymously submitted to the admin team.",
            ephemeral=True,
        )

        admin_channel = interaction.client.get_channel(ADMIN_FEEDBACK_CHANNEL_ID)
        if not admin_channel:
            try:
                admin_channel = await interaction.client.fetch_channel(ADMIN_FEEDBACK_CHANNEL_ID)
            except Exception as e:
                print(f"Error fetching admin feedback channel: {e}")
                return

        embed = discord.Embed(
            title="📥 New Anonymous Feedback Received",
            color=discord.Color.gold(),
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="⚔️ Combat Level", value=self.combat_level.value, inline=True)
        embed.add_field(name="📊 Total Level", value=self.total_level.value, inline=True)
        embed.add_field(name="💬 Feedback", value=self.feedback_text.value, inline=False)
        embed.set_footer(text="Emberborn Admin Feedback System")

        await admin_channel.send(embed=embed)


# ==============================================================================
# PART 9: CORE LOGIC
# ==============================================================================


async def assign_task(interaction: discord.Interaction, bracket_name: Optional[str] = None):
    await interaction.response.defer(ephemeral=True)

    user_id = str(interaction.user.id)
    user_name = str(interaction.user)

    if is_tasks_locked():
        await interaction.followup.send(
            "🔒 Challenges are currently locked by an administrator.", ephemeral=True
        )
        return

    user_data = get_user_data(interaction.user.id)
    if not user_data or not user_data.get("current_bracket"):
        await interaction.followup.send(
            "❌ You have not signed up yet! Please run `/signup` first to set your levels.",
            ephemeral=True,
        )
        return

    active_event = get_active_event()
    if not active_event or not is_event_within_time_window(active_event):
        await interaction.followup.send(
            "⚠️ Task assignments are only available between the event's start and end times.",
            ephemeral=True,
        )
        return

    event_id = str(active_event["id"])

    # ==========================================
    # 🔄 SMART AUTOMATIC WEEKLY RESET LOGIC
    # ==========================================
    last_reset = user_data.get("last_reset_event_id")

    if last_reset != event_id:
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE users 
                    SET bless_uses_this_week = 0,
                        last_reset_event_id = %s
                    WHERE user_id = %s
                    """,
                    (event_id, user_id),
                )
                conn.commit()
    # ==========================================

    bracket_name = bracket_name or user_data.get("current_bracket") or "Low Bracket"
    tier_key = bracket_name.lower().split()[0]  # "low", "mid", "high"
    slayer_lvl = user_data.get("slayer_level", 1) or 1

    # =========================================================
    # SINGLE SOURCE OF TRUTH: Supabase only
    # =========================================================
    existing_assignment = (
        supabase.table("user_event_tasks")
        .select("*, tasks(task_description)")
        .eq("event_id", event_id)
        .eq("user_id", user_id)
        .order("assigned_at", desc=True)
        .limit(1)
        .execute()
    )

    if existing_assignment.data:
        latest_record = existing_assignment.data[0]
        status = latest_record.get("status")
        task_info = latest_record.get("tasks") or {}
        active_desc = task_info.get("task_description") or "Challenge"

        if status == "assigned":
            # Keep local cache in sync
            with get_db() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE users SET current_task = %s WHERE user_id = %s",
                        (active_desc, interaction.user.id),
                    )
                    conn.commit()

            await interaction.followup.send(
                f"📌 You already have an active challenge:\n➜ **{active_desc}**\n\n"
                f"Use `/skiptask` to skip it!",
                ephemeral=True,
            )
            return

        if status == "submitted":
            await interaction.followup.send(
                "⏳ Your challenge submission is currently pending review by an admin!",
                ephemeral=True,
            )
            return
    else:
        # No active Supabase assignment → clear any stale local cache
        if user_data.get("current_task"):
            with get_db() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE users SET current_task = NULL WHERE user_id = %s",
                        (interaction.user.id,),
                    )
                    conn.commit()
    # =========================================================

    # =========================================================
    # 🔒 PENDING OFFER CHECK (PREVENT RE-ROLLING)
    # =========================================================
    pending_res = (
        supabase.table("pending_task_offers")
        .select("task_id_1, task_id_2, task_id_3")
        .eq("user_id", user_id)
        .execute()
    )

    if pending_res.data:
        # User has an active offer waiting -> fetch those saved tasks directly
        offer = pending_res.data[0]
        offered_ids = [offer["task_id_1"], offer["task_id_2"], offer["task_id_3"]]
        
        # Remove any potential None/null IDs from padding
        valid_ids = [t_id for t_id in offered_ids if t_id]

        tasks_res = (
            supabase.table("tasks")
            .select("*")
            .in_("id", valid_ids)
            .execute()
        )
        selected_options = tasks_res.data or []
        selected_options.sort(key=lambda x: x.get("points", 1))

        # Extract base name for the embed title
        first_task = selected_options[0] if selected_options else {}
        selected_base = (first_task.get("base_name") or "").strip()
        if not selected_base:
            desc = (first_task.get("task_description") or "").strip()
            selected_base = re.sub(
                r"\s*[\(\[]?\d+\s*/?\s*\d*\s*KC[\)\]]?.*$",
                "",
                desc,
                flags=re.IGNORECASE,
            ).strip()
            selected_base = re.sub(r"\s+\d+.*$", "", selected_base).strip() or desc or "Challenge"
    else:
        # Fetch eligible tasks for user's bracket and slayer level
        tasks_res = (
            supabase.table("tasks")
            .select("*")
            .eq("tier", tier_key)
            .eq("is_active", True)
            .lte("slayer_req", slayer_lvl)
            .execute()
        )

        eligible_tasks = tasks_res.data
        if not eligible_tasks:
            await interaction.followup.send(
                f"❌ No eligible challenges found for **{bracket_name}** (Slayer {slayer_lvl}).",
                ephemeral=True,
            )
            return

        # =========================================================
        # GROUP BY base_name (preferred) with description fallback
        # =========================================================
        groups = defaultdict(list)
        for task in eligible_tasks:
            base = (task.get("base_name") or "").strip()
            if not base:
                desc = (task.get("task_description") or "").strip()
                base = re.sub(
                    r"\s*[\(\[]?\d+\s*/?\s*\d*\s*KC[\)\]]?.*$",
                    "",
                    desc,
                    flags=re.IGNORECASE,
                ).strip()
                base = re.sub(r"\s+\d+.*$", "", base).strip() or desc
            groups[base].append(task)

        if not groups:
            await interaction.followup.send(
                f"❌ No eligible challenges found for **{bracket_name}** (Slayer {slayer_lvl}).",
                ephemeral=True,
            )
            return

        group_list = list(groups.items())

        # One weight per base challenge
        weights = [
            max((t.get("weight") or 1) for t in group_tasks)
            for _, group_tasks in group_list
        ]

        selected_base, selected_options = random.choices(
            group_list, weights=weights, k=1
        )[0]

        selected_options.sort(key=lambda x: x.get("points", 1))

        # Save selected task IDs so re-running /givetask yields the same options
        task_ids = [t["id"] for t in selected_options]
        # Pad array up to 3 elements if fewer options exist
        while len(task_ids) < 3:
            task_ids.append(task_ids[0])

        supabase.table("pending_task_offers").insert({
            "user_id": user_id,
            "discord_name": user_name,
            "task_id_1": task_ids[0],
            "task_id_2": task_ids[1],
            "task_id_3": task_ids[2]
        }).execute()

    # =========================================================
    # EMBED AND SELECTION VIEW DISPLAY
    # =========================================================
    embed = discord.Embed(
        title=f"📜 {active_event['name']} – Choose Difficulty",
        description=f"**Monster/Boss:** {selected_base}\n\nSelect a difficulty button below to accept your challenge:",
        color=discord.Color.gold(),
    )

    for task in selected_options:
        pts = task.get("points", 1)
        desc = task.get("task_description") or "Unknown task"
        kc = task.get("kc") or task.get("kill_count")

        field_value = f"**Requirement:** {desc}"
        if kc:
            field_value += f"\n**Target KC:** {kc}"

        embed.add_field(
            name=f"⭐ {pts} Point{'s' if pts > 1 else ''}",
            value=field_value,
            inline=False,
        )

    view = DifficultySelectView(
        interaction=interaction,
        task_options=selected_options,
        event_id=event_id,
        tier_key=tier_key,
        user_id=user_id,
        user_name=user_name,
        bracket_name=bracket_name,
    )

    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


# ==============================================================================
# PART 10: PERMISSION CHECKS
# ==============================================================================


def is_authorized():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        user_roles = [role.name for role in interaction.user.roles]
        if any(role in user_roles for role in AUTHORIZED_ROLE_NAMES):
            return True

        await interaction.response.send_message(
            "❌ You do not have permission to use this command.", ephemeral=True
        )
        return False

    return app_commands.check(predicate)


# ==============================================================================
# PART 11: PLAYER COMMANDS
# ==============================================================================


# --- Registration ---
@bot.tree.command(name="signup", description="Register for the event")
async def signup(interaction: discord.Interaction):
    await interaction.response.send_modal(SignupModal())


# --- Tasks: /givetask /currenttask /taskcomplete /skiptask /difficulty ---
@bot.tree.command(name="givetask", description="Get a new task")
async def givetask(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    user_id = str(interaction.user.id)
    user_data = get_user_data(interaction.user.id)

    if not user_data or not user_data.get("current_bracket"):
        await interaction.followup.send(
            "❌ You have not signed up yet! Please run `/signup` first.",
            ephemeral=True,
        )
        return

    active_event = get_active_event()
    if not active_event or not is_event_within_time_window(active_event):
        await interaction.followup.send(
            "⚠️ `/givetask` is only available between the event's start and end times. There is currently no event open for task assignments.",
            ephemeral=True,
        )
        return

    event_id = str(active_event["id"])

    # Single Source of Truth check in Supabase
    existing_assignment = (
        supabase.table("user_event_tasks")
        .select("*, tasks(task_description)")
        .eq("event_id", event_id)
        .eq("user_id", user_id)
        .order("assigned_at", desc=True)
        .limit(1)
        .execute()
    )

    if existing_assignment.data:
        latest_record = existing_assignment.data[0]
        status = latest_record.get("status")
        task_info = latest_record.get("tasks") or {}
        active_desc = task_info.get("task_description") or "Challenge"

        if status == "assigned":
            with get_db() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE users SET current_task = %s WHERE user_id = %s",
                        (active_desc, interaction.user.id),
                    )
                    conn.commit()

            await interaction.followup.send(
                f"📌 You already have an active challenge:\n➜ **{active_desc}**\n\n"
                f"Use `/skiptask` to skip it or `/difficulty` to change options!",
                ephemeral=True,
            )
            return

        if status == "submitted":
            await interaction.followup.send(
                "⏳ Your challenge submission is currently pending review by an admin!",
                ephemeral=True,
            )
            return

    bracket_name = user_data.get("current_bracket") or "Low Bracket"
    tier_key = bracket_name.lower().split()[0]
    slayer_lvl = user_data.get("slayer_level", 1) or 1

    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # 1. Check Supabase for the saved pending offer.
                # Supabase is the source of truth so /skiptask and /verifytask
                # can reliably consume it and allow a fresh /givetask roll.
                pending_offer = get_pending_task_offer(user_id)

                if pending_offer:
                    # Retrieve the saved task choices
                    offered_ids = [
                        pending_offer["task_id_1"],
                        pending_offer["task_id_2"],
                        pending_offer["task_id_3"],
                    ]
                    valid_ids = [t_id for t_id in offered_ids if t_id]

                    res = (
                        supabase.table("tasks")
                        .select("*")
                        .in_("id", valid_ids)
                        .execute()
                    )
                    selected_options = res.data or []
                    selected_options.sort(key=lambda x: x.get("points", 1))

                    first_task = selected_options[0] if selected_options else {}
                    selected_base = (first_task.get("base_name") or "").strip()
                    if not selected_base:
                        desc = (first_task.get("task_description") or "").strip()
                        selected_base = re.sub(
                            r"\s*[\(\[]?\d+\s*/?\s*\d*\s*KC[\)\]]?.*$",
                            "",
                            desc,
                            flags=re.IGNORECASE,
                        ).strip()

                else:
                    # 2. Pick 1 random base task category for the user's bracket
                    res = (
                        supabase.table("tasks")
                        .select("*")
                        .eq("tier", tier_key)
                        .eq("is_active", True)
                        .lte("slayer_req", slayer_lvl)
                        .execute()
                    )

                    eligible_tasks = res.data
                    if not eligible_tasks:
                        await interaction.followup.send(
                            f"❌ No eligible tasks found for **{bracket_name}**.",
                            ephemeral=True,
                        )
                        return

                    # Group tasks by base_name (e.g. "Gain XP in Woodcutting")
                    groups = defaultdict(list)
                    for task in eligible_tasks:
                        base = (task.get("base_name") or "").strip()
                        if not base:
                            desc = (task.get("task_description") or "").strip()
                            base = re.sub(
                                r"\s*[\(\[]?\d+\s*/?\s*\d*\s*KC[\)\]]?.*$",
                                "",
                                desc,
                                flags=re.IGNORECASE,
                            ).strip()
                        groups[base].append(task)

                    group_list = list(groups.items())
                    weights = [
                        max((t.get("weight") or 1) for t in group_tasks)
                        for _, group_tasks in group_list
                    ]

                    # Pick ONE random task group
                    selected_base, selected_options = random.choices(
                        group_list, weights=weights, k=1
                    )[0]

                    # Sort the 1-point, 2-point, 3-point options for this task
                    selected_options.sort(key=lambda x: x.get("points", 1))

                    task_ids = [t["id"] for t in selected_options]
                    while len(task_ids) < 3:
                        task_ids.append(task_ids[0])

                    # Store pending offers so re-running /givetask keeps the exact same options
                    cur.execute(
                        """
                        INSERT INTO pending_task_offers (user_id, task_id_1, task_id_2, task_id_3)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (user_id) DO UPDATE SET
                            task_id_1 = EXCLUDED.task_id_1,
                            task_id_2 = EXCLUDED.task_id_2,
                            task_id_3 = EXCLUDED.task_id_3;
                        """,
                        (user_id, task_ids[0], task_ids[1], task_ids[2]),
                    )
                    conn.commit()

        # Keep the Supabase pending offers table in sync with the choices shown in /givetask.
        if selected_options:
            supabase_task_ids = [task["id"] for task in selected_options]
            while len(supabase_task_ids) < 3:
                supabase_task_ids.append(supabase_task_ids[0])
            supabase.table("pending_task_offers").upsert(
                {
                    "user_id": user_id,
                    "discord_name": str(interaction.user),
                    "task_id_1": supabase_task_ids[0],
                    "task_id_2": supabase_task_ids[1],
                    "task_id_3": supabase_task_ids[2],
                },
                on_conflict="user_id",
            ).execute()

        embed = discord.Embed(
            title=f"📜 Challenge Selection — {selected_base}",
            description="Choose your desired difficulty for this task below:",
            color=discord.Color.gold(),
        )

        for task in selected_options:
            pts = task.get("points", 1)
            desc = task.get("task_description") or "Unknown task"
            embed.add_field(
                name=f"⭐ {pts} Point{'s' if pts > 1 else ''}",
                value=f"**Requirement:** {desc}",
                inline=False,
            )

        view = DifficultySelectView(
            interaction=interaction,
            task_options=selected_options,
            event_id=event_id,
            tier_key=tier_key,
            user_id=user_id,
            user_name=str(interaction.user),
            bracket_name=bracket_name,
        )

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    except Exception as e:
        print(f"Error in givetask: {e}")
        await interaction.followup.send(
            f"❌ An error occurred while fetching your task: `{e}`", ephemeral=True
        )


@givetask.error
async def givetask_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Report errors raised before the command's main try/except (DB/Supabase checks)."""
    print(f"Unhandled /givetask error: {error!r}", flush=True)
    message = "❌ I couldn't check your event or load a task. Please try again shortly."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException as response_error:
        print(f"Could not send /givetask error response: {response_error!r}", flush=True)

@bot.tree.command(name="currenttask", description="Check your current task progress")
async def currenttask(interaction: discord.Interaction):
    # Step 1: Tell Discord immediately "I'm thinking!" so it doesn't time out
    await interaction.response.defer(ephemeral=True)

    try:
        user_data = get_user_data(interaction.user.id)
        current_task = user_data.get("current_task") if user_data else None
        bracket_name = user_data.get("current_bracket") if user_data else "Unknown Bracket"

        if not current_task:
            await interaction.followup.send(
                "❌ You don't currently have an active task! Use `/givetask` to get one.",
                ephemeral=True,
            )
            return

        # Format and send the response back to the player
        embed = discord.Embed(
            title="📜 Your Active Challenge",
            color=discord.Color.gold()
        )
        embed.add_field(name="Player", value=interaction.user.mention, inline=True)
        embed.add_field(name="Bracket", value=bracket_name, inline=True)
        embed.add_field(name="Challenge", value=f"**{current_task}**", inline=False)
        embed.set_footer(text="Complete it and use /taskcomplete to submit your proof!")

        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        print(f"Error in currenttask: {e}")
        await interaction.followup.send(
            f"❌ An error occurred while checking your task: `{e}`",
            ephemeral=True
        )

@bot.tree.command(name="taskcomplete", description="Complete your active challenge with proof links")
@app_commands.describe(
    before_image="Link to your BEFORE screenshot",
    after_image="Link to your AFTER screenshot",
)
async def taskcomplete(interaction: discord.Interaction, before_image: str, after_image: str):
    await interaction.response.defer()

    try:
        user_id = interaction.user.id
        user = get_user_data(user_id) or {}
        current_task = user.get("current_task")

        if not current_task:
            await interaction.followup.send("❌ You don't have an active challenge to complete!", ephemeral=True)
            return

        for url in [before_image, after_image]:
            if not (url.startswith("http://") or url.startswith("https://")):
                await interaction.followup.send(
                    f"❌ Invalid link: `{url}`. Must start with http:// or https://",
                    ephemeral=True,
                )
                return

        assignment_id = None
        try:
            assignment_res = (
                supabase.table("user_event_tasks")
                .select("*")
                .eq("user_id", str(user_id))
                .eq("status", "assigned")
                .order("assigned_at", desc=True)
                .limit(1)
                .execute()
            )
            if assignment_res.data:
                assignment_id = assignment_res.data[0]["id"]
        except Exception as sb_err:
            print(f"Supabase fetch assignment error: {sb_err}")

        try:
            supabase.table("task_submissions").insert({
                "assignment_id": assignment_id,
                "proof_url_1": before_image,
                "proof_url_2": after_image,
                "user_id": str(user_id),
                "discord_name": str(interaction.user),  # full Discord tag
            }).execute()

            if assignment_id:
                supabase.table("user_event_tasks").update({"status": "submitted"}).eq("id", assignment_id).execute()
        except Exception as sb_insert_err:
            await interaction.followup.send(f"❌ Failed to log proof: `{sb_insert_err}`", ephemeral=True)
            return

        # Clear current task
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE users SET current_task = NULL WHERE user_id = %s",
                    (user_id,),
                )
                conn.commit()

        await interaction.followup.send(
            f"🎉 **Challenge Submitted for Verification!**\n\n"
            f"{interaction.user.mention} completed: **{current_task}**!\n\n"
            f"📷 **Proof:**\n• Before: {before_image}\n• After: {after_image}"
        )

    except Exception as e:
        print(f"Unhandled error in taskcomplete: {e}")
        await interaction.followup.send(f"❌ An error occurred: `{str(e)}`", ephemeral=True)

@bot.tree.command(name="skiptask", description="Skip your current task (costs 2 points)")
async def skiptask(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    user_id = str(interaction.user.id)
    user = get_user_data(interaction.user.id) or {}
    active_event = get_active_event()

    if not active_event or not is_event_within_time_window(active_event):
        await interaction.followup.send(
            "⚠️ There is currently no active event task to skip.", ephemeral=True
        )
        return

    event_id = str(active_event["id"])

    try:
        # Supabase is the source of truth for the active task.
        assignment = get_active_supabase_assignment(user_id, event_id)
        if not assignment or assignment.get("status") != "assigned":
            await interaction.followup.send(
                "❌ You don't have an active task to skip.", ephemeral=True
            )
            return

        current_points = user.get("points", 0) or 0
        if current_points < 2:
            await interaction.followup.send(
                f"❌ You need at least **2 points** to skip. You have **{current_points}**.",
                ephemeral=True
            )
            return

        assignment_id = assignment["id"]

        # Change only the exact active assignment.
        update_result = (
            supabase.table("user_event_tasks")
            .update({"status": "skipped"})
            .eq("id", assignment_id)
            .eq("status", "assigned")
            .execute()
        )

        if not update_result.data:
            await interaction.followup.send(
                "❌ I couldn't mark your current task as skipped. No points were deducted.",
                ephemeral=True
            )
            return

        # Deduct the normal 2-point skip cost. Roll the assignment back if the
        # local points update cannot be completed.
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE users
                    SET points = points - 2,
                        current_task = NULL
                    WHERE user_id = %s AND points >= 2
                    RETURNING points;
                    """,
                    (interaction.user.id,)
                )
                row = cursor.fetchone()
                if not row:
                    conn.rollback()
                    supabase.table("user_event_tasks").update({"status": "assigned"}).eq(
                        "id", assignment_id
                    ).execute()
                    await interaction.followup.send(
                        "❌ I couldn't deduct the 2 points, so your task was not skipped.",
                        ephemeral=True
                    )
                    return
                new_points = row[0]
                conn.commit()

        # CRITICAL FIX: /skiptask must consume the pending offer.
        # Otherwise /givetask sees the old offer and gives the same task again.
        clear_pending_task_offer(user_id)

        await interaction.followup.send(
            f"⏭️ **Task skipped!**\n"
            f"• 2 points deducted\n"
            f"• New balance: **{new_points}** points\n"
            f"• Previous task offer cleared\n"
            f"You can now claim a new task.",
            ephemeral=True
        )

    except Exception as e:
        print(f"Error in /skiptask: {e!r}", flush=True)
        await interaction.followup.send(
            f"❌ An error occurred while skipping your task: `{e}`",
            ephemeral=True
        )

@bot.tree.command(
    name="difficulty",
    description="Change the difficulty/point level of your currently assigned challenge."
)
async def difficulty(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    user_id = str(interaction.user.id)
    user_data = get_user_data(interaction.user.id)

    if not user_data or not user_data.get("current_task"):
        await interaction.followup.send(
            "❌ You do not have an active challenge! Use `/givetask` first to get one.",
            ephemeral=True,
        )
        return

    active_event = get_active_event()
    if not active_event:
        await interaction.followup.send(
            "⚠️ There is currently no active **Weekly Challenges** event!",
            ephemeral=True,
        )
        return

    event_id = active_event["id"]

    # Retrieve current assigned record from Supabase
    assignment_res = (
        supabase.table("user_event_tasks")
        .select("*, tasks(task_description, base_name)")
        .eq("event_id", event_id)
        .eq("user_id", user_id)
        .eq("status", "assigned")
        .order("assigned_at", desc=True)
        .limit(1)
        .execute()
    )

    if not assignment_res.data:
        await interaction.followup.send(
            "❌ You do not have an active unsubmitted challenge to change difficulty for.",
            ephemeral=True,
        )
        return

    current_assignment = assignment_res.data[0]
    assignment_id = current_assignment["id"]

    # Get current task details
    current_desc = user_data["current_task"]
    bracket_name = user_data.get("current_bracket", "Low Bracket")
    tier_key = bracket_name.lower().split()[0]
    slayer_lvl = user_data.get("slayer_level", 1) or 1

    # Prefer base_name from the joined task, fall back to cleaned description
    current_base = None
    task_info = current_assignment.get("tasks") or {}
    if isinstance(task_info, dict):
        current_base = (task_info.get("base_name") or "").strip()

    if not current_base:
        current_base = re.sub(
            r"\s*[\(\[]?\d+\s*/?\s*\d*\s*KC[\)\]]?.*$",
            "",
            current_desc,
            flags=re.IGNORECASE,
        ).strip()
        current_base = re.sub(r"\s+\d+.*$", "", current_base).strip() or current_desc

    # Fetch all tasks in this tier
    tasks_res = (
        supabase.table("tasks")
        .select("*")
        .eq("tier", tier_key)
        .eq("is_active", True)
        .lte("slayer_req", slayer_lvl)
        .execute()
    )

    matching_options = []
    if tasks_res.data:
        for task in tasks_res.data:
            task_base = (task.get("base_name") or "").strip()
            if not task_base:
                desc = (task.get("task_description") or "").strip()
                task_base = re.sub(
                    r"\s*[\(\[]?\d+\s*/?\s*\d*\s*KC[\)\]]?.*$",
                    "",
                    desc,
                    flags=re.IGNORECASE,
                ).strip()
                task_base = re.sub(r"\s+\d+.*$", "", task_base).strip() or desc

            if task_base.lower() == current_base.lower():
                matching_options.append(task)

    if not matching_options:
        await interaction.followup.send(
            f"❌ Could not find alternative difficulty options for **{current_base}**.",
            ephemeral=True,
        )
        return

    matching_options.sort(key=lambda x: x.get("points", 1))

    embed = discord.Embed(
        title=f"⚙️ Switch Difficulty — {current_base}",
        description=f"Your current task is: **{current_desc}**\n\nSelect a new difficulty level below:",
        color=discord.Color.blue(),
    )

    for task in matching_options:
        pts = task.get("points", 1)
        desc = task.get("task_description") or "Unknown task"
        kc = task.get("kc") or task.get("kill_count")

        field_value = f"**Requirement:** {desc}"
        if kc:
            field_value += f"\n**Target KC:** {kc}"

        embed.add_field(
            name=f"⭐ {pts} Point{'s' if pts > 1 else ''}",
            value=field_value,
            inline=False,
        )

    view = SwitchDifficultyView(
        interaction=interaction,
        task_options=matching_options,
        assignment_id=assignment_id,
        bracket_name=bracket_name,
    )

    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


# --- Points & rankings: /points /leaderboard /checkplayers /bless ---
@bot.tree.command(name="points", description="Check your or another player's total earned points.")
@app_commands.describe(player="Optional: Select a player to view their points.")
async def points(interaction: discord.Interaction, player: Optional[discord.User] = None):
    await interaction.response.defer()

    target_user = player or interaction.user
    # Ensure row exists, then read points from the same DB as /editpoints
    user = get_user_data(target_user.id) or {}
    total_points = user.get("points") or 0

    embed = discord.Embed(
        title="🏆 Point Balance",
        color=discord.Color.gold()
    )
    embed.add_field(name="Player", value=target_user.mention, inline=True)
    embed.add_field(name="Total Points", value=f"**{total_points}**", inline=True)
    embed.set_thumbnail(url=target_user.display_avatar.url)

    await interaction.followup.send(embed=embed)

@bot.tree.command(
    name="leaderboard",
    description="View current point rankings for a specific bracket."
)
@app_commands.choices(
    bracket=[
        app_commands.Choice(name="Low Bracket", value="Low Bracket"),
        app_commands.Choice(name="Mid Bracket", value="Mid Bracket"),
        app_commands.Choice(name="High Bracket", value="High Bracket"),
    ]
)
async def leaderboard(interaction: discord.Interaction, bracket: app_commands.Choice[str]):
    await interaction.response.defer()
    bracket_name = bracket.value

    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id, points 
                FROM users 
                WHERE current_bracket = %s AND points > 0 
                ORDER BY points DESC 
                LIMIT 10
                """,
                (bracket_name,),
            )
            rows = cursor.fetchall()

    if not rows:
        await interaction.followup.send(
            f"🏆 No points recorded yet for **{bracket_name}**!", ephemeral=True
        )
        return

    embed = discord.Embed(title=f"🏆 {bracket_name} Leaderboard", color=discord.Color.gold())
    medals = ["🥇", "🥈", "🥉"]
    lines = [
        f"{medals[idx - 1] if idx <= 3 else f'`#{idx}`'} <@{u_id}> — **{pts} Pts**"
        for idx, (u_id, pts) in enumerate(rows, start=1)
    ]
    embed.description = "\n".join(lines)
    embed.set_footer(text="Keep grinding tasks to climb the ranks!")

    await interaction.followup.send(embed=embed)

@bot.tree.command(name="checkplayers", description="Display full event roster details")
async def checkplayers(interaction: discord.Interaction):
    await interaction.response.defer()

    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute("SELECT * FROM users WHERE current_bracket IS NOT NULL")
            registered_users = cursor.fetchall()

    if not registered_users:
        await interaction.followup.send("⚠️ No players have signed up yet!", ephemeral=True)
        return

    for i in range(0, len(registered_users), 15):
        chunk = registered_users[i : i + 15]
        embed = discord.Embed(
            title="🏆 Event Roster & Player Overview",
            description=f"Total Signed Up Participants: **{len(registered_users)}**",
            color=discord.Color.gold(),
        )

        for user_dict in chunk:
            discord_name = user_dict.get("discord_name") or f"<@{user_dict['user_id']}>"
            bracket = user_dict.get("current_bracket") or "Unassigned"
            combat_lvl = user_dict.get("combat_level") or 0
            total_lvl = user_dict.get("total_level") or 0
            slayer_lvl = user_dict.get("slayer_level") or 1
            points = user_dict.get("points") or 0
            task = user_dict.get("current_task") or "None"

            field_value = (
                f"**Bracket:** {bracket}\n"
                f"**Combat level:** {combat_lvl}\n"
                f"**Total level:** {total_lvl}\n"
                f"**Slayer level:** {slayer_lvl}\n"
                f"**Points:** {points}\n"
                f"**Current task:** {task}"
            )
            embed.add_field(name=f"👤 {discord_name}", value=field_value, inline=False)

        await interaction.followup.send(embed=embed)

@bot.tree.command(
    name="bless",
    description="[Leader] Activate Hero's Favor: +1 bonus point on your next verified task (max 2/week)."
)
async def bless(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    user_id = interaction.user.id
    user_data = get_user_data(user_id) or {}

    user_points = user_data.get("points", 0) or 0
    already_blessed = user_data.get("active_bless_buff", False)
    weekly_uses = user_data.get("bless_uses_this_week", 0) or 0
    bracket_name = user_data.get("current_bracket")

    # 1. Must be signed up with a bracket
    if not bracket_name:
        await interaction.followup.send(
            "❌ You must be signed up with a bracket first. Use `/signup`.",
            ephemeral=True,
        )
        return

    # 2. 2x Weekly Cap Check
    if weekly_uses >= 2:
        await interaction.followup.send(
            "⚠️ You have reached your limit of **2 Hero's Favor uses** for this week!",
            ephemeral=True,
        )
        return

    # 3. Minimum 10 Points Check
    if user_points < 10:
        await interaction.followup.send(
            "🔒 You must have at least **10 total points** to use Hero's Favor.",
            ephemeral=True,
        )
        return

    # 4. Already active buff
    if already_blessed:
        await interaction.followup.send(
            "✨ Your **Hero's Favor** is already active for your next completed task!",
            ephemeral=True,
        )
        return

    # 5. Must be the current #1 in this bracket
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id, points
                FROM users
                WHERE current_bracket = %s
                  AND points > 0
                ORDER BY points DESC
                LIMIT 1
                """,
                (bracket_name,),
            )
            top = cursor.fetchone()

    if not top:
        await interaction.followup.send(
            "❌ No ranked players found in your bracket yet.",
            ephemeral=True,
        )
        return

    top_user_id, top_points = top[0], top[1]

    if int(top_user_id) != int(user_id):
        await interaction.followup.send(
            f"🔒 Only the current **#1** in **{bracket_name}** can use `/bless`.\n"
            f"Current leader has **{top_points}** points.",
            ephemeral=True,
        )
        return

    # User is #1 — activate the buff
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE users
                SET bless_uses_this_week = COALESCE(bless_uses_this_week, 0) + 1,
                    active_bless_buff = TRUE
                WHERE user_id = %s
                """,
                (user_id,),
            )
            conn.commit()

    embed = discord.Embed(
        title="✨ Hero's Favor Activated!",
        description=(
            "Your next verified task will grant an **extra +1 Bonus Point** upon staff approval!\n\n"
            f"📊 **Weekly Usage:** `{weekly_uses + 1}/2`\n"
            f"🏆 **Bracket:** {bracket_name} (#1)"
        ),
        color=discord.Color.purple(),
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


# --- Task lists: /taskslow /tasksmid /taskshigh ---
async def fetch_and_send_tier_tasks(
    interaction: discord.Interaction, tier_keys: list[str], title: str, color: discord.Color
):
    await interaction.response.defer()

    try:
        res = (
            supabase.table("tasks")
            .select("*")
            .eq("is_active", True)
            .execute()
        )

        if not res.data:
            await interaction.followup.send(
                "❌ No active tasks found in the database.", ephemeral=True
            )
            return

        task_lines = []
        for task in res.data:
            raw_tier = str(task.get("tier", "")).lower().strip()
            desc = task.get("task_description") or task.get("task_name", "Unknown Task")
            slayer_req = task.get("slayer_req", 1)
            pts = task.get("points", 0)

            if raw_tier in tier_keys:
                task_lines.append(f"• **{desc}** *(Slayer Req: {slayer_req} | Points: {pts})*")

        if not task_lines:
            await interaction.followup.send(
                f"ℹ️ No active tasks found for **{title}**.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"📜 {title}",
            description=f"Total Active Challenges: **{len(task_lines)}**",
            color=color,
        )

        current_chunk = ""
        field_count = 1

        for line in task_lines:
            if len(current_chunk) + len(line) + 1 > 1000:
                field_name = f"Challenges (Part {field_count})" if field_count > 1 else "Challenges"
                embed.add_field(name=field_name, value=current_chunk, inline=False)
                current_chunk = line + "\n"
                field_count += 1
            else:
                current_chunk += line + "\n"

        if current_chunk:
            field_name = f"Challenges (Part {field_count})" if field_count > 1 else "Challenges"
            embed.add_field(name=field_name, value=current_chunk, inline=False)

        embed.set_footer(text="Use /signup to check or update your assigned bracket tier!")
        await interaction.followup.send(embed=embed)

    except Exception as e:
        print(f"Error fetching tasks for {title}: {e}")
        await interaction.followup.send(
            f"❌ Failed to retrieve tasks list: `{str(e)}`", ephemeral=True
        )

@bot.tree.command(name="taskslow", description="View all available Low Bracket challenges.")
async def taskslow(interaction: discord.Interaction):
    await fetch_and_send_tier_tasks(
        interaction, ["low"], "Low Bracket Tasks", discord.Color.green()
    )


@bot.tree.command(name="tasksmid", description="View all available Mid Bracket challenges.")
async def tasksmid(interaction: discord.Interaction):
    await fetch_and_send_tier_tasks(
        interaction, ["med", "mid", "medium"], "Mid Bracket Tasks", discord.Color.gold()
    )


@bot.tree.command(name="taskshigh", description="View all available High Bracket challenges.")
async def taskshigh(interaction: discord.Interaction):
    await fetch_and_send_tier_tasks(
        interaction, ["high"], "High Bracket Tasks", discord.Color.red()
    )


# --- Feedback ---
@bot.tree.command(name="feedback", description="Submit anonymous feedback to the admin team")
async def feedback(interaction: discord.Interaction):
    await interaction.response.send_modal(FeedbackModal())


# ==============================================================================
# PART 12: POINT STORE COMMANDS
# /store, /buy (players) and /storeadmin (staff).
# ==============================================================================


@bot.tree.command(name="store", description="View the point store and your balance")
async def store(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    user = get_user_data(interaction.user.id) or {}
    pts = user.get("points", 0) or 0
    catalog = get_store_catalog()

    embed = discord.Embed(
        title="🛒 Point Store",
        description=f"Your balance: **{pts}** points\nSpend with `/buy`",
        color=discord.Color.gold(),
    )
    for key, item in catalog.items():
        embed.add_field(
            name=f"{item['name']} — `{key}` ({item['cost']} pts)",
            value=item["description"],
            inline=False,
        )
    embed.set_footer(text="Costs are set by staff and may change between events.")
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="buy", description="Buy an item from the point store")
@app_commands.describe(item="Item to purchase")
@app_commands.choices(
    item=[
        app_commands.Choice(name="Bond Redemption", value="bond"),
        app_commands.Choice(name="Extra Raffle Entry", value="raffle"),
        app_commands.Choice(name="Free Task Skip", value="free_skip"),
        app_commands.Choice(name="Double Points (Next Task)", value="double_buff"),
        app_commands.Choice(name="Cosmetic Role (7 days)", value="cosmetic"),
    ]
)
async def buy(interaction: discord.Interaction, item: app_commands.Choice[str]):
    await interaction.response.defer(ephemeral=True)

    item_key = item.value
    catalog = get_store_catalog().get(item_key)
    if not catalog:
        await interaction.followup.send("❌ Unknown item.", ephemeral=True)
        return

    cost = int(catalog["cost"])
    user_id = interaction.user.id
    user = get_user_data(user_id) or {}
    points = user.get("points", 0) or 0

    if points < cost:
        await interaction.followup.send(
            f"❌ Not enough points. Need **{cost}**, you have **{points}**.",
            ephemeral=True,
        )
        return

    ok, err = _store_limit_ok(user_id, item_key, catalog.get("limit", "none"))
    if not ok:
        await interaction.followup.send(err, ephemeral=True)
        return

    active_event = get_active_event()
    event_id = active_event["id"] if active_event else None

    if item_key == "free_skip":
        if not active_event or not is_event_within_time_window(active_event):
            await interaction.followup.send(
                "❌ There is no active event/task to skip right now.", ephemeral=True
            )
            return
        free_skip_assignment = get_active_supabase_assignment(
            str(user_id), str(active_event["id"])
        )
        if not free_skip_assignment or free_skip_assignment.get("status") != "assigned":
            await interaction.followup.send(
                "❌ You don't have an active task to skip.", ephemeral=True
            )
            return

    if item_key == "raffle" and not active_event:
        await interaction.followup.send(
            "❌ No active event — can't buy a raffle entry right now.",
            ephemeral=True,
        )
        return

    if item_key == "cosmetic" and not COSMETIC_ROLE_ID:
        await interaction.followup.send(
            "❌ Cosmetic role is not configured yet (staff must set COSMETIC_ROLE_ID).",
            ephemeral=True,
        )
        return

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users SET points = points - %s
                WHERE user_id = %s AND points >= %s
                RETURNING points
                """,
                (cost, user_id, cost),
            )
            row = cur.fetchone()
            if not row:
                await interaction.followup.send(
                    "❌ Could not deduct points (try again).", ephemeral=True
                )
                return
            new_balance = row[0]
            conn.commit()

    _log_store_purchase(user_id, item_key, cost, event_id)

    if item_key == "bond":
        channel = interaction.client.get_channel(STORE_STAFF_CHANNEL_ID)
        if channel is None:
            try:
                channel = await interaction.client.fetch_channel(STORE_STAFF_CHANNEL_ID)
            except Exception:
                channel = None
        if channel:
            embed = discord.Embed(
                title="🎟️ Bond Redemption Request",
                color=discord.Color.purple(),
                timestamp=datetime.datetime.now(datetime.timezone.utc),
            )
            embed.add_field(name="Player", value=interaction.user.mention, inline=True)
            embed.add_field(name="User ID", value=str(user_id), inline=True)
            embed.add_field(name="Cost", value=f"{cost} points", inline=True)
            embed.set_footer(text="Fulfill bond manually, then confirm with the player.")
            await channel.send(embed=embed)

        await interaction.followup.send(
            f"✅ **Bond redemption** purchased for **{cost}** points.\n"
            f"Staff has been notified. New balance: **{new_balance}**.",
            ephemeral=True,
        )

    elif item_key == "raffle":
        try:
            supabase.table("user_event_tasks").insert(
                {
                    "event_id": str(event_id),
                    "user_id": str(user_id),
                    "user_name": str(interaction.user),
                    "tier": (user.get("current_bracket") or "low").split()[0].lower(),
                    "status": "bonus_raffle",
                    "assigned_at": datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat(),
                }
            ).execute()
        except Exception as e:
            print(f"bonus_raffle insert warning: {e}", flush=True)

        await interaction.followup.send(
            f"✅ **Extra raffle entry** added for **{cost}** points.\n"
            f"New balance: **{new_balance}**.",
            ephemeral=True,
        )

    elif item_key == "free_skip":
        try:
            free_skip_assignment = get_active_supabase_assignment(
                str(user_id), str(event_id)
            )
            if not free_skip_assignment or free_skip_assignment.get("status") != "assigned":
                await interaction.followup.send(
                    "❌ Your active task could not be found, so the free skip was not consumed.",
                    ephemeral=True,
                )
                return

            assignment_id = free_skip_assignment["id"]
            update_result = (
                supabase.table("user_event_tasks")
                .update({"status": "skipped"})
                .eq("id", assignment_id)
                .eq("status", "assigned")
                .execute()
            )
            if not update_result.data:
                await interaction.followup.send(
                    "❌ Your active task could not be skipped, so the free skip was not completed.",
                    ephemeral=True,
                )
                return

            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE users SET current_task = NULL WHERE user_id = %s",
                        (user_id,),
                    )
                    conn.commit()

            # CRITICAL FIX: consume the pending offer so the next /givetask is fresh.
            clear_pending_task_offer(str(user_id))

        except Exception as e:
            print(f"Error applying free task skip: {e!r}", flush=True)
            await interaction.followup.send(
                f"⚠️ The free skip purchase was processed, but applying the skip encountered an error: `{e}`. Please contact staff.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"✅ **Free task skip used!** No points were charged.\n"
            f"• Weekly free skip: **1 used**\n"
            f"• New balance: **{new_balance}** points\n"
            f"• Previous task offer cleared\n"
            f"Use `/givetask` when ready.",
            ephemeral=True,
        )

    elif item_key == "double_buff":
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET store_double_buff = TRUE WHERE user_id = %s",
                    (user_id,),
                )
                conn.commit()

        await interaction.followup.send(
            f"✅ **Double points** active on your next verified task (**{cost}** pts).\n"
            f"New balance: **{new_balance}**.",
            ephemeral=True,
        )

    elif item_key == "cosmetic":
        until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            days=7
        )
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET cosmetic_role_until = %s WHERE user_id = %s",
                    (until, user_id),
                )
                conn.commit()

        role = (
            interaction.guild.get_role(COSMETIC_ROLE_ID) if interaction.guild else None
        )
        if role:
            try:
                await interaction.user.add_roles(
                    role, reason="Store cosmetic purchase"
                )
            except Exception as e:
                await interaction.followup.send(
                    f"✅ Purchased, but could not assign role: `{e}`.\n"
                    f"Staff can add it manually. Balance: **{new_balance}**.",
                    ephemeral=True,
                )
                return

        await interaction.followup.send(
            f"✅ **Cosmetic role** granted for 7 days (**{cost}** pts).\n"
            f"New balance: **{new_balance}**.",
            ephemeral=True,
        )

@bot.tree.command(
    name="storeadmin",
    description="[Staff] View or change point-store item costs (saved to DB)",
)
@app_commands.describe(
    item="Leave empty to list current costs; pick an item to change its price",
    cost="New point cost (required when changing an item; minimum 1)",
)
@app_commands.choices(
    item=[
        app_commands.Choice(name="Bond Redemption", value="bond"),
        app_commands.Choice(name="Extra Raffle Entry", value="raffle"),
        app_commands.Choice(name="Free Task Skip", value="free_skip"),
        app_commands.Choice(name="Double Points (Next Task)", value="double_buff"),
        app_commands.Choice(name="Cosmetic Role (7 days)", value="cosmetic"),
    ]
)
@app_commands.checks.has_any_role("Owner", "Admin", "Aegis Warrior")
async def storeadmin(
    interaction: discord.Interaction,
    item: app_commands.Choice[str] = None,
    cost: Optional[int] = None,
):
    await interaction.response.defer(ephemeral=True)
    catalog = get_store_catalog()

    # List mode
    if item is None:
        lines = [
            f"• **{v['name']}** (`{k}`) — **{v['cost']}** pts"
            for k, v in catalog.items()
        ]
        embed = discord.Embed(
            title="🛠️ Store Admin — Current Costs",
            description="\n".join(lines)
            + "\n\nChange a price with:\n`/storeadmin item:<name> cost:<number>`",
            color=discord.Color.orange(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    item_key = item.value
    if item_key not in catalog:
        await interaction.followup.send("❌ Unknown item.", ephemeral=True)
        return

    if cost is None:
        await interaction.followup.send(
            f"**{catalog[item_key]['name']}** currently costs "
            f"**{catalog[item_key]['cost']}** points.\n"
            f"To change it: `/storeadmin item:{item_key} cost:<number>`",
            ephemeral=True,
        )
        return

    if cost < 1:
        await interaction.followup.send("❌ Cost must be at least **1**.", ephemeral=True)
        return

    old_cost = catalog[item_key]["cost"]
    set_store_item_cost(item_key, cost, updated_by=str(interaction.user))
    new_catalog = get_store_catalog()

    await interaction.followup.send(
        f"✅ **{new_catalog[item_key]['name']}** cost updated:\n"
        f"**{old_cost}** → **{new_catalog[item_key]['cost']}** points\n"
        f"(Saved to database — survives restarts.)",
        ephemeral=True,
    )


@storeadmin.error
async def storeadmin_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    msg = (
        "🚫 Only Staff can use `/storeadmin`."
        if isinstance(error, app_commands.MissingAnyRole)
        else f"❌ Error: {str(error)}"
    )
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


# ==============================================================================
# PART 13: STAFF COMMANDS
# ==============================================================================


# --- Task review ---
@bot.tree.command(
    name="verifytask",
    description="[Staff Only] Review and approve or reject a player's task proof.",
)
@app_commands.describe(
    player="The player whose submission you are reviewing.",
    action="Approve or Reject the submission.",
    reason="Optional reason (especially useful if rejecting).",
)
@app_commands.choices(
    action=[
        app_commands.Choice(name="Approve", value="approve"),
        app_commands.Choice(name="Reject (Needs Fix)", value="reject"),
    ]
)
@app_commands.checks.has_any_role("Admin", "Aegis Warrior", "Owner")
async def verifytask(
    interaction: discord.Interaction,
    player: discord.User,
    action: app_commands.Choice[str],
    reason: str = "No reason provided.",
):
    await interaction.response.defer()

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    user_id = str(player.id)
    active_event = get_active_event()

    if not active_event:
        await interaction.followup.send("❌ No active event found.", ephemeral=True)
        return

    event_id = active_event["id"]

    assignment_res = (
        supabase.table("user_event_tasks")
        .select("*, tasks(task_description, points)")
        .eq("event_id", event_id)
        .eq("user_id", user_id)
        .eq("status", "submitted")
        .order("assigned_at", desc=True)
        .limit(1)
        .execute()
    )

    if not assignment_res.data:
        await interaction.followup.send(
            f"⚠️ No submitted task found for {player.mention}.",
            ephemeral=True,
        )
        return

    assignment = assignment_res.data[0]
    assignment_id = assignment["id"]
    task_info = assignment.get("tasks") or {}
    task_desc = task_info.get("task_description", "Unknown Task") if isinstance(task_info, dict) else "Unknown Task"
    task_points = (task_info.get("points") or 1) if isinstance(task_info, dict) else 1

    if action.value == "approve":
        already_has_entry = (
            supabase.table("user_event_tasks")
            .select("id")
            .eq("event_id", event_id)
            .eq("user_id", user_id)
            .in_("status", ["verified", "verified_no_raffle"])
            .execute()
        )

        new_status = "verified_no_raffle" if already_has_entry.data else "verified"
        raffle_text = (
            "No additional raffle entry (already has one this event)"
            if already_has_entry.data
            else "Entered into the raffle (1 free entry)"
        )
        color = discord.Color.blue() if already_has_entry.data else discord.Color.green()

        supabase.table("user_event_tasks").update({"status": new_status}).eq(
            "id", assignment_id
        ).execute()
        supabase.table("task_submissions").update(
            {
                "verified_by": str(interaction.user),
                "verified_at": now_iso,
            }
        ).eq("assignment_id", assignment_id).execute()

        user_data = get_user_data(int(user_id))
        bracket_name = (
            user_data.get("current_bracket", "Low Bracket") if user_data else "Low Bracket"
        )

        has_bless = user_data.get("active_bless_buff", False) if user_data else False
        has_double = user_data.get("store_double_buff", False) if user_data else False
        base_pts = task_points or 1
        if has_double:
            base_pts = base_pts * 2
        bonus_pt = 1 if has_bless else 0
        total_points_to_add = base_pts + bonus_pt

        takeover_type = None
        safe_new_user_pts = 0
        former_top_id = None

        with get_db() as conn:
            with conn.cursor() as cursor:
                # Current #1 in this bracket
                cursor.execute(
                    """
                    SELECT user_id, COALESCE(points, 0)
                    FROM users
                    WHERE current_bracket = %s
                    ORDER BY points DESC
                    LIMIT 1
                    """,
                    (bracket_name,),
                )
                top_player = cursor.fetchone()

                former_top_id = str(top_player[0]) if top_player else None
                former_top_pts = (top_player[1] or 0) if top_player else 0

                # Award points + clear current task / buffs (ONE statement only)
                cursor.execute(
                    """
                    UPDATE users
                    SET points = COALESCE(points, 0) + %s,
                        current_task = NULL,
                        active_bless_buff = FALSE,
                        store_double_buff = FALSE
                    WHERE user_id = %s
                    RETURNING COALESCE(points, 0)
                    """,
                    (total_points_to_add, user_id),
                )
                res_row = cursor.fetchone()
                new_user_pts = res_row[0] if res_row else 0

                # Clear pending offers locally if the table exists
                try:
                    cursor.execute(
                        "DELETE FROM pending_task_offers WHERE user_id = %s",
                        (user_id,),
                    )
                except Exception:
                    pass

                safe_former_top_pts = former_top_pts or 0
                safe_new_user_pts = new_user_pts or 0

                POINT_THRESHOLD = 10
                if (
                    former_top_id
                    and former_top_id != user_id
                    and safe_former_top_pts > 0
                    and safe_new_user_pts >= POINT_THRESHOLD
                ):
                    if safe_new_user_pts > safe_former_top_pts:
                        takeover_type = "passed"
                    elif safe_new_user_pts == safe_former_top_pts:
                        takeover_type = "tied"

                if takeover_type:
                    cursor.execute(
                        """
                        UPDATE users
                        SET leader_perks_available = COALESCE(leader_perks_available, 0) + 1
                        WHERE user_id = %s
                        """,
                        (user_id,),
                    )

                conn.commit()

        # Verification consumes the pending offer so the next /givetask rolls a new task.
        clear_pending_task_offer(str(user_id))

        embed = discord.Embed(title="✅ Task Approved", color=color)
        embed.add_field(name="Player", value=player.mention, inline=True)
        embed.add_field(name="Verified By", value=interaction.user.mention, inline=True)
        embed.add_field(name="Task", value=f"**{task_desc}**", inline=False)
        pts_str = f"**+{total_points_to_add} Point{'s' if total_points_to_add > 1 else ''}**"
        extras = []
        if has_double:
            extras.append("2× store buff")
        if bonus_pt:
            extras.append("+1 Hero's Favor")
        if extras:
            pts_str += f" *({', '.join(extras)})*"
        embed.add_field(name="Points Awarded", value=pts_str, inline=True)
        embed.add_field(name="Raffle Status", value=raffle_text, inline=False)

        await interaction.followup.send(embed=embed)

        if takeover_type:
            action_str = (
                "has **PASSED**" if takeover_type == "passed" else "has **TIED WITH**"
            )
            former_mention = f"<@{former_top_id}>"

            announcement_embed = discord.Embed(
                title="🔥 LEADERBOARD TAKEOVER! 🔥",
                description=(
                    f"📢 **@Weekly Challenge**\n\n"
                    f"⚔️ {player.mention} {action_str} {former_mention} for **#1 Rank** "
                    f"in **{bracket_name}** with **{safe_new_user_pts} Points**!\n\n"
                    f"✨ {player.mention} has unlocked the **`/bless`** ability! "
                    f"Run `/bless` to empower your next completed task for **+1 Bonus Point**!"
                ),
                color=discord.Color.gold(),
            )
            await interaction.channel.send(
                content="@Weekly Challenge", embed=announcement_embed
            )

    elif action.value == "reject":
        supabase.table("user_event_tasks").update({"status": "rejected"}).eq(
            "id", assignment_id
        ).execute()
        supabase.table("task_submissions").update(
            {
                "verified_by": str(interaction.user),
                "verified_at": now_iso,
                "rejection_reason": reason,
            }
        ).eq("assignment_id", assignment_id).execute()

        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE users SET current_task = NULL WHERE user_id = %s",
                    (user_id,),
                )
                try:
                    cursor.execute(
                        "DELETE FROM pending_task_offers WHERE user_id = %s",
                        (user_id,),
                    )
                except Exception:
                    pass
                conn.commit()

        # Rejected submissions also consume the old pending offer.
        clear_pending_task_offer(str(user_id))

        embed = discord.Embed(title="❌ Task Proof Rejected", color=discord.Color.red())
        embed.add_field(name="Player", value=player.mention, inline=True)
        embed.add_field(name="Reviewed By", value=interaction.user.mention, inline=True)
        embed.add_field(name="Task", value=f"**{task_desc}**", inline=False)
        embed.add_field(name="Reason", value=f"*{reason}*", inline=False)

        await interaction.followup.send(embed=embed)


# --- Points & raffle ---
@bot.tree.command(
    name="editpoints",
    description="[Staff] Add or remove points from a player (use negative to remove)",
)
@app_commands.describe(
    member="The player to update",
    amount="Points to add (positive) or remove (negative), e.g. 10 or -5",
)
@app_commands.checks.has_any_role("Owner", "Admin", "Aegis Warrior")
async def editpoints(interaction: discord.Interaction, member: discord.Member, amount: int):
    await interaction.response.defer(ephemeral=True)

    if amount == 0:
        await interaction.followup.send("❌ Amount cannot be **0**.", ephemeral=True)
        return

    # Ensure the user row exists in the same DB everything else uses
    get_user_data(member.id)

    with get_db() as conn:
        with conn.cursor() as cur:
            # COALESCE fixes NULL + amount → NULL (old /addpoints bug)
            cur.execute(
                """
                UPDATE users
                SET points = GREATEST(0, COALESCE(points, 0) + %s)
                WHERE user_id = %s
                RETURNING COALESCE(points, 0)
                """,
                (amount, member.id),
            )
            row = cur.fetchone()
            conn.commit()

    if not row:
        await interaction.followup.send(
            f"❌ Could not update points for {member.mention}.",
            ephemeral=True,
        )
        return

    new_total = row[0]
    if amount > 0:
        action = f"Added **+{amount}**"
    else:
        action = f"Removed **{amount}**"

    await interaction.followup.send(
        f"✅ {action} points for {member.mention}.\n"
        f"New balance: **{new_total}** points.",
        ephemeral=True,
    )


@editpoints.error
async def editpoints_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    msg = (
        "🚫 Only Staff can use `/editpoints`."
        if isinstance(error, app_commands.MissingAnyRole)
        else f"❌ Error: {str(error)}"
    )
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(
    name="drawraffle",
    description="[Staff] Draw 1 random winner from all raffle entries for the active Weekly Challenge event.",
)
@app_commands.checks.has_any_role("Owner", "Admin", "Aegis Warrior")
async def drawraffle(interaction: discord.Interaction):
    await interaction.response.defer()

    active_event = get_active_event()
    if not active_event:
        await interaction.followup.send(
            "❌ No active Weekly Challenge event found. Cannot draw a raffle winner.",
            ephemeral=True,
        )
        return

    event_id = str(active_event["id"])
    event_name = active_event.get("name") or "Weekly Challenge"

    try:
        # Collect every raffle-eligible row:
        #   - "verified"        = free entry from first verified task
        #   - "bonus_raffle"    = extra entries bought from the store
        entries_res = (
            supabase.table("user_event_tasks")
            .select("user_id, user_name, status")
            .eq("event_id", event_id)
            .in_("status", ["verified", "bonus_raffle"])
            .execute()
        )
    except Exception as e:
        await interaction.followup.send(
            f"❌ Failed to fetch raffle entries: `{e}`",
            ephemeral=True,
        )
        return

    entries = entries_res.data or []
    if not entries:
        await interaction.followup.send(
            f"⚠️ No raffle entries found for **{event_name}**.\n"
            "Entries come from first verified tasks and store purchases.",
            ephemeral=True,
        )
        return

    # Build the ticket pool (one ticket per row so bonus buys increase odds)
    ticket_pool = []
    for row in entries:
        uid = row.get("user_id")
        if not uid:
            continue
        ticket_pool.append(
            {
                "user_id": str(uid),
                "user_name": row.get("user_name") or f"<@{uid}>",
                "status": row.get("status"),
            }
        )

    if not ticket_pool:
        await interaction.followup.send(
            "⚠️ Raffle entries existed but none had a valid user_id.",
            ephemeral=True,
        )
        return

    winner = random.choice(ticket_pool)
    winner_id = winner["user_id"]
    total_tickets = len(ticket_pool)

    # Count tickets per user for the announcement
    counts = Counter(t["user_id"] for t in ticket_pool)
    winner_tickets = counts[winner_id]

    # Clean up weekly challenge tables via RPC call
    try:
        supabase.rpc("reset_weekly_challenge_data", {}).execute()
    except Exception as rpc_err:
        print(f"Failed to reset Supabase tables: {rpc_err}")

    # Set chacaraman's Discord ID here
    chacaraman_id = "CHACARAMAN_USER_ID"

    embed = discord.Embed(
        title="🎟️ Raffle Winner Drawn!",
        description=(
            f"**Event:** {event_name}\n\n"
            f"🎉 Congratulations to <@{winner_id}>!\n"
            f"They won the Weekly Challenge raffle with "
            f"**{winner_tickets}** ticket{'s' if winner_tickets != 1 else ''} "
            f"out of **{total_tickets}** total.\n\n"
            f"Get with <@{887708604621266954}> for your winnings!"
        ),
        color=discord.Color.gold(),
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    embed.add_field(
        name="Drawn By",
        value=interaction.user.mention,
        inline=True,
    )
    embed.add_field(
        name="Total Entries",
        value=str(total_tickets),
        inline=True,
    )
    embed.set_footer(text="Entries = first verified task + any store bonus raffle buys")

    await interaction.followup.send(content="@Weekly Challenge", embed=embed)


@drawraffle.error
async def drawraffle_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    msg = (
        "🚫 Only Staff (Owner / Admin / Aegis Warrior) can use `/drawraffle`."
        if isinstance(error, app_commands.MissingAnyRole)
        else f"❌ Error: {str(error)}"
    )
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


# --- Bot admin: /togglelock /synccommands /update /updated ---
@bot.tree.command(name="togglelock", description="Toggle whether task claims are locked")
async def togglelock(interaction: discord.Interaction):
    current = is_tasks_locked()
    set_tasks_locked(not current)
    status = "🔒 LOCKED" if not current else "🔓 UNLOCKED"
    await interaction.response.send_message(f"Task claiming is now {status}")

@bot.tree.command(name="synccommands", description="[Staff] Remove duplicate commands and resync")
@app_commands.checks.has_any_role("Owner", "Admin", "Aegis Warrior")
async def synccommands(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        if interaction.guild:
            # Wipe guild-specific commands only (this removes the doubles)
            bot.tree.clear_commands(guild=interaction.guild)
            await bot.tree.sync(guild=interaction.guild)

        # Keep only global commands (one of each)
        synced = await bot.tree.sync()

        await interaction.followup.send(
            f"✅ Duplicates cleared. **{len(synced)}** global commands synced.\n"
            f"Wait 30–60 seconds, then restart Discord (Ctrl+R) if any doubles remain.",
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Sync failed: `{e}`", ephemeral=True)

@bot.tree.command(name="update", description="Announce that the Task Manager bot is undergoing maintenance.")
@app_commands.checks.has_role("Owner")
async def update(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛠️ Task Manager Bot Maintenance",
        description="We are currently updating the **Task Manager** bot! Please be patient while we deploy new features and fixes. We will be back shortly.",
        color=discord.Color.orange()
    )
    embed.set_footer(text="Task Manager Updates • Thank you for your patience!")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="updated", description="Announce that the Task Manager bot is back online.")
@app_commands.checks.has_role("Owner")
async def updated(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🚀 Task Manager Bot is Back Live!",
        description="The maintenance/update is officially complete! **Task Manager** is back online and fully operational. Thank you for waiting!",
        color=discord.Color.green()
    )
    embed.set_footer(text="Task Manager Updates • All systems operational")
    await interaction.response.send_message(embed=embed)


@update.error
@updated.error
async def owner_role_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingRole):
        await interaction.response.send_message(
            "❌ You do not have permission to use this command. Requires the **Owner** role.", 
            ephemeral=True
        )
    else:
        await interaction.response.send_message(f"❌ An error occurred: `{str(error)}`", ephemeral=True)


# ==============================================================================
# PART 14: APPLICATION LAUNCHER
# ==============================================================================


if __name__ == "__main__":
    if ENABLE_KEEP_ALIVE:
        keep_alive()
    bot.run(DISCORD_TOKEN)
