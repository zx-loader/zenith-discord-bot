import os
import json
import datetime
import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiohttp

# ---------- CONFIG (from Railway Variables) ----------
DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
PANDA_API_KEY = os.environ["PANDA_API_KEY"]
PANDA_SERVICE = "zenithhub"
TRUEMONEY_PHONE = os.environ["TRUEMONEY_PHONE"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_OWNER = "zx-loader"
GITHUB_KEYPOOL_REPO = "zenith-keypool"
GITHUB_KEYPOOL_FILE = "keypool.json"

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

PREMIUM_ROLE_NAME = "Premium"

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

# ---------- PREMIUM STATUS TRACKING ----------
PURCHASES_FILE = "purchases.json"


def load_purchases():
    if os.path.exists(PURCHASES_FILE):
        with open(PURCHASES_FILE, "r") as f:
            return json.load(f)
    return {}


def save_purchases(data):
    with open(PURCHASES_FILE, "w") as f:
        json.dump(data, f)


purchases_data = load_purchases()


def record_purchase(user_id: int, package_key: str, key: str):
    """
    Save purchase info: which key, which package, when it expires.
    days=None (lifetime) -> expires_at = None
    """
    pkg = PACKAGES[package_key]
    if pkg["days"] is not None:
        expires_at = (
            datetime.datetime.utcnow() + datetime.timedelta(days=pkg["days"])
        ).isoformat()
    else:
        expires_at = None

    purchases_data[str(user_id)] = {
        "package": package_key,
        "package_label": pkg["label"],
        "key": key,
        "expires_at": expires_at,
        "purchased_at": datetime.datetime.utcnow().isoformat(),
    }
    save_purchases(purchases_data)


def is_expired(purchase: dict) -> bool:
    if purchase.get("expires_at") is None:
        return False  # lifetime, never expires
    expires_at = datetime.datetime.fromisoformat(purchase["expires_at"])
    return datetime.datetime.utcnow() > expires_at



WELCOME_FILE = "welcome_config.json"
DEV_ID = 1077542254677344366


def load_welcome_config():
    if os.path.exists(WELCOME_FILE):
        with open(WELCOME_FILE, "r") as f:
            return json.load(f)
    return {}


def save_welcome_config(cfg):
    with open(WELCOME_FILE, "w") as f:
        json.dump(cfg, f)


welcome_config = load_welcome_config()

# ---------- DISCORD BOT SETUP ----------
intents = discord.Intents.default()
intents.members = True  # required for on_member_join welcome messages
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
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://gift.truemoney.com/campaign/",
        "Origin": "https://gift.truemoney.com",
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
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
                    return False, f"HTTP {status}: {str(body)[:200]}"
        except aiohttp.ClientTimeout:
            print("[TrueMoney redeem] TIMEOUT")
            return False, "หมดเวลาเชื่อมต่อ TrueMoney กรุณาลองใหม่"
        except Exception as e:
            print(f"[TrueMoney redeem] EXCEPTION: {e}")
            return False, str(e)


async def panda_create_key(days):
    """
    DEPRECATED — Panda doesn't expose a public API for this.
    Kept as a no-op fallback, not used anymore.
    """
    return False, "Not used — using local key pool instead"


GITHUB_API_BASE = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_KEYPOOL_REPO}/contents/{GITHUB_KEYPOOL_FILE}"


async def github_get_keypool():
    """
    Fetch keypool.json from GitHub.
    Expected format: {"3day": ["KEY1", "KEY2"], "15day": [...], "lifetime": [...]}
    Returns (pool_dict, sha) where sha is needed to update the file later.
    """
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(GITHUB_API_BASE, headers=headers, timeout=10) as resp:
            if resp.status != 200:
                text = await resp.text()
                print(f"[GitHub get keypool] status={resp.status} body={text}")
                return {}, None

            body = await resp.json()
            sha = body["sha"]
            import base64
            content = base64.b64decode(body["content"]).decode("utf-8")
            pool = json.loads(content)
            return pool, sha


async def github_update_keypool(pool: dict, sha: str):
    """
    Push the updated keypool.json back to GitHub, overwriting the old one.
    """
    import base64
    new_content = base64.b64encode(json.dumps(pool, indent=2).encode("utf-8")).decode("utf-8")

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    payload = {
        "message": "Update keypool (key sold)",
        "content": new_content,
        "sha": sha,
    }
    async with aiohttp.ClientSession() as session:
        async with session.put(GITHUB_API_BASE, headers=headers, json=payload, timeout=10) as resp:
            status = resp.status
            body = await resp.text()
            print(f"[GitHub update keypool] status={status} body={body[:300]}")
            return status == 200


