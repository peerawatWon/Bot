import asyncio
import os
import time
from collections import deque

import aiohttp
import psycopg2
import twitchio
from aiohttp import web
from dotenv import load_dotenv
from twitchio.ext import commands

load_dotenv()

TOKEN = os.getenv("TOKEN")
MOD_TOKEN = os.getenv("MOD_TOKEN")
CHANNEL = os.getenv("CHANNEL")
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", 5000))

DB_KEY = "haw_count"
TRIGGER = "ห์"
SHOUTOUT_DEDUP_SEC = 300

seen_ids: deque[str] = deque(maxlen=500)
recent_shoutouts: dict[str, float] = {}


# --- storage ---------------------------------------------------------------

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def setup_db():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_counts (
                key VARCHAR(100) PRIMARY KEY,
                value INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
    print("[db] tables ready")


def load_count() -> int:
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT value FROM bot_counts WHERE key = %s", (DB_KEY,))
            row = cur.fetchone()
            if row:
                print(f"[db] loaded count={row[0]}")
                return row[0]

            cur.execute(
                "INSERT INTO bot_counts (key, value) VALUES (%s, 0) ON CONFLICT DO NOTHING",
                (DB_KEY,),
            )
            conn.commit()
            print("[db] no row, starting at 0")
            return 0
    except Exception as e:
        print(f"[db] load error: {type(e).__name__}: {e}")
        return 0


def save_count(n: int) -> None:
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE bot_counts SET value = %s, updated_at = NOW() WHERE key = %s",
                (n, DB_KEY),
            )
            conn.commit()
    except Exception as e:
        print(f"[db] save error: {type(e).__name__}: {e}")


# --- health check ----------------------------------------------------------

