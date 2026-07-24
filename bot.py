import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import asyncio
import os
import time
import threading
import re
from dotenv import load_dotenv

# โหลด TOKEN จากไฟล์ .env (บน Render จะดึงจาก Environment Variables อัตโนมัติ)
load_dotenv()
TOKEN = os.getenv('TOKEN')

class MusicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix='!', intents=intents)
        
        # ระบบเสียง
        self.eq = {'low': 0, 'mid': 0, 'high': 0}
        self.volume = 0.5
        self.current_url = None
        self.current_title = None
        self.start_time = 0
        
        # ระบบ Console Messenger
        self.target_channel_id = None 

    async def setup_hook(self):
        await self.tree.sync()
        print(f"✅ Sync Slash Commands สำเร็จ!")

bot = MusicBot()

# --- ระบบ Console Messenger (ปิดการใช้งานบน Cloud Render เนื่องจากไม่มี Keyboard ให้พิมพ์) ---
def console_input_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    while True:
        try:
            user_input = input("") 
            if not user_input:
                continue

            if user_input.startswith("-pip Channel"):
                try:
                    new_id = int(user_input.replace("-pip Channel", "").strip())
                    bot.target_channel_id = new_id
                    print(f"📌 [System] เปลี่ยนเป้าหมายไปที่ห้องไอดี: {new_id}")
                except ValueError:
                    print("❌ [Error] ไอดีห้องต้องเป็นตัวเลขเท่านั้น")

            elif user_input.startswith("-send"):
                if not bot.target_channel_id:
                    print("⚠️ [System] กรุณาเลือกห้องก่อนด้วย -pip Channel [ID]")
                    continue

                channel = bot.get_channel(bot.target_channel_id)
                if not channel:
                    print("❌ [Error] หาห้องไม่เจอ")
                    continue

                text_match = re.search(r'"(.*?)"', user_input)
                msg_content = text_match.group(1) if text_match else ""
                
                img_part = user_input.split("img")
                discord_files = []
                
                if len(img_part) > 1:
                    filenames = img_part[1].strip().split()
                    for fn in filenames:
                        if os.path.exists(fn):
                            discord_files.append(discord.File(fn))
                        else:
                            print(f"❌ [Error] ไม่พบไฟล์: {fn}")

                if msg_content or discord_files:
                    asyncio.run_coroutine_threadsafe(
                        channel.send(content=msg_content, files=discord_files if discord_files else None), 
                        bot.loop
                    )
                    print(f"✅ [Sent] ส่งข้อความเรียบร้อยแล้ว")

            elif bot.target_channel_id:
                channel = bot.get_channel(bot.target_channel_id)
                if channel:
                    asyncio.run_coroutine_threadsafe(channel.send(user_input), bot.loop)
                else:
                    print("❌ [Error] หาห้องไม่เจอ")
            else:
                print("⚠️ [System] โปรดตั้งค่าห้องก่อนส่งข้อความ: -pip Channel [ID]")
        except EOFError:
            break

# --- ส่วนจัดการเสียง (Equalizer & FFmpeg) ---

def get_ffmpeg_options(seek_time=None):
    filters = (
        f"equalizer=f=60:width_type=h:width=50:g={bot.eq['low']},"
        f"equalizer=f=1000:width_type=h:width=200:g={bot.eq['mid']},"
        f"equalizer=f=8000:width_type=h:width=1000:g={bot.eq['high']}"
    )
    before_args = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
    if seek_time:
        before_args += f' -ss {seek_time}'
    return {'before_options': before_args, 'options': f'-vn -af "{filters}"'}

async def play_audio(vc, seek_time=None):
    if not bot.current_url: return
    # แก้ไขจาก "./ffmpeg.exe" เป็น "ffmpeg" เพื่อใช้โปรแกรมที่ลงไว้ในระบบ Linux ของ Render
    raw_source = discord.FFmpegPCMAudio(bot.current_url, executable="ffmpeg", **get_ffmpeg_options(seek_time))
    source = discord.PCMVolumeTransformer(raw_source, volume=bot.volume)
    bot.start_time = time.time() - (seek_time if seek_time else 0)
    vc.play(source)

