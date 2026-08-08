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
GITHUB_RULES_FILE = "rules.json"

# Packages: label -> (days, price in baht). days=None means permanent/lifetime.
PACKAGES = {
    "lifetime": {"label": "ถาวร", "days": None, "price": 149},
}

# Allowed admins for /panel and /editpanel
ADMIN_IDS = {
    1077542254677344366,
    1393262647889104937,
    766955807836995654,
}

SCRIPT_LINK = f"https://ads.pandauth.com/getkey/{PANDA_SERVICE}"

PREMIUM_ROLE_NAME = "Premium"
AUTO_PANEL_CHANNEL_NAME = "premium"  # channel where /panel auto-posts/updates on bot startup
WELCOME_CHANNEL_NAME = "welce"  # channel name (without emoji) where welcome messages are posted

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

# ---------- SOLD KEYS TRACKING (stored on GitHub, same repo as keypool) ----------
SOLD_KEYS_FILE = "sold_keys.json"


async def mark_key_sold(key: str, user_id: int):
    """
    Record that this key was sold to this discord user, pushing the update to GitHub.
    Format: {key: discord_user_id}
    """
    data, sha = await github_get_json(SOLD_KEYS_FILE)
    if sha is None:
        # File doesn't exist yet or fetch failed — try creating it fresh
        data = {}
        sha = None

    data[key] = str(user_id)

    if sha:
        await github_update_json(SOLD_KEYS_FILE, data, sha, f"Mark key sold: {key}")
    else:
        # Create the file for the first time
        import base64
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        }
        content = base64.b64encode(json.dumps(data, indent=2).encode("utf-8")).decode("utf-8")
        payload = {"message": f"Create sold_keys.json (mark {key} sold)", "content": content}
        async with aiohttp.ClientSession() as session:
            async with session.put(
                github_api_url(SOLD_KEYS_FILE), headers=headers, json=payload, timeout=10
            ) as resp:
                text = await resp.text()
                print(f"[GitHub create sold_keys] status={resp.status} body={text[:200]}")




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

    existing = purchases_data.get(str(user_id), {})
    purchase_count = existing.get("purchase_count", 0) + 1

    purchases_data[str(user_id)] = {
        "package": package_key,
        "package_label": pkg["label"],
        "key": key,
        "expires_at": expires_at,
        "purchased_at": datetime.datetime.utcnow().isoformat(),
        "purchase_count": purchase_count,
    }
    save_purchases(purchases_data)


def is_expired(purchase: dict) -> bool:
    if purchase.get("expires_at") is None:
        return False  # lifetime, never expires
    expires_at = datetime.datetime.fromisoformat(purchase["expires_at"])
    return datetime.datetime.utcnow() > expires_at



DEV_ID = 1077542254677344366

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


def github_api_url(filename: str) -> str:
    return f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_KEYPOOL_REPO}/contents/{filename}"


async def github_get_json(filename: str):
    """
    Fetch a JSON file from the zenith-keypool GitHub repo.
    Returns (data_dict, sha) where sha is needed to update the file later.
    On failure, returns (None, None) so callers can tell "empty" apart from "error".
    """
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    url = github_api_url(filename)
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=10) as resp:
            if resp.status != 200:
                text = await resp.text()
                print(f"[GitHub get {filename}] status={resp.status} body={text}")
                return None, None

            body = await resp.json()
            sha = body["sha"]
            import base64
            content = base64.b64decode(body["content"]).decode("utf-8").strip()

            if not content:
                # File exists but is empty (0 bytes) — treat as empty dict
                print(f"[GitHub get {filename}] file is empty, treating as {{}}")
                return {}, sha

            try:
                data = json.loads(content)
            except json.JSONDecodeError as e:
                print(f"[GitHub get {filename}] JSON parse error: {e}, content was: {content[:200]!r}")
                return {}, sha

            return data, sha


