import os
from dotenv import load_dotenv
import twitchio
from twitchio.ext import commands

load_dotenv()

TOKEN = os.getenv("TOKEN")
CHANNEL = os.getenv("CHANNEL")

count = 0


class Bot(commands.Bot):
    def __init__(self):
        super().__init__(token=TOKEN, prefix="!", initial_channels=[CHANNEL])

    async def event_ready(self):
        print(f"Bot พร้อมแล้ว | กำลังดู chat ใน #{CHANNEL}")

    async def event_message(self, message: twitchio.Message):
        global count
        if message.echo:
            return
        if message.content.endswith("ห์"):
            count += 1
            print(f'[{count}] {message.author.name}: {message.content}')
            channel = self.get_channel(CHANNEL)
            await channel.send(f"𓈒 ก็คือจิบน้ำหน่อยมั้ยหลงห์กันไปแล้ว {count} ครั้งนะห์ 🥤𓈒")


bot = Bot()
bot.run()
