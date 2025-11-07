import os
from pyrogram import Client, filters
from config import API_ID, API_HASH, BOT_TOKEN, SESSION
from player import MusicPlayer
import yt_dlp
import logging

logging.basicConfig(level=logging.ERROR)

app = Client("music-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
assistant = Client("assistant", api_id=API_ID, api_hash=API_HASH, session_string=SESSION)
player = MusicPlayer(assistant)


# ----------- YT Downloader -----------
def download_audio(query):
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": "song.mp3",
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(query, download=True)
    return "song.mp3"


# ------------ Commands ---------------

@app.on_message(filters.command("play"))
async def play(_, message):
    if len(message.command) < 2:
        return await message.reply("⚠️ Song name do!")

    query = message.text.split(None, 1)[1]
    m = await message.reply("🔎 Searching…")

    file = download_audio(f"ytsearch:{query}")
    await m.edit("✅ Downloaded\n🎧 Joining VC...")

    await player.join(message.chat.id)
    await player.play(file)

    await m.edit(f"▶️ Playing: **{query}**")


@app.on_message(filters.command("stop"))
async def stop(_, message):
    await player.stop()
    await message.reply("⏹ Stopped")


@app.on_message(filters.command("leave"))
async def leave(_, message):
    await player.leave()
    await message.reply("✅ Left VC")


assistant.start()
app.run()