async def github_update_json(filename: str, data: dict, sha: str, message: str):
    """
    Push updated JSON content back to GitHub, overwriting the old file.
    """
    import base64
    new_content = base64.b64encode(json.dumps(data, indent=2).encode("utf-8")).decode("utf-8")

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    payload = {
        "message": message,
        "content": new_content,
        "sha": sha,
    }
    url = github_api_url(filename)
    async with aiohttp.ClientSession() as session:
        async with session.put(url, headers=headers, json=payload, timeout=10) as resp:
            status = resp.status
            body = await resp.text()
            print(f"[GitHub update {filename}] status={status} body={body[:300]}")
            return status == 200


async def github_get_keypool():
    pool, sha = await github_get_json(GITHUB_KEYPOOL_FILE)
    return (pool or {}), sha


async def github_update_keypool(pool: dict, sha: str):
    return await github_update_json(GITHUB_KEYPOOL_FILE, pool, sha, "Update keypool (key sold)")


async def get_stock_counts() -> dict:
    """Returns {package_key: count} for all packages, e.g. {"lifetime": 5}"""
    pool, sha = await github_get_keypool()
    if sha is None:
        return {k: 0 for k in PACKAGES}
    return {k: len(pool.get(k, [])) for k in PACKAGES}


DEFAULT_RULES_TEXT = "แอดมินยังไม่ได้ตั้งค่ากฎ/เงื่อนไขการใช้งาน — ใช้ /setrules เพื่อตั้งค่า"


async def get_rules_text() -> str:
    data, sha = await github_get_json(GITHUB_RULES_FILE)
    if sha is None or not data or not data.get("text"):
        return DEFAULT_RULES_TEXT
    return data["text"]


async def set_rules_text(new_text: str) -> bool:
    _, sha = await github_get_json(GITHUB_RULES_FILE)
    return await github_update_json(GITHUB_RULES_FILE, {"text": new_text}, sha, "Update rules text")


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

        # We no longer call Panda's API directly (no public endpoint for that).
        # Panda validates the key for real when the script runs in-game (HWID lock etc).
        # Here we just record that this user redeemed this key.
        redeemed_data[str(interaction.user.id)] = key
        save_data(redeemed_data)
        await mark_key_sold(key, interaction.user.id)

        if self.then_send_script:
            await interaction.response.send_message(
                f"✅ Redeem สำเร็จ!\n\n📜 นี่คือสคริปต์ของคุณ:\n{SCRIPT_LINK}\n\n"
                f"⚠️ คีย์นี้จะถูกตรวจสอบจริงตอนรันสคริปต์ในเกม",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "✅ Redeem สำเร็จ\n\n⚠️ คีย์นี้จะถูกตรวจสอบจริงตอนรันสคริปต์ในเกม",
                ephemeral=True,
            )


