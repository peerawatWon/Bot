import os
import asyncio
from collections import deque
from dotenv import load_dotenv
import twitchio
from twitchio.ext import commands
from aiohttp import web
import psycopg2

load_dotenv()

TOKEN = os.getenv("TOKEN")
MOD_TOKEN = os.getenv("MOD_TOKEN")
CHANNEL = os.getenv("CHANNEL")
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", 5000))

DB_KEY = "haw_count"

seen_ids: deque = deque(maxlen=500)


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def setup_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bot_counts (
                    key VARCHAR(100) PRIMARY KEY,
                    value INTEGER NOT NULL DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)
            conn.commit()
    print("[db] tables ready")


def load_count():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM bot_counts WHERE key = %s", (DB_KEY,))
                row = cur.fetchone()
                if row:
                    print(f"[count] โหลดจาก PostgreSQL: {row[0]}")
                    return row[0]
                else:
                    cur.execute(
                        "INSERT INTO bot_counts (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
                        (DB_KEY, 0)
                    )
                    conn.commit()
                    print("[count] ไม่เจอใน DB เริ่มที่ 0")
                    return 0
    except Exception as e:
        print(f"[count] DB error: {e}")
        return 0


def save_count(n):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE bot_counts SET value = %s, updated_at = NOW() WHERE key = %s",
                    (n, DB_KEY)
                )
                conn.commit()
    except Exception as e:
        print(f"[save] DB error: {e}")


setup_db()
count = load_count()
mod_bot: "ModBot | None" = None


async def handle_ping(_request):
    return web.Response(text="OK")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Health check server running on port {PORT}")



class Bot(commands.Bot):
    def __init__(self):
        super().__init__(token=TOKEN, prefix="!", initial_channels=[CHANNEL])

    async def event_ready(self):
        print(f"Bot พร้อมแล้ว | กำลังดู chat ใน #{CHANNEL} | count เริ่มต้นที่ {count}")

    async def event_message(self, message: twitchio.Message):
        global count
        if message.echo:
            return
        if message.author and message.author.name.lower() == self.nick.lower():
            return
        if "ห์" in message.content:
            msg_id = getattr(message, "id", None)
            if msg_id:
                if msg_id in seen_ids:
                    print(f"[dedup] ข้ามข้อความซ้ำ id={msg_id}")
                    return
                seen_ids.append(msg_id)
            count += 1
            save_count(count)
            print(f'[{count}] {message.author.name}: {message.content}')
            try:
                await message.channel.send(f"𓈒 ก็คือจิบน้ำหน่อยมั้ย หลงกันไปแล้ว {count} ครั้งแล้ว 🥤𓈒")
                print(f"[send] ส่งแล้ว count={count}")
            except Exception as e:
                print(f"[send error] {type(e).__name__}: {e}")


class ModBot(commands.Bot):
    def __init__(self):
        super().__init__(token=MOD_TOKEN, prefix="!", initial_channels=[CHANNEL])

    async def event_ready(self):
        print(f"ModBot พร้อมแล้ว | ใช้สำหรับ shoutout")

    async def event_raid(self, channel, raider, viewer_count):
        print(f"[raid] {raider.name} มา raid ด้วย {viewer_count} คน")
        try:
            await channel.send(f"!so {raider.name}")
            print(f"!so {raider.name}")
            await asyncio.sleep(1)
            await channel.send(f"/shoutout {raider.name}")
            print(f"/shoutout {raider.name}")
        except Exception as e:
            print(f"[raid error] {type(e).__name__}: {e}")


async def main():
    global mod_bot
    await start_web_server()
    bot = Bot()
    mod_bot = ModBot()
    await asyncio.gather(bot.start(), mod_bot.start())


asyncio.run(main())