async def get_key_from_pool(package_key: str):
    """
    Pop one key from the GitHub-hosted pool for this package.
    Returns the key string, or None if pool is empty / on error.
    """
    pool, sha = await github_get_keypool()
    if sha is None:
        return None

    keys = pool.get(package_key, [])
    if not keys:
        return None

    chosen = keys[0]
    pool[package_key] = keys[1:]

    success = await github_update_keypool(pool, sha)
    if not success:
        return None

    return chosen


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

        key_result = await get_key_from_pool(self.package_key)
        if not key_result:
            await interaction.followup.send(
                f"⚠️ รับเงินสำเร็จ ({amount:.2f} บาท) แต่ตอนนี้คีย์แพ็คเกจ {pkg['label']} หมดคลังชั่วคราว\n"
                f"กรุณาติดต่อแอดมินพร้อมแจ้งยอดนี้เพื่อรับคีย์",
                ephemeral=True,
            )
            return

        # Record purchase + give Premium role
        record_purchase(interaction.user.id, self.package_key, key_result)
        role_msg = ""
        premium_role = discord.utils.get(interaction.guild.roles, name=PREMIUM_ROLE_NAME)
        if premium_role:
            try:
                await interaction.user.add_roles(premium_role, reason="Purchased key")
                role_msg = f"\n🎖️ ได้รับยศ **{PREMIUM_ROLE_NAME}** แล้ว!"
            except Exception as e:
                print(f"[Role assign] failed: {e}")
                role_msg = "\n⚠️ ให้ยศ Premium ไม่สำเร็จ (บอทอาจไม่มีสิทธิ์จัดการ role นี้)"
        else:
            role_msg = f"\n⚠️ ไม่พบ role ชื่อ '{PREMIUM_ROLE_NAME}' ในเซิร์ฟเวอร์"

        await interaction.followup.send(
            f"✅ ซื้อสำเร็จ! แพ็คเกจ {pkg['label']}\n\n"
            f"🔑 Key ของคุณ:\n`{key_result}`\n\n"
            f"ใช้ปุ่ม Redeem Key เพื่อผูกคีย์นี้กับเครื่องของคุณ"
            f"{role_msg}",
            ephemeral=True,
        )


class BuyPackageSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=f"{p['label']} — {p['price']} บาท",
                description=f"รับคีย์ใช้งาน {p['label']}",
                emoji="🔑",
                value=key,
            )
            for key, p in PACKAGES.items()
        ]
        super().__init__(
            placeholder="เลือกแพ็คเกจที่ต้องการซื้อ...",
            options=options,
            custom_id="buy_package_select",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(BuyKeyModal(self.values[0]))


class BuyPackageView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(BuyPackageSelect())


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

    @discord.ui.button(label="🔑 Redeem Key", style=discord.ButtonStyle.secondary, custom_id="panel_redeem")
    async def redeem_key(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RedeemKeyModal(then_send_script=False))

    @discord.ui.button(label="📜 Get Script", style=discord.ButtonStyle.secondary, custom_id="panel_getscript")
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

    @discord.ui.button(label="💎 ซื้อ Key", style=discord.ButtonStyle.primary, custom_id="panel_buykey", row=1)
    async def buy_key(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="💎 ซื้อ Key — Zenith Soul HUB",
            description="เลือกแพ็คเกจที่ต้องการจากเมนูด้านล่าง แล้วเตรียมลิงก์ซองอั่งเปา TrueMoney ให้พร้อม",
            color=discord.Color.gold(),
        )
        for p in PACKAGES.values():
            embed.add_field(name=p["label"], value=f"{p['price']} บาท", inline=True)
        await interaction.response.send_message(embed=embed, view=BuyPackageView(), ephemeral=True)


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
    embed = discord.Embed(
        title="⚡ ZENITH SOUL HUB",
        description="**Premium Key Script — Zenith**",
        color=discord.Color.from_rgb(20, 20, 20),
    )
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


@bot.tree.command(name="testkeypool", description="[Admin only] Test pulling a key from the pool, skipping payment")
@app_commands.describe(package="Which package to test")
@app_commands.choices(package=[
    app_commands.Choice(name="3 วัน", value="3day"),
    app_commands.Choice(name="15 วัน", value="15day"),
    app_commands.Choice(name="ถาวร", value="lifetime"),
])
async def testkeypool(interaction: discord.Interaction, package: app_commands.Choice[str]):
    if interaction.user.id not in ADMIN_IDS:
        await interaction.response.send_message(
            "You don't have permission to use this command.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    key_result = await get_key_from_pool(package.value)

    if not key_result:
        await interaction.followup.send(
            f"❌ คลัง key แพ็คเกจ {package.name} ว่างเปล่า หรือดึงจาก GitHub ไม่สำเร็จ (เช็ค logs)",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        f"✅ ทดสอบสำเร็จ! ดึง key จากคลัง {package.name} ได้:\n`{key_result}`\n\n"
        f"(ลองเข้าไปดู repo zenith-keypool ว่า key นี้ถูกลบออกจากไฟล์แล้วหรือยัง)",
        ephemeral=True,
    )


@bot.tree.command(name="setwelcome", description="ตั้งค่าห้องสำหรับส่งข้อความต้อนรับสมาชิกใหม่")
async def setwelcome(interaction: discord.Interaction):
    if interaction.user.id != DEV_ID:
        await interaction.response.send_message(
            "You don't have permission to use this command.", ephemeral=True
        )
        return

    welcome_config[str(interaction.guild.id)] = interaction.channel.id
    save_welcome_config(welcome_config)

    await interaction.response.send_message(
        f"✅ ตั้งค่าแล้ว จะส่งข้อความต้อนรับสมาชิกใหม่ในห้องนี้ ({interaction.channel.mention})",
        ephemeral=True,
    )


@bot.event
async def on_member_join(member: discord.Member):
    channel_id = welcome_config.get(str(member.guild.id))
    if not channel_id:
        return

    channel = member.guild.get_channel(channel_id)
    if not channel:
        return

    embed = discord.Embed(
        title="👋 Welcome to Zenith Hub",
        description=f"ยินดีต้อนรับ {member.mention} เข้าสู่ Zenith Soul HUB!",
        color=discord.Color.blurple(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"สมาชิกคนที่ {member.guild.member_count}")

    try:
        await channel.send(embed=embed)
    except Exception as e:
        print(f"[Welcome] failed to send: {e}")


@bot.tree.command(name="status", description="ดูสถานะของคุณ (คีย์, วันหมดอายุ, ยศ Premium)")
async def status(interaction: discord.Interaction):
    purchase = purchases_data.get(str(interaction.user.id))

    embed = discord.Embed(title="📊 สถานะของคุณ", color=discord.Color.from_str("#C0C0C0"))
    embed.add_field(name="ชื่อผู้ใช้", value=interaction.user.mention, inline=False)

    if not purchase:
        embed.add_field(name="คีย์", value="ยังไม่มีการซื้อคีย์", inline=False)
        embed.add_field(name="สถานะ Premium", value="❌ ไม่มี", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    expired = is_expired(purchase)
    if purchase["expires_at"] is None:
        expire_text = "ถาวร (ไม่มีวันหมดอายุ)"
    else:
        expire_dt = datetime.datetime.fromisoformat(purchase["expires_at"])
        expire_text = expire_dt.strftime("%Y-%m-%d %H:%M UTC")
        if expired:
            expire_text += " (หมดอายุแล้ว)"

    embed.add_field(name="แพ็คเกจ", value=purchase["package_label"], inline=True)
    embed.add_field(name="คีย์", value=f"`{purchase['key']}`", inline=True)
    embed.add_field(name="วันหมดอายุ", value=expire_text, inline=False)
    embed.add_field(
        name="สถานะ Premium",
        value="✅ Active" if not expired else "❌ หมดอายุแล้ว",
        inline=False,
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@tasks.loop(hours=24)
async def check_expired_premiums():
    """Runs once a day: removes Premium role from anyone whose key expired."""
    for guild in bot.guilds:
        premium_role = discord.utils.get(guild.roles, name=PREMIUM_ROLE_NAME)
        if not premium_role:
            continue

        for user_id_str, purchase in list(purchases_data.items()):
            if not is_expired(purchase):
                continue

            member = guild.get_member(int(user_id_str))
            if member and premium_role in member.roles:
                try:
                    await member.remove_roles(premium_role, reason="Key expired")
                    print(f"[Expiry check] Removed Premium from {member} (key expired)")
                except Exception as e:
                    print(f"[Expiry check] Failed to remove role from {user_id_str}: {e}")


# ---------- STARTUP ----------
@bot.event
async def on_ready():
    bot.add_view(PanelView())  # register persistent view so buttons keep working after restart
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Sync error: {e}")

    if not check_expired_premiums.is_running():
        check_expired_premiums.start()

    print(f"Bot is online as {bot.user}")


bot.run(DISCORD_BOT_TOKEN)
