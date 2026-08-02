import os
import json
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp

# ---------- CONFIG (from Railway Variables) ----------
DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
PANDA_API_KEY = os.environ["PANDA_API_KEY"]
PANDA_SERVICE = "zenithhub"

# Allowed admins for /panel and /editpanel
ADMIN_IDS = {
    1077542254677344366,
    1393262647889104937,
    766955807836995654,
}

SCRIPT_LINK = f"https://ads.pandauth.com/getkey/{PANDA_SERVICE}"

# Simple local storage: which discord user redeemed which key
DATA_FILE = "redeemed_keys.json"


def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}


def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)


redeemed_data = load_data()

# ---------- DISCORD BOT SETUP ----------
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


# ---------- PANDA AUTH API HELPERS ----------
async def panda_validate_key(key: str):
    """
    Check if a key is valid/active for our service.
    Returns (True, data) if valid, (False, error_message) if not.
    """
    url = "https://api.pandauth.com/v2/keys/validate"
    headers = {
        "Authorization": f"Bearer {PANDA_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"service": PANDA_SERVICE, "key": key}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, headers=headers, json=payload, timeout=10) as resp:
                status = resp.status
                try:
                    body = await resp.json()
                except Exception:
                    body = await resp.text()

                print(f"[Panda validate] status={status} body={body}")

                if status == 200 and isinstance(body, dict) and body.get("valid"):
                    return True, body
                else:
                    return False, body
        except Exception as e:
            print(f"[Panda validate] EXCEPTION: {e}")
            return False, str(e)


async def panda_reset_hwid(key: str):
    """
    Ask Panda to reset the HWID lock for a given key.
    Returns (True, data) on success, (False, error_message) on failure.
    """
    url = "https://api.pandauth.com/v2/keys/reset-hwid"
    headers = {
        "Authorization": f"Bearer {PANDA_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"service": PANDA_SERVICE, "key": key}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, headers=headers, json=payload, timeout=10) as resp:
                status = resp.status
                try:
                    body = await resp.json()
                except Exception:
                    body = await resp.text()

                print(f"[Panda reset hwid] status={status} body={body}")

                if status == 200:
                    return True, body
                else:
                    return False, body
        except Exception as e:
            print(f"[Panda reset hwid] EXCEPTION: {e}")
            return False, str(e)


# ---------- MODALS ----------
class RedeemKeyModal(discord.ui.Modal, title="Redeem Your Key"):
    key_input = discord.ui.TextInput(
        label="Keys",
        placeholder="PANDA-XXXX-XXXX-XXXX",
        required=True,
    )

    def __init__(self, then_send_script: bool = False):
        super().__init__()
        self.then_send_script = then_send_script

    async def on_submit(self, interaction: discord.Interaction):
        key = self.key_input.value.strip()
        valid, data = await panda_validate_key(key)

        if not valid:
            await interaction.response.send_message(
                f"❌ คีย์ไม่ถูกต้อง\n(debug: `{data}`)", ephemeral=True
            )
            return

        # Save redeem record
        redeemed_data[str(interaction.user.id)] = key
        save_data(redeemed_data)

        if self.then_send_script:
            await interaction.response.send_message(
                f"✅ Redeem สำเร็จ!\n\n📜 นี่คือสคริปต์ของคุณ:\n{SCRIPT_LINK}",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message("✅ Redeem สำเร็จ", ephemeral=True)


class ResetHWIDModal(discord.ui.Modal, title="Reset HWID"):
    key_input = discord.ui.TextInput(
        label="Keys",
        placeholder="PANDA-XXXX-XXXX-XXXX",
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        key = self.key_input.value.strip()
        success, data = await panda_reset_hwid(key)

        if success:
            await interaction.response.send_message("✅ รีเซ็ต HWID สำเร็จ", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"❌ รีเซ็ต HWID ไม่สำเร็จ\n(debug: `{data}`)", ephemeral=True
            )


# ---------- PANEL VIEW (buttons) ----------
class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔑 Redeem Key", style=discord.ButtonStyle.success, custom_id="panel_redeem")
    async def redeem_key(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RedeemKeyModal(then_send_script=False))

    @discord.ui.button(label="📜 Get Script", style=discord.ButtonStyle.primary, custom_id="panel_getscript")
    async def get_script(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        existing_key = redeemed_data.get(user_id)

        if existing_key:
            # Re-validate in case it was revoked
            valid, data = await panda_validate_key(existing_key)
            if valid:
                await interaction.response.send_message(
                    f"📜 นี่คือสคริปต์ของคุณ:\n{SCRIPT_LINK}", ephemeral=True
                )
                return
            else:
                # stored key no longer valid, remove it
                redeemed_data.pop(user_id, None)
                save_data(redeemed_data)

        # No valid key on file — open modal to enter one now
        await interaction.response.send_modal(RedeemKeyModal(then_send_script=True))

    @discord.ui.button(label="🔄 Reset HWID", style=discord.ButtonStyle.secondary, custom_id="panel_resethwid")
    async def reset_hwid(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ResetHWIDModal())


# ---------- SLASH COMMANDS ----------
def is_admin():
    async def predicate(interaction: discord.Interaction):
        if interaction.user.id not in ADMIN_IDS:
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)


@bot.tree.command(name="panel", description="Show the Zenith Soul HUB key panel")
@is_admin()
async def panel(interaction: discord.Interaction):
    embed = discord.Embed(description="สามารถซื้อได้ที่ห้อง 🎫 ทิคเก็ต เลยครับ")
    embed.set_image(url="attachment://banner.png")
    # NOTE: if you don't have a local banner file, remove set_image line above
    # and instead use embed.set_image(url="<your image URL>")
    await interaction.response.send_message(embed=embed, view=PanelView())


@bot.tree.command(name="editpanel", description="Edit the panel message text/image")
@is_admin()
@app_commands.describe(text="New embed text", image_url="New banner image URL (optional)")
async def editpanel(interaction: discord.Interaction, text: str, image_url: str = None):
    embed = discord.Embed(description=text)
    if image_url:
        embed.set_image(url=image_url)

    # Find the most recent panel message from this bot in the channel and edit it
    found = False
    async for msg in interaction.channel.history(limit=50):
        if msg.author.id == bot.user.id and msg.components:
            await msg.edit(embed=embed, view=PanelView())
            found = True
            break

    if found:
        await interaction.response.send_message("✅ Panel updated", ephemeral=True)
    else:
        await interaction.response.send_message(
            "❌ ไม่พบ panel message ในช่องนี้ (ลองรัน /panel ก่อน)", ephemeral=True
        )


# ---------- STARTUP ----------
@bot.event
async def on_ready():
    bot.add_view(PanelView())  # register persistent view so buttons keep working after restart
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Sync error: {e}")
    print(f"Bot is online as {bot.user}")


bot.run(DISCORD_BOT_TOKEN)
