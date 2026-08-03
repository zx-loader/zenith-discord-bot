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
TRUEMONEY_PHONE = os.environ["TRUEMONEY_PHONE"]

# Packages: label -> (days, price in baht). days=None means permanent/lifetime.
PACKAGES = {
    "3day": {"label": "3 วัน", "days": 3, "price": 10},
    "15day": {"label": "15 วัน", "days": 15, "price": 25},
    "lifetime": {"label": "ถาวร", "days": None, "price": 40},
}

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


async def redeem_truemoney_voucher(voucher_url_or_code: str):
    """
    Redeem a TrueMoney angpao voucher into our shop's wallet.
    Returns (True, amount_baht) on success, (False, error_message) on failure.
    """
    # Extract voucher hash if a full URL was given
    code = voucher_url_or_code.strip()
    if "v=" in code:
        code = code.split("v=")[-1].split("&")[0]

    url = f"https://gift.truemoney.com/campaign/vouchers/{code}/redeem"
    payload = {"mobile": TRUEMONEY_PHONE}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload, timeout=10) as resp:
                status = resp.status
                try:
                    body = await resp.json()
                except Exception:
                    body = await resp.text()

                print(f"[TrueMoney redeem] status={status} body={body}")

                if status == 200 and isinstance(body, dict):
                    status_data = body.get("status", {})
                    if status_data.get("code") == "SUCCESS":
                        amount = float(body["data"]["my_ticket"]["amount_baht"])
                        return True, amount
                    else:
                        return False, status_data.get("message", "Unknown error")
                else:
                    return False, str(body)
        except Exception as e:
            print(f"[TrueMoney redeem] EXCEPTION: {e}")
            return False, str(e)


async def panda_create_key(days):
    """
    Create a new key via Panda API.
    days=None means lifetime/permanent (no expiration).
    Returns (True, key_string) on success, (False, error) on failure.
    """
    url = "https://api.pandauth.com/v2/keys/generate"
    headers = {
        "Authorization": f"Bearer {PANDA_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"service": PANDA_SERVICE, "amount": 1}
    if days is not None:
        payload["expiration_days"] = days

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, headers=headers, json=payload, timeout=10) as resp:
                status = resp.status
                try:
                    body = await resp.json()
                except Exception:
                    body = await resp.text()

                print(f"[Panda create key] status={status} body={body}")

                if status == 200 and isinstance(body, dict):
                    # try a couple of likely response shapes
                    key = None
                    if "key" in body:
                        key = body["key"]
                    elif "keys" in body and isinstance(body["keys"], list) and body["keys"]:
                        key = body["keys"][0]
                    elif "data" in body and isinstance(body["data"], dict):
                        key = body["data"].get("key")

                    if key:
                        return True, key
                    else:
                        return False, f"Unexpected response shape: {body}"
                else:
                    return False, str(body)
        except Exception as e:
            print(f"[Panda create key] EXCEPTION: {e}")
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


class BuyKeyModal(discord.ui.Modal, title="ซื้อ Key"):
    voucher_input = discord.ui.TextInput(
        label="ลิงก์ซองอั่งเปา TrueMoney",
        placeholder="https://gift.truemoney.com/campaign/?v=xxxxxxxx",
        required=True,
    )

    def __init__(self, package_key: str):
        super().__init__()
        self.package_key = package_key

    async def on_submit(self, interaction: discord.Interaction):
        pkg = PACKAGES[self.package_key]
        await interaction.response.defer(ephemeral=True, thinking=True)

        voucher = self.voucher_input.value.strip()
        success, result = await redeem_truemoney_voucher(voucher)

        if not success:
            await interaction.followup.send(
                f"❌ ไม่สามารถใช้ซองนี้ได้\n(debug: `{result}`)", ephemeral=True
            )
            return

        amount = result  # baht received
        if amount < pkg["price"]:
            await interaction.followup.send(
                f"❌ ยอดเงินไม่พอ ({amount:.2f} บาท) แพ็คเกจ {pkg['label']} ราคา {pkg['price']} บาท\n"
                f"⚠️ ระบบรับเงินเข้าร้านแล้ว กรุณาติดต่อแอดมินเพื่อขอเงินคืนส่วนต่างหรือรับคีย์แบบสั้นลง",
                ephemeral=True,
            )
            return

        key_success, key_result = await panda_create_key(pkg["days"])
        if not key_success:
            await interaction.followup.send(
                f"⚠️ รับเงินสำเร็จ ({amount:.2f} บาท) แต่สร้างคีย์อัตโนมัติไม่สำเร็จ\n"
                f"กรุณาติดต่อแอดมินพร้อมแจ้งยอดนี้เพื่อรับคีย์\n(debug: `{key_result}`)",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"✅ ซื้อสำเร็จ! แพ็คเกจ {pkg['label']}\n\n"
            f"🔑 Key ของคุณ:\n`{key_result}`\n\n"
            f"ใช้ปุ่ม Redeem Key เพื่อผูกคีย์นี้กับเครื่องของคุณ",
            ephemeral=True,
        )


class BuyPackageView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(label="3 วัน - 10฿", style=discord.ButtonStyle.secondary)
    async def buy_3day(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BuyKeyModal("3day"))

    @discord.ui.button(label="15 วัน - 25฿", style=discord.ButtonStyle.secondary)
    async def buy_15day(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BuyKeyModal("15day"))

    @discord.ui.button(label="ถาวร - 40฿", style=discord.ButtonStyle.secondary)
    async def buy_lifetime(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BuyKeyModal("lifetime"))


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

    @discord.ui.button(label="💰 ซื้อ Key", style=discord.ButtonStyle.success, custom_id="panel_buykey", row=1)
    async def buy_key(self, interaction: discord.Interaction, button: discord.ui.Button):
        text = "\n".join(f"• {p['label']} — {p['price']} บาท" for p in PACKAGES.values())
        await interaction.response.send_message(
            f"เลือกแพ็คเกจที่ต้องการ:\n{text}\n\nกดปุ่มด้านล่างแล้วส่งลิงก์ซองอั่งเปา TrueMoney",
            view=BuyPackageView(),
            ephemeral=True,
        )


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
