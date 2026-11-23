import io
import os
import re
import asyncio
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

# ==================== 設定 ====================
CONFIG = {
    "TOKEN": "",                    # 空のまま。環境変数 DISCORD_TOKEN を優先
    "GUILD_ID": 1574041160786386944,
    "STAFF_ROLE_ID": 1574279867996831784,
    "LOG_CHANNEL_ID": 1574476876804792352,
    "CATEGORY_ID": 1574334716461449216,

    "COLOR": 0x8E44AD,
    "PANEL_TITLE": "【📩お問い合わせ窓口】",
    "BUTTON_CREATE": "Create ticket",
    "BUTTON_CLOSE": "チケットを閉じる",
    "FOOTER": "",
    "TICKET_NAME": "ticket-{num:04d}-{name}",
    "MAX_OPEN": 1,
    "PING_STAFF": True,
    "DELETE_ON_CLOSE": True,
    "CLOSE_DELAY": 5,
}
# ==============================================

TOKEN = os.environ.get("DISCORD_TOKEN") or CONFIG["TOKEN"]
TOPIC_PREFIX = "ticket-owner:"

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def build_panel_embed() -> discord.Embed:
    e = discord.Embed(
        title=CONFIG["PANEL_TITLE"],
        description=(
            "運営への相談・報告・質問がある場合は、下の「Create ticket」ボタンを押してください。\n"
            "あなたと運営だけが見られる専用チャンネルが作成されます。"
        ),
        color=CONFIG["COLOR"],
    )
    e.add_field(
        name="▼ このような時にご利用ください",
        value=(
            "・ルール違反・迷惑行為・荒らしの報告\n"
            "・サーバーのルールやシステムに関する質問\n"
            "・運営への要望・不具合の連絡\n"
            "・その他、他のメンバーに見られたくない相談"
        ),
        inline=False,
    )
    e.add_field(
        name="⚠️注意事項",
        value=(
            "・違反報告の際は、可能であればスクリーンショットや録画を添付してください。\n"
            "・用件が終わりましたら、運営側でチケットを閉じます。\n"
            "・いたずらにチケットを発行するのは禁止です。"
        ),
        inline=False,
    )
    if CONFIG["FOOTER"]:
        e.set_footer(text=CONFIG["FOOTER"])
    e.timestamp = datetime.now(timezone.utc)
    return e


def next_number(guild: discord.Guild) -> int:
    nums = []
    for c in guild.text_channels:
        m = re.match(r"ticket-(\d+)-", c.name)
        if m:
            nums.append(int(m.group(1)))
    return max(nums, default=0) + 1


def is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.manage_guild:
        return True
    return any(r.id == CONFIG["STAFF_ROLE_ID"] for r in member.roles)


def get_open_tickets(guild: discord.Guild, user_id: int) -> list[discord.TextChannel]:
    topic = f"{TOPIC_PREFIX}{user_id}"
    return [c for c in guild.text_channels if c.topic == topic]


def owner_id_of(channel: discord.TextChannel) -> int | None:
    if channel.topic and channel.topic.startswith(TOPIC_PREFIX):
        try:
            return int(channel.topic.split(":")[1])
        except (IndexError, ValueError):
            return None
    return None


async def close_ticket(interaction: discord.Interaction):
    channel = interaction.channel
    owner = owner_id_of(channel)

    if not is_staff(interaction.user) and interaction.user.id != owner:
        await interaction.response.send_message(
            "チケットを閉じられるのは作成者かスタッフだけです。", ephemeral=True
        )
        return

    await interaction.response.send_message("チケットを閉じています…")

    lines = []
    async for msg in channel.history(limit=None, oldest_first=True):
        body = msg.content or ""
        if msg.embeds:
            body += " [embed]"
        if msg.attachments:
            body += " " + " ".join(a.url for a in msg.attachments)
        lines.append(f"[{msg.created_at:%Y-%m-%d %H:%M:%S}] {msg.author}: {body}")

    log = channel.guild.get_channel(CONFIG["LOG_CHANNEL_ID"])
    if log:
        e = discord.Embed(
            title="チケットを閉じました",
            color=CONFIG["COLOR"],
            description=(
                f"チャンネル: #{channel.name}\n"
                f"実行者: {interaction.user.mention}\n"
                f"作成者: {f'<@{owner}>' if owner else '不明'}"
            ),
        )
        e.timestamp = datetime.now(timezone.utc)
        try:
            await log.send(
                embed=e,
                file=discord.File(
                    io.BytesIO("\n".join(lines).encode()), filename=f"{channel.name}.txt"
                ),
            )
        except discord.HTTPException:
            pass

    if CONFIG["DELETE_ON_CLOSE"]:
        await asyncio.sleep(CONFIG["CLOSE_DELAY"])
        try:
            await channel.delete()
        except discord.NotFound:
            pass


class TicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label=CONFIG["BUTTON_CLOSE"], emoji="🔒",
        style=discord.ButtonStyle.danger, custom_id="ticket:close",
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await close_ticket(interaction)


class TicketPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label=CONFIG["BUTTON_CREATE"], emoji="📩",
        style=discord.ButtonStyle.primary, custom_id="ticket:create",
    )
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild

        opened = get_open_tickets(guild, interaction.user.id)
        if len(opened) >= CONFIG["MAX_OPEN"]:
            await interaction.response.send_message(
                f"すでにチケットがあります: {opened[0].mention}", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        staff = guild.get_role(CONFIG["STAFF_ROLE_ID"])
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True,
                attach_files=True, read_message_history=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True,
                manage_channels=True, read_message_history=True,
            ),
        }
        if staff:
            overwrites[staff] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True,
                read_message_history=True, attach_files=True,
            )

        category = guild.get_channel(CONFIG["CATEGORY_ID"])
        if not isinstance(category, discord.CategoryChannel):
            category = None

        safe_name = "".join(
            c for c in interaction.user.name.lower() if c.isalnum() or c in "-_"
        ) or "user"
        name = CONFIG["TICKET_NAME"].format(num=next_number(guild), name=safe_name)[:95]

        try:
            channel = await guild.create_text_channel(
                name=name, category=category,
                overwrites=overwrites, topic=f"{TOPIC_PREFIX}{interaction.user.id}",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "チャンネルを作成できませんでした。ボットのロール位置か「チャンネル管理」権限を確認してください。",
                ephemeral=True,
            )
            return

        e = discord.Embed(
            title="お問い合わせ",
            description=(
                f"{interaction.user.mention} チケットを作成しました。\n"
                "用件をこのチャンネルに書き込んでください。スクリーンショットの添付も可能です。"
            ),
            color=CONFIG["COLOR"],
        )
        ping = interaction.user.mention
        if CONFIG["PING_STAFF"] and staff:
            ping += f" {staff.mention}"
        await channel.send(content=ping, embed=e, view=TicketControls())
        await interaction.followup.send(
            f"チケットを作成しました: {channel.mention}", ephemeral=True
        )


@bot.tree.command(name="panel", description="チケットパネルをこのチャンネルに設置します")
@app_commands.default_permissions(manage_guild=True)
async def panel(interaction: discord.Interaction):
    await interaction.channel.send(embed=build_panel_embed(), view=TicketPanel())
    await interaction.response.send_message("パネルを設置しました。", ephemeral=True)


@bot.tree.command(name="close", description="このチケットを閉じます")
async def close_cmd(interaction: discord.Interaction):
    if not owner_id_of(interaction.channel):
        await interaction.response.send_message(
            "ここはチケットチャンネルではありません。", ephemeral=True
        )
        return
    await close_ticket(interaction)


@bot.tree.command(name="add", description="メンバーをこのチケットに追加します")
@app_commands.describe(user="追加するメンバー")
async def add_cmd(interaction: discord.Interaction, user: discord.Member):
    if not is_staff(interaction.user):
        await interaction.response.send_message("スタッフ専用です。", ephemeral=True)
        return
    await interaction.channel.set_permissions(
        user, view_channel=True, send_messages=True,
        read_message_history=True, attach_files=True,
    )
    await interaction.response.send_message(f"{user.mention} を追加しました。")


@bot.tree.command(name="remove", description="メンバーをこのチケットから外します")
@app_commands.describe(user="外すメンバー")
async def remove_cmd(interaction: discord.Interaction, user: discord.Member):
    if not is_staff(interaction.user):
        await interaction.response.send_message("スタッフ専用です。", ephemeral=True)
        return
    await interaction.channel.set_permissions(user, overwrite=None)
    await interaction.response.send_message(f"{user.mention} を外しました。")


@bot.event
async def on_ready():
    bot.add_view(TicketPanel())
    bot.add_view(TicketControls())
    guild = discord.Object(CONFIG["GUILD_ID"])
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    print(f"ログイン: {bot.user}")


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN が設定されていません。")
    bot.run(TOKEN)