def create_eq_embed():
    embed = discord.Embed(title="🎚️ เครื่องเสียงดุริยางค์ (EQ Control)", color=0x2ecc71)
    embed.description = f"**กำลังเล่น:** {bot.current_title}"
    def get_bar(val):
        plus = "🟦" * (val // 4) if val > 0 else ""
        minus = "🟥" * (abs(val) // 4) if val < 0 else ""
        return f"{minus}🔘{plus}" if (plus or minus) else "⬜🔘⬜"
    embed.add_field(name="🎸 LOW", value=f"`{bot.eq['low']} dB`\n{get_bar(bot.eq['low'])}", inline=True)
    embed.add_field(name="🎙️ MID", value=f"`{bot.eq['mid']} dB`\n{get_bar(bot.eq['mid'])}", inline=True)
    embed.add_field(name="🔔 HIGH", value=f"`{bot.eq['high']} dB`\n{get_bar(bot.eq['high'])}", inline=True)
    return embed

class EQControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    async def reload_audio(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            current_pos = int(time.time() - bot.start_time)
            vc.stop()
            await play_audio(vc, seek_time=current_pos)
        await interaction.response.edit_message(embed=create_eq_embed(), view=self)

    @discord.ui.button(label="Bass +", style=discord.ButtonStyle.blurple, row=0)
    async def low_up(self, interaction, b): bot.eq['low'] = min(bot.eq['low'] + 4, 20); await self.reload_audio(interaction)
    @discord.ui.button(label="Mid +", style=discord.ButtonStyle.green, row=0)
    async def mid_up(self, interaction, b): bot.eq['mid'] = min(bot.eq['mid'] + 4, 20); await self.reload_audio(interaction)
    @discord.ui.button(label="High +", style=discord.ButtonStyle.gray, row=0)
    async def high_up(self, interaction, b): bot.eq['high'] = min(bot.eq['high'] + 4, 20); await self.reload_audio(interaction)
    @discord.ui.button(label="Bass -", style=discord.ButtonStyle.blurple, row=1)
    async def low_down(self, interaction, b): bot.eq['low'] = max(bot.eq['low'] - 4, -12); await self.reload_audio(interaction)
    @discord.ui.button(label="Mid -", style=discord.ButtonStyle.green, row=1)
    async def mid_down(self, interaction, b): bot.eq['mid'] = max(bot.eq['mid'] - 4, -12); await self.reload_audio(interaction)
    @discord.ui.button(label="High -", style=discord.ButtonStyle.gray, row=1)
    async def high_down(self, interaction, b): bot.eq['high'] = max(bot.eq['high'] - 4, -12); await self.reload_audio(interaction)
    @discord.ui.button(label="RESET", style=discord.ButtonStyle.danger, row=2)
    async def reset(self, interaction, b): bot.eq = {'low': 0, 'mid': 0, 'high': 0}; await self.reload_audio(interaction)

# --- Slash Commands ---

@bot.tree.command(name="play", description="เล่นเพลง")
async def play(interaction: discord.Interaction, search: str):
    await interaction.response.defer(thinking=True)
    if not interaction.user.voice: return await interaction.followup.send("❌ โปรดเข้าห้องเสียง!")
    vc = interaction.guild.voice_client or await interaction.user.voice.channel.connect()
    try:
        ydl_opts = {'format': 'bestaudio/best', 'noplaylist': True, 'quiet': True, 'default_search': 'ytsearch'}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(search, download=False))
            if 'entries' in info: info = info['entries'][0]
            bot.current_url, bot.current_title = info['url'], info['title']
        if vc.is_playing(): vc.stop()
        await play_audio(vc)
        await interaction.followup.send(embed=create_eq_embed(), view=EQControlView())
    except Exception as e: await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="volume", description="ปรับระดับเสียง (0-200)")
async def volume(interaction: discord.Interaction, level: int):
    if not interaction.guild.voice_client: return await interaction.response.send_message("❌ บอทไม่ได้เล่นเพลงอยู่")
    bot.volume = max(0, min(level, 200)) / 100
    if interaction.guild.voice_client.source: interaction.guild.voice_client.source.volume = bot.volume
    await interaction.response.send_message(f"🔊 ปรับเสียงเป็น {level}%!")

@bot.tree.command(name="stop", description="หยุดเพลง")
async def stop(interaction: discord.Interaction):
    if interaction.guild.voice_client: await interaction.guild.voice_client.disconnect(); await interaction.response.send_message("⏹️ ปิดเครื่องเล่น")

@bot.event
async def on_ready():
    print(f'🚀 บอทออนไลน์: {bot.user.name}')

bot.run(TOKEN)
