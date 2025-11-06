# File: main.py
# Telegram Music Assistant for Voice Chats (Pyrogram + pytgcalls)
# --- Save this file as main.py ---
# Required environment variables (set these before running / on pella.app):
#   API_ID (int), API_HASH (str), BOT_TOKEN (str), SESSION_NAME (str, optional: else bot uses BOT_TOKEN session)
#
# Dependencies: pyrogram, tgcrypto, pytgcalls, yt-dlp, pydub, asyncio, aiohttp, python-dotenv
# ffmpeg must be installed in the environment.

import os
import asyncio
import shutil
import tempfile
from typing import Dict, List
from pyrogram import Client, filters
from pyrogram.types import Message
from pytgcalls import PyTgCalls, idle
from pytgcalls.types.input_stream import InputAudioStream
from pytgcalls.exceptions import GroupCallNotFoundError
from yt_dlp import YoutubeDL
import subprocess

API_ID = int(os.environ.get("API_ID", "20898349"))
API_HASH = os.environ.get("API_HASH", "9fdb830d1e435b785f536247f49e7d87")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8225247075:AAGJcw3n1oOhgJFG-wUSmideBEkC7aGfZd4")
SESSION_NAME = os.environ.get("SESSION_NAME", "music_bot_session")  # can be a string session file or "bot"

if not (API_ID and API_HASH and BOT_TOKEN):
    raise SystemExit("Set API_ID, API_HASH and BOT_TOKEN environment variables before running.")

# Pyrogram client: if using bot token, set session_name to "bot" to use Bot API session
if SESSION_NAME == "bot":
    app = Client("bot", bot_token=BOT_TOKEN, api_id=API_ID, api_hash=API_HASH)
else:
    # Use a user/bot string session file name; if you want pure bot-only, set SESSION_NAME="bot"
    app = Client(SESSION_NAME, api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

pytgcalls = PyTgCalls(app)

# Playback queues per chat (group call)
queues: Dict[int, asyncio.Queue] = {}
playing: Dict[int, Dict] = {}  # store metadata for currently playing {chat_id: {title, file}}

YTDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "prefer_ffmpeg": True,
    "outtmpl": "%(id)s.%(ext)s",
    "geo_bypass": True,
    "nocheckcertificate": True,
    "extract_flat": False,
    "source_address": "0.0.0.0",
}

ydl = YoutubeDL(YTDL_OPTS)

# Utilities

def download_audio(url: str, download_dir: str) -> Dict:
    """
    Downloads audio using yt-dlp and returns dict: {title, filename, duration}
    """
    opts = dict(YTDL_OPTS)
    opts["outtmpl"] = os.path.join(download_dir, "%(id)s.%(ext)s")
    with YoutubeDL(opts) as y:
        info = y.extract_info(url, download=True)
        # If it's a playlist, take the first entry
        if "entries" in info:
            info = info["entries"][0]
        # Find the downloaded filename
        ext = info.get("ext") or "m4a"
        filename = os.path.join(download_dir, f"{info['id']}.{ext}")
        return {"title": info.get("title", "Unknown"), "file": filename, "duration": info.get("duration")}

async def ensure_queue(chat_id: int):
    if chat_id not in queues:
        queues[chat_id] = asyncio.Queue()

async def _play_next(chat_id: int):
    """
    Internal: pop next item from queue and stream to vc
    """
    await ensure_queue(chat_id)
    if queues[chat_id].empty():
        # nothing to play
        playing.pop(chat_id, None)
        return

    item = await queues[chat_id].get()
    playing[chat_id] = item
    file_path = item["file"]
    # Convert to raw-compatible input for pytgcalls using ffmpeg to create .raw? pytgcalls accepts raw/ogg often.
    # We will stream using ffmpeg to a temp opus file.
    tmp_out = tempfile.mktemp(suffix=".opus")
    # create opus with libopus if available; fallback to ogg/opus container
    cmd = [
        "ffmpeg", "-y", "-i", file_path,
        "-vn", "-c:a", "libopus", "-b:a", "128k", "-vbr", "on",
        "-f", "opus", tmp_out
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        # fallback to raw pcm 48k
        tmp_out = tempfile.mktemp(suffix=".raw")
        cmd = [
            "ffmpeg", "-y", "-i", file_path,
            "-vn", "-ar", "48000", "-ac", "2", "-f", "s16le", tmp_out
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Attach to group call and play
    try:
        await pytgcalls.join_group_call(
            chat_id,
            InputAudioStream(tmp_out),
        )
    except GroupCallNotFoundError:
        # no active voice chat: attempt to start via Pyrogram? Bot cannot start group call; user must start voice chat.
        # We raise to stop
        playing.pop(chat_id, None)
        return

    # wait for duration if present, else wait until the stream stops; here we poll for queue changes
    duration = item.get("duration")
    # If duration known, sleep and then play next
    if duration:
        await asyncio.sleep(duration + 0.5)
    else:
        # wait a safe default (5 minutes) or until queue trigger; simpler: sleep until file finishes — use ffprobe to get duration
        try:
            ffprobe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path],
                capture_output=True, text=True, check=True
            )
            d = float(ffprobe.stdout.strip())
            await asyncio.sleep(d + 0.5)
        except Exception:
            await asyncio.sleep(300)

    # after playing, leave or continue
    try:
        await pytgcalls.leave_group_call(chat_id)
    except Exception:
        pass

    # cleanup temp opus/raw
    try:
        os.remove(tmp_out)
    except Exception:
        pass

    # play next item if exists
    if not queues[chat_id].empty():
        await _play_next(chat_id)
    else:
        playing.pop(chat_id, None)