async def start_web_server() -> None:
    app = web.Application()
    app.router.add_get("/", lambda _r: web.Response(text="OK"))
    app.router.add_get("/health", lambda _r: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print(f"[web] health check on port {PORT}")


# --- counter bot -----------------------------------------------------------

class CounterBot(commands.Bot):
    def __init__(self, count: int):
        super().__init__(token=TOKEN, prefix="!", initial_channels=[CHANNEL])
        self.count = count

    async def event_ready(self):
        print(f"[counter] ready in #{CHANNEL} | count={self.count}")

    def _should_skip(self, message: twitchio.Message) -> bool:
        if message.echo or not message.author:
            return True
        return message.author.name.lower() in (self.nick.lower(), "nightbot")

    async def event_message(self, message: twitchio.Message):
        if self._should_skip(message) or TRIGGER not in message.content:
            return

        # Twitch can redeliver a message after a reconnect; dedupe by message id.
        msg_id = getattr(message, "id", None)
        if msg_id:
            if msg_id in seen_ids:
                return
            seen_ids.append(msg_id)

        self.count += 1
        save_count(self.count)
        print(f"[counter] {self.count} <- {message.author.name}: {message.content}")

        try:
            await message.channel.send(
                f"𓈒 ก็คือจิบน้ำหน่อยมั้ย หลงกันไปแล้ว {self.count} ครั้งแล้ว 🥤𓈒"
            )
        except Exception as e:
            print(f"[counter] send error: {type(e).__name__}: {e}")


# --- shoutout bot ----------------------------------------------------------

class ModBot(commands.Bot):
    def __init__(self):
        super().__init__(token=MOD_TOKEN, prefix="!", initial_channels=[CHANNEL])
        self._raw_token = MOD_TOKEN.replace("oauth:", "")
        self.client_id: str | None = None
        self.moderator_id: str | None = None
        self.broadcaster_id: str | None = None

    @property
    def _helix_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._raw_token}", "Client-Id": self.client_id}

    @property
    def _ids_ready(self) -> bool:
        return bool(self.client_id and self.moderator_id and self.broadcaster_id)

    async def event_ready(self):
        print("[mod] ready")
        await self._resolve_ids()

    async def _resolve_ids(self) -> None:
        """Read client_id/user_id off the token, then look up the channel's id."""
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(
                    "https://id.twitch.tv/oauth2/validate",
                    headers={"Authorization": f"OAuth {self._raw_token}"},
                ) as r:
                    if r.status != 200:
                        print(f"[mod] token validate failed: {r.status}")
                        return
                    data = await r.json()

                self.client_id = data.get("client_id")
                self.moderator_id = data.get("user_id")
                if "moderator:manage:shoutouts" not in (data.get("scopes") or []):
                    print("[mod] token missing moderator:manage:shoutouts — "
                          "official shoutout will fail, !so still works")

                self.broadcaster_id = await self._user_id(sess, CHANNEL)

            print(f"[mod] ids resolved | broadcaster={self.broadcaster_id} "
                  f"moderator={self.moderator_id}")
        except Exception as e:
            print(f"[mod] resolve error: {type(e).__name__}: {e}")

    async def _user_id(self, sess: aiohttp.ClientSession, login: str) -> str | None:
        async with sess.get(
            "https://api.twitch.tv/helix/users",
            params={"login": login.lower()},
            headers=self._helix_headers,
        ) as r:
            users = (await r.json()).get("data") or []
        return users[0]["id"] if users else None

    async def _helix_shoutout(self, login: str) -> None:
        """The real /shoutout, which IRC no longer accepts as a chat command."""
        if not self._ids_ready:
            await self._resolve_ids()
        if not self._ids_ready:
            print("[shoutout] ids unavailable, skipping helix call")
            return

        try:
            async with aiohttp.ClientSession() as sess:
                to_id = await self._user_id(sess, login)
                if not to_id:
                    print(f"[shoutout] unknown user: {login}")
                    return

                async with sess.post(
                    "https://api.twitch.tv/helix/chat/shoutouts",
                    params={
                        "from_broadcaster_id": self.broadcaster_id,
                        "to_broadcaster_id": to_id,
                        "moderator_id": self.moderator_id,
                    },
                    headers=self._helix_headers,
                ) as r:
                    if r.status == 204:
                        print(f"[shoutout] helix ok: {login}")
                    else:
                        # 400 = channel offline, 429 = still on cooldown
                        print(f"[shoutout] helix {r.status}: {await r.text()}")
        except Exception as e:
            print(f"[shoutout] error: {type(e).__name__}: {e}")

    async def do_shoutout(self, channel, login: str, send_chat: bool = True) -> None:
        login = login.lstrip("@")
        now = time.time()
        last = recent_shoutouts.get(login.lower())
        if last and now - last < SHOUTOUT_DEDUP_SEC:
            print(f"[shoutout] skip {login}, done {int(now - last)}s ago")
            return
        recent_shoutouts[login.lower()] = now

        if send_chat:
            try:
                await channel.send(f"!so {login}")
            except Exception as e:
                print(f"[shoutout] !so failed: {type(e).__name__}: {e}")

        # Give the chat bot time to post its blurb before the native shoutout.
        await asyncio.sleep(2)
        await self._helix_shoutout(login)

    async def event_raw_usernotice(self, channel, tags: dict):
        if tags.get("msg-id") != "raid":
            return
        login = tags.get("msg-param-login") or tags.get("login")
        if not login:
            print(f"[raid] usernotice without login: {tags}")
            return
        print(f"[raid] {login} ({tags.get('msg-param-viewerCount', '?')} viewers)")
        await self.do_shoutout(channel, login)

    @commands.command(name="so")
    async def cmd_so(self, ctx: commands.Context, target: str = None):
        if not target or not (ctx.author.is_mod or ctx.author.is_broadcaster):
            return
        # The mod's own message already triggered the chat !so, so don't repeat it.
        await self.do_shoutout(ctx.channel, target, send_chat=False)


async def main() -> None:
    setup_db()
    await start_web_server()
    await asyncio.gather(CounterBot(load_count()).start(), ModBot().start())


if __name__ == "__main__":
    asyncio.run(main())