class BuyKeyModal(discord.ui.Modal, title="ซื้อ Key"):
    voucher_input = discord.ui.TextInput(
        label="ลิงก์ซองอั่งเปา TrueMoney",
        placeholder="https://gift.truemoney.com/campaign/?v=xxxxxxxx",
        required=False,
    )

    def __init__(self, package_key: str, skip_payment: bool = False):
        super().__init__()
        self.package_key = package_key
        self.skip_payment = skip_payment
        if skip_payment:
            self.voucher_input.label = "[TEST MODE] ใส่อะไรก็ได้ (ไม่เช็คจริง)"
            self.voucher_input.required = False

    async def on_submit(self, interaction: discord.Interaction):
        pkg = PACKAGES[self.package_key]
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            if self.skip_payment:
                amount = pkg["price"]  # pretend full payment was received
            else:
                voucher = self.voucher_input.value.strip()
                success, result = await redeem_truemoney_voucher(voucher)

                if not success:
                    await interaction.followup.send(
                        "ตรวจสอบซองไม่พบยอดเงิน ลองเช็คลิงก์อีกครั้งแล้วส่งใหม่ได้เลยครับ",
                        ephemeral=True,
                    )
                    return

                amount = result  # baht received
                if amount < pkg["price"]:
                    await interaction.followup.send(
                        f"ยอดในซองไม่ครบราคาแพ็คเกจ (ได้รับ {amount:.2f} บาท) "
                        f"ทักแอดมินพร้อมแจ้งยอดนี้ เดี๋ยวจัดการให้ครับ",
                        ephemeral=True,
                    )
                    return

            key_result = await get_key_from_pool(self.package_key)
            if not key_result:
                await interaction.followup.send(
                    f"รับเงินเรียบร้อย ({amount:.2f} บาท) แต่คีย์แพ็คเกจ {pkg['label']} หมดสต๊อกพอดี\n"
                    f"ทักแอดมินพร้อมแจ้งยอดนี้ได้เลยครับ จะจัดคีย์ให้",
                    ephemeral=True,
                )
                return

            # Record purchase + give Premium role
            record_purchase(interaction.user.id, self.package_key, key_result)
            await mark_key_sold(key_result, interaction.user.id)
            role_msg = ""
            premium_role = discord.utils.get(interaction.guild.roles, name=PREMIUM_ROLE_NAME)
            if premium_role:
                try:
                    await interaction.user.add_roles(premium_role, reason="Purchased key")
                    role_msg = f"\nได้รับยศ **{PREMIUM_ROLE_NAME}** แล้วครับ"
                except Exception as e:
                    print(f"[Role assign] failed: {e}")
                    role_msg = "\nให้ยศ Premium ไม่สำเร็จ ทักแอดมินให้เพิ่มให้หน่อยครับ"
            else:
                role_msg = f"\nไม่พบยศ '{PREMIUM_ROLE_NAME}' ในเซิร์ฟเวอร์ ทักแอดมินได้เลยครับ"

            purchased_at = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            test_tag = "\n(โหมดทดสอบ ไม่มีการตัดเงินจริง)" if self.skip_payment else ""

            await interaction.followup.send(
                f"ซื้อสำเร็จครับ 🎉\n\n"
                f"🔑 คีย์: `{key_result}`\n"
                f"🕒 เวลาที่ซื้อ: {purchased_at}\n"
                f"📦 แพ็คเกจ: {pkg['label']}\n\n"
                f"กดปุ่ม Redeem Key เพื่อผูกคีย์กับเครื่องของคุณได้เลยครับ"
                f"{role_msg}{test_tag}",
                ephemeral=True,
            )
        except Exception as e:
            print(f"[BuyKeyModal] UNEXPECTED ERROR: {e}")
            await interaction.followup.send(
                f"เกิดข้อผิดพลาดระหว่างดำเนินการ ทักแอดมินได้เลยครับ\n(debug: `{e}`)",
                ephemeral=True,
            )