# Bot Commands

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(c: Client, m: Message):
    await m.reply_text("Music assistant ready. Invite to group and start a voice chat. Use /play in group.")

@app.on_message(filters.command("play") & filters.chat_type.groups)
async def play_cmd(c: Client, m: Message):
    chat_id = m.chat.id
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        await m.reply_text("Usage: /play <YouTube url or search query>")
        return
    query = args[1].strip()

    await ensure_queue(chat_id)
    msg = await m.reply_text("🔎 Processing...")

    # If it's not a URL, perform a ytsearch:
    is_url = query.startswith("http://") or query.startswith("https://")
    url = query
    if not is_url:
        # search and get first result
        try:
            info = ydl.extract_info(f"ytsearch:{query}", download=False)["entries"][0]
            url = info["webpage_url"]
        except Exception as e:
            await msg.edit_text("❌ Search failed.")
            return

    # download audio into temp dir
    download_dir = tempfile.mkdtemp(prefix="tg_music_")
    try:
        info = download_audio(url, download_dir)
    except Exception as e:
        shutil.rmtree(download_dir, ignore_errors=True)
        await msg.edit_text("❌ Download failed.")
        return

    # add to queue
    await queues[chat_id].put(info)
    pos = queues[chat_id].qsize()
    await msg.edit_text(f"✅ Queued: {info['title']}\nPosition: {pos}")

    # if not currently playing, start playback task
    if chat_id not in playing:
        asyncio.create_task(_play_next(chat_id))

@app.on_message(filters.command("queue") & filters.chat_type.groups)
async def queue_cmd(c: Client, m: Message):
    chat_id = m.chat.id
    await ensure_queue(chat_id)
    q = queues[chat_id]
    if q.empty() and chat_id not in playing:
        await m.reply_text("Queue is empty.")
        return
    lines: List[str] = []
    if chat_id in playing:
        curr = playing[chat_id]
        lines.append(f"▶ Now: {curr.get('title')}")
    idx = 1
    # Build a copy of queue items (non-destructive)
    items = list(q._queue)  # type: ignore
    for it in items:
        lines.append(f"{idx}. {it.get('title')}")
        idx += 1
    await m.reply_text("\n".join(lines))

@app.on_message(filters.command("skip") & filters.chat_type.groups)
async def skip_cmd(c: Client, m: Message):
    chat_id = m.chat.id
    # Leaving the group call will stop current playback; then play next
    try:
        await pytgcalls.leave_group_call(chat_id)
    except Exception:
        pass
    await m.reply_text("⏭ Skipped current track.")
    # Start next if queue not empty
    if chat_id in queues and not queues[chat_id].empty():
        asyncio.create_task(_play_next(chat_id))

@app.on_message(filters.command("stop") & filters.chat_type.groups)
async def stop_cmd(c: Client, m: Message):
    chat_id = m.chat.id
    # Clear queue and leave
    if chat_id in queues:
        while not queues[chat_id].empty():
            item = await queues[chat_id].get()
            # cleanup downloaded files if present
            try:
                os.remove(item.get("file"))
            except Exception:
                pass
    try:
        await pytgcalls.leave_group_call(chat_id)
    except Exception:
        pass
    playing.pop(chat_id, None)
    await m.reply_text("⏹ Stopped and cleared queue.")

@app.on_message(filters.command("pause") & filters.chat_type.groups)
async def pause_cmd(c: Client, m: Message):
    chat_id = m.chat.id
    try:
        await pytgcalls.pause_stream(chat_id)
        await m.reply_text("⏸ Paused.")
    except Exception:
        await m.reply_text("Failed to pause. Is the voice chat active?")

@app.on_message(filters.command("resume") & filters.chat_type.groups)
async def resume_cmd(c: Client, m: Message):
    chat_id = m.chat.id
    try:
        await pytgcalls.resume_stream(chat_id)
        await m.reply_text("▶ Resumed.")
    except Exception:
        await m.reply_text("Failed to resume. Is the voice chat active?")

@app.on_message(filters.command("ping") & filters.chat_type.groups)
async def ping_cmd(c: Client, m: Message):
    await m.reply_text("pong")

# Graceful startup/shutdown handlers
@app.on_message(filters.command("help") & filters.private)
async def help_pm(c: Client, m: Message):
    await m.reply_text("/play <url or search>\n/queue\n/skip\n/stop\n/pause\n/resume\n\nUse in groups. Start a voice chat in the group and invite the bot (or ensure the bot is admin if needed).")

async def main():
    await app.start()
    await pytgcalls.start()
    print("Bot started.")
    # keep running
    await idle()
    await pytgcalls.stop()
    await app.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