class BuyPackageSelect(discord.ui.Select):
    def __init__(self, stock: dict):
        options = []
        for key, p in PACKAGES.items():
            count = stock.get(key, 0)
            stock_text = f"เหลือ {count}" if count > 0 else "หมด"
            options.append(
                discord.SelectOption(
                    label=f"{p['label']} — {p['price']} บาท ({stock_text})",
                    description=f"รับคีย์ใช้งาน {p['label']}",
                    emoji="🔑" if count > 0 else "❌",
                    value=key,
                )
            )
        super().__init__(
            placeholder="เลือกแพ็คเกจที่ต้องการซื้อ...",
            options=options,
            custom_id="buy_package_select",
        )
        self.stock = stock

    async def callback(self, interaction: discord.Interaction):
        chosen = self.values[0]
        if self.stock.get(chosen, 0) <= 0:
            await interaction.response.send_message(
                f"แพ็คเกจ {PACKAGES[chosen]['label']} หมดสต๊อกพอดีครับ รอแอดมินเติมสต๊อกอีกนิดนะครับ",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(BuyKeyModal(chosen))


class BuyPackageView(discord.ui.View):
    def __init__(self, stock: dict):
        super().__init__(timeout=120)
        self.add_item(BuyPackageSelect(stock))


class TestBuyPackageSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=f"{p['label']} — {p['price']} บาท",
                description=f"[TEST] จำลองซื้อ {p['label']} ไม่มีการตัดเงินจริง",
                emoji="🧪",
                value=key,
            )
            for key, p in PACKAGES.items()
        ]
        super().__init__(
            placeholder="[TEST MODE] เลือกแพ็คเกจที่ต้องการจำลอง...",
            options=options,
            custom_id="test_buy_package_select",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(BuyKeyModal(self.values[0], skip_payment=True))


class TestBuyPackageView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(TestBuyPackageSelect())


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
            await interaction.response.send_message(
                f"📜 นี่คือสคริปต์ของคุณ:\n{SCRIPT_LINK}", ephemeral=True
            )
            return

        # No key on file — open modal to enter one now
        await interaction.response.send_modal(RedeemKeyModal(then_send_script=True))

    @discord.ui.button(label="🔄 Reset HWID", style=discord.ButtonStyle.secondary, custom_id="panel_resethwid")
    async def reset_hwid(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ResetHWIDModal())

    @discord.ui.button(label="💎 ซื้อ Key", style=discord.ButtonStyle.danger, custom_id="panel_buykey", row=1)
    async def buy_key(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        stock = await get_stock_counts()

        embed = discord.Embed(
            title="🔑 Zenith Script — Shop",
            description="เลือกแพ็คเกจที่ต้องการจากเมนูด้านล่าง แล้วเตรียมลิงก์ซองอั่งเปา TrueMoney ให้พร้อมนะครับ\n\nรับชำระผ่านซองอั่งเปาเท่านั้น (ยังไม่รองรับโอนผ่านธนาคาร)",
            color=discord.Color.gold(),
        )
        for key, p in PACKAGES.items():
            count = stock.get(key, 0)
            stock_text = f"เหลือ {count}" if count > 0 else "❌ หมด"
            embed.add_field(name=p["label"], value=f"{p['price']} บาท ({stock_text})", inline=True)

        await interaction.followup.send(embed=embed, view=BuyPackageView(stock), ephemeral=True)

    @discord.ui.button(label="📊 ดูสถานะ", style=discord.ButtonStyle.secondary, custom_id="panel_status", row=1)
    async def view_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = await build_status_embed(interaction.user)
        await interaction.response.send_message(embed=embed, view=StatusDetailView(), ephemeral=True)


class StatusDetailView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="📋 ดูรายละเอียดเพิ่ม", style=discord.ButtonStyle.secondary, custom_id="status_more_details")
    async def more_details(self, interaction: discord.Interaction, button: discord.ui.Button):
        rules_text = await get_rules_text()
        embed = discord.Embed(
            title="📋 กฎ/เงื่อนไขการใช้งาน",
            description=rules_text,
            color=discord.Color.from_str("#C0C0C0"),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


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


def build_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="⚡ ZENITH SOUL HUB",
        description="**Premium Key Script — Zenith**",
        color=discord.Color.from_rgb(20, 20, 20),
    )
    embed.set_image(url="attachment://banner.png")
    return embed


async def ensure_panel_posted():
    """
    Runs on bot startup. Looks for a channel named AUTO_PANEL_CHANNEL_NAME in every
    guild the bot is in. If the bot's own panel message already exists there, leave
    it (buttons are already persistent via add_view). If not, post a fresh one.
    """
    for guild in bot.guilds:
        channel = find_premium_channel(guild)
        if not channel:
            print(f"[AutoPanel] No channel named '{AUTO_PANEL_CHANNEL_NAME}' in {guild.name}")
            continue

        found_existing = False
        async for msg in channel.history(limit=50):
            if msg.author.id == bot.user.id and msg.components:
                found_existing = True
                break

        if found_existing:
            print(f"[AutoPanel] Panel already exists in #{channel.name}, leaving as is")
            continue

        try:
            embed = build_panel_embed()
            await channel.send(embed=embed, view=PanelView())
            print(f"[AutoPanel] Posted new panel in #{channel.name}")
        except Exception as e:
            print(f"[AutoPanel] Failed to post panel: {e}")


@bot.tree.command(name="setrules", description="[Admin only] ตั้ง/แก้ข้อความกฎ-เงื่อนไขการใช้งาน (โชว์ในปุ่มดูรายละเอียดเพิ่ม)")
@app_commands.describe(text="ข้อความกฎ/เงื่อนไขที่ต้องการให้แสดง")
async def setrules(interaction: discord.Interaction, text: str):
    if interaction.user.id not in ADMIN_IDS:
        await interaction.response.send_message(
            "You don't have permission to use this command.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    ok = await set_rules_text(text)
    if ok:
        await interaction.followup.send("✅ อัปเดตข้อความกฎ/เงื่อนไขแล้ว", ephemeral=True)
    else:
        await interaction.followup.send("❌ อัปเดตไม่สำเร็จ ลองใหม่อีกครั้ง", ephemeral=True)


@bot.tree.command(name="showrules", description="[Admin only] ดูข้อความกฎ/เงื่อนไขปัจจุบัน")
async def showrules(interaction: discord.Interaction):
    if interaction.user.id not in ADMIN_IDS:
        await interaction.response.send_message(
            "You don't have permission to use this command.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    text = await get_rules_text()
    embed = discord.Embed(title="📋 กฎ/เงื่อนไขปัจจุบัน", description=text, color=discord.Color.from_str("#C0C0C0"))
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="showpanel", description="[Admin only] สั่งให้ panel ขึ้นในห้อง premium ทันที ไม่ต้องรอ restart บอท")
async def showpanel(interaction: discord.Interaction):
    if interaction.user.id not in ADMIN_IDS:
        await interaction.response.send_message(
            "You don't have permission to use this command.", ephemeral=True
        )
        return

    channel = find_premium_channel(interaction.guild)
    if not channel:
        await interaction.response.send_message(
            f"❌ ไม่พบห้องชื่อ '{AUTO_PANEL_CHANNEL_NAME}' ในเซิร์ฟเวอร์", ephemeral=True
        )
        return

    embed = build_panel_embed()
    await channel.send(embed=embed, view=PanelView())
    await interaction.response.send_message(
        f"✅ ส่ง panel ไปที่ {channel.mention} แล้ว", ephemeral=True
    )


@bot.tree.command(name="panel", description="Show the Zenith Soul HUB key panel")
@is_admin()
async def panel(interaction: discord.Interaction):
    embed = build_panel_embed()
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


@bot.tree.command(name="testbuy", description="[Admin only] จำลองการซื้อ Key แบบเต็มระบบ ไม่ตัดเงินจริง")
async def testbuy(interaction: discord.Interaction):
    if interaction.user.id not in ADMIN_IDS:
        await interaction.response.send_message(
            "You don't have permission to use this command.", ephemeral=True
        )
        return

    await interaction.response.send_message(
        "🧪 โหมดทดสอบ — เลือกแพ็คเกจ แล้วใส่ข้อความอะไรก็ได้ในช่องซอง (ไม่เช็คจริง)",
        view=TestBuyPackageView(),
        ephemeral=True,
    )


@bot.tree.command(name="เทสคน", description="[Admin only] จำลองคนเข้าเซิร์ฟเวอร์ใหม่ เพื่อทดสอบข้อความต้อนรับ")
async def test_member_join(interaction: discord.Interaction):
    if interaction.user.id not in ADMIN_IDS:
        await interaction.response.send_message(
            "You don't have permission to use this command.", ephemeral=True
        )
        return

    # Simulate on_member_join using the admin's own member object
    await on_member_join(interaction.user)

    await interaction.response.send_message(
        "🧪 จำลองส่งข้อความต้อนรับแล้ว เช็คในห้อง welce ดูครับ",
        ephemeral=True,
    )


def find_premium_channel(guild: discord.Guild):
    """Find the premium/panel channel by matching the end of its name (ignores emoji prefix)."""
    for ch in guild.text_channels:
        if ch.name.lower().endswith(AUTO_PANEL_CHANNEL_NAME.lower()):
            return ch
    return None


def find_welcome_channel(guild: discord.Guild):
    """Find the welcome channel by matching the end of its name (ignores emoji prefix)."""
    for ch in guild.text_channels:
        if ch.name.lower().endswith(WELCOME_CHANNEL_NAME.lower()):
            return ch
    return None


@bot.event
async def on_member_join(member: discord.Member):
    print(f"[Welcome] on_member_join fired for {member} in guild {member.guild.id}")

    channel = find_welcome_channel(member.guild)
    if not channel:
        print(f"[Welcome] No channel matching '{WELCOME_CHANNEL_NAME}' found in {member.guild.name}")
        return

    embed = discord.Embed(
        title="👋 Welcome to Zenith Hub",
        description=f"ยินดีต้อนรับ **{member.display_name}** เข้าสู่ Zenith Soul HUB!",
        color=discord.Color.blurple(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"สมาชิกคนที่ {member.guild.member_count}")

    try:
        await channel.send(embed=embed)
        print(f"[Welcome] Sent welcome message for {member}")
    except Exception as e:
        print(f"[Welcome] failed to send: {e}")


async def build_status_embed(member: discord.Member) -> discord.Embed:
    purchase = purchases_data.get(str(member.id))

    embed = discord.Embed(title="📊 สถานะของคุณ", color=discord.Color.from_str("#C0C0C0"))
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ชื่อผู้ใช้", value=member.mention, inline=False)

    premium_role = discord.utils.get(member.guild.roles, name=PREMIUM_ROLE_NAME)
    has_premium = premium_role in member.roles if premium_role else False
    embed.add_field(name="ยศ", value="💎 Premium" if has_premium else "ไม่มี", inline=True)

    joined_at = member.joined_at
    if joined_at:
        days_in_server = (datetime.datetime.now(datetime.timezone.utc) - joined_at).days
        embed.add_field(
            name="เข้าเซิร์ฟเวอร์เมื่อ",
            value=f"{joined_at.strftime('%Y-%m-%d')} ({days_in_server} วันที่แล้ว)",
            inline=True,
        )

    if not purchase:
        embed.add_field(name="Key / วันหมดอายุ / จำนวนที่เติม", value="ยังไม่มีการซื้อคีย์", inline=False)
        return embed

    expired = is_expired(purchase)
    if purchase["expires_at"] is None:
        expire_text = "ถาวร"
    else:
        expire_dt = datetime.datetime.fromisoformat(purchase["expires_at"])
        expire_text = expire_dt.strftime("%Y-%m-%d %H:%M UTC")
        if expired:
            expire_text += " (หมดอายุแล้ว)"

    embed.add_field(
        name="Key / วันหมดอายุ / จำนวนที่เติม",
        value=(
            f"🔑 `{purchase['key']}`\n"
            f"📦 แพ็คเกจ: {purchase['package_label']}\n"
            f"⏰ หมดอายุ: {expire_text}\n"
            f"🔁 เติมแล้ว: {purchase.get('purchase_count', 1)} ครั้ง"
        ),
        inline=False,
    )

    return embed




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

    await ensure_panel_posted()

    print(f"Bot is online as {bot.user}")


bot.run(DISCORD_BOT_TOKEN)
