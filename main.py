import os
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from db import Database

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
PT_CHANNEL_ID = int(os.getenv("PT_CHANNEL_ID", "0") or 0)
STEROID_CHANNEL_ID = int(os.getenv("STEROID_CHANNEL_ID", "0") or 0)

PT_RATE = 50_000_000
STEROID_RATE = 20_000_000

db = Database(DATABASE_URL)


def money(value: int) -> str:
    return f"{value:,}원"


def kind_name(kind: str) -> str:
    return "PT 골든벨" if kind == "pt" else "스테로이드 골든벨"


def rate_for(kind: str) -> int:
    return PT_RATE if kind == "pt" else STEROID_RATE


def color_for(kind: str) -> int:
    return 0x3498DB if kind == "pt" else 0xE74C3C


def is_manager(member: discord.Member) -> bool:
    perms = member.guild_permissions
    return perms.administrator or perms.manage_guild


async def settlement_lines(ticket_id: int, kind: str) -> list[str]:
    rows = await db.get_ticket_totals_by_user(ticket_id)
    rate = rate_for(kind)
    result = []
    for row in rows:
        account = await db.get_account(row["user_id"]) or "미등록"
        amount = row["count"] * rate
        result.append(
            f"<@{row['user_id']}> | `{account}` | **{row['count']}회** | **{money(amount)}**"
        )
    return result


async def build_ticket_embed(ticket: dict, final: bool = False) -> discord.Embed:
    current = ticket["current_count"]
    target = ticket["target_count"]
    remaining = max(target - current, 0)
    rate = rate_for(ticket["kind"])

    embed = discord.Embed(
        title=f"{'✅ 마감' if final else '🔔 진행 중'} · {kind_name(ticket['kind'])}",
        color=0x2ECC71 if final else color_for(ticket["kind"]),
    )
    embed.add_field(name="🎫 티켓번호", value=f"**#{ticket['id']}**", inline=False)
    embed.add_field(name="목표 횟수", value=f"**{target}회**", inline=True)
    embed.add_field(name="현재 횟수", value=f"**{current}회**", inline=True)
    embed.add_field(name="남은 횟수", value=f"**{remaining}회**", inline=True)
    embed.add_field(
        name="현재 총 정산금액" if not final else "총 정산금액",
        value=f"**{money(current * rate)}**",
        inline=False,
    )

    lines = await settlement_lines(ticket["id"], ticket["kind"])
    if lines:
        text = "\n".join(lines)
        if len(text) > 3900:
            text = text[:3850] + "\n…일부 생략"
    else:
        text = "아직 등록된 기록이 없습니다."

    embed.add_field(
        name="정산 현황" if not final else "최종 정산",
        value=text,
        inline=False,
    )
    if final:
        footer_text = f"티켓번호 #{ticket['id']} · 닉네임(멘션) | 계좌번호 | 횟수 | 정산금액"
    else:
        rule_text = "10분 = +1회 · 20분 = +1회" if ticket["kind"] == "pt" else "1회 등록 = +1회"
        footer_text = f"티켓번호 #{ticket['id']} · {rule_text}"

    embed.set_footer(text=footer_text)
    return embed


class GoldenBellView(discord.ui.View):
    def __init__(self, ticket_id: int, kind: str, disabled: bool = False):
        super().__init__(timeout=None)

        if kind == "pt":
            self.add_item(RecordButton(ticket_id, kind, "10분", 1, disabled))
            self.add_item(RecordButton(ticket_id, kind, "20분", 1, disabled))
        else:
            self.add_item(RecordButton(ticket_id, kind, "1회 등록", 1, disabled))

        self.add_item(UndoButton(ticket_id, kind, disabled))
        self.add_item(CloseButton(ticket_id, kind, disabled))


class RecordButton(discord.ui.Button):
    def __init__(self, ticket_id, kind, label, delta, disabled=False):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary,
            custom_id=f"gb:{ticket_id}:{kind}:add:{delta}",
            disabled=disabled,
        )
        self.ticket_id = ticket_id
        self.kind = kind
        self.delta = delta

    async def callback(self, interaction: discord.Interaction):
        account = await db.get_account(interaction.user.id)
        if not account:
            await interaction.response.send_message(
                "❌ 등록된 계좌번호가 없습니다. 관리자에게 `/계좌등록`을 요청해주세요.",
                ephemeral=True,
            )
            return

        ticket, status = await db.add_entry(
            self.ticket_id,
            interaction.user.id,
            self.delta,
            self.label,
        )

        if status == "closed":
            await interaction.response.send_message("이미 마감된 골든벨입니다.", ephemeral=True)
            return

        if status == "over":
            remaining = ticket["target_count"] - ticket["current_count"]
            await interaction.response.send_message(
                f"❌ 남은 횟수는 **{remaining}회**입니다. 목표 횟수를 초과할 수 없습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        if ticket["current_count"] >= ticket["target_count"]:
            ticket = await db.close_ticket(self.ticket_id)
            await interaction.message.edit(
                embed=await build_ticket_embed(ticket, final=True),
                view=GoldenBellView(self.ticket_id, self.kind, disabled=True),
            )
        else:
            await interaction.message.edit(
                embed=await build_ticket_embed(ticket),
                view=GoldenBellView(self.ticket_id, self.kind),
            )


class UndoButton(discord.ui.Button):
    def __init__(self, ticket_id, kind, disabled=False):
        super().__init__(
            label="되돌리기",
            style=discord.ButtonStyle.secondary,
            custom_id=f"gb:{ticket_id}:{kind}:undo",
            disabled=disabled,
        )
        self.ticket_id = ticket_id
        self.kind = kind

    async def callback(self, interaction: discord.Interaction):
        ticket = await db.undo_last_entry(self.ticket_id, interaction.user.id)
        if not ticket:
            await interaction.response.send_message(
                "되돌릴 수 있는 본인 기록이 없습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        await interaction.message.edit(
            embed=await build_ticket_embed(ticket),
            view=GoldenBellView(self.ticket_id, self.kind),
        )


class CloseButton(discord.ui.Button):
    def __init__(self, ticket_id, kind, disabled=False):
        super().__init__(
            label="마감",
            style=discord.ButtonStyle.danger,
            custom_id=f"gb:{ticket_id}:{kind}:close",
            disabled=disabled,
        )
        self.ticket_id = ticket_id
        self.kind = kind

    async def callback(self, interaction: discord.Interaction):
        ticket = await db.get_ticket(self.ticket_id)
        if not ticket or ticket["status"] != "open":
            await interaction.response.send_message("이미 마감된 골든벨입니다.", ephemeral=True)
            return

        if interaction.user.id != ticket["creator_id"] and not is_manager(interaction.user):
            await interaction.response.send_message(
                "❌ 티켓 생성자 또는 관리자만 마감할 수 있습니다.",
                ephemeral=True,
            )
            return

        ticket = await db.close_ticket(self.ticket_id)
        await interaction.response.defer()
        await interaction.message.edit(
            embed=await build_ticket_embed(ticket, final=True),
            view=GoldenBellView(self.ticket_id, self.kind, disabled=True),
        )


class GoldenBellBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL 환경변수가 없습니다.")

        await db.connect()

        for ticket in await db.get_open_tickets():
            self.add_view(
                GoldenBellView(ticket["id"], ticket["kind"]),
                message_id=ticket["message_id"],
            )

        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def close(self):
        await db.close()
        await super().close()


bot = GoldenBellBot()


async def create_ticket(interaction: discord.Interaction, kind: str, count: int):
    if count < 1:
        await interaction.response.send_message("횟수는 1 이상이어야 합니다.", ephemeral=True)
        return

    expected_channel = PT_CHANNEL_ID if kind == "pt" else STEROID_CHANNEL_ID
    if expected_channel and interaction.channel_id != expected_channel:
        await interaction.response.send_message(
            f"❌ {kind_name(kind)} 지정 채널에서만 사용할 수 있습니다.",
            ephemeral=True,
        )
        return

    ticket = await db.create_ticket(
        kind=kind,
        guild_id=interaction.guild_id,
        channel_id=interaction.channel_id,
        creator_id=interaction.user.id,
        target_count=count,
    )

    await interaction.response.send_message(
        embed=await build_ticket_embed(ticket),
        view=GoldenBellView(ticket["id"], kind),
    )

    message = await interaction.original_response()
    await db.set_ticket_message(ticket["id"], message.id)


@bot.tree.command(name="피티", description="PT 골든벨 티켓을 생성합니다.")
@app_commands.describe(횟수="이번 PT 골든벨의 총 횟수")
async def pt(interaction: discord.Interaction, 횟수: app_commands.Range[int, 1, 100000]):
    await create_ticket(interaction, "pt", 횟수)


@bot.tree.command(name="스테로이드", description="스테로이드 골든벨 티켓을 생성합니다.")
@app_commands.describe(횟수="이번 스테로이드 골든벨의 총 횟수")
async def steroid(interaction: discord.Interaction, 횟수: app_commands.Range[int, 1, 100000]):
    await create_ticket(interaction, "steroid", 횟수)


@bot.tree.command(name="계좌등록", description="유저의 계좌번호를 등록하거나 수정합니다.")
@app_commands.describe(유저="계좌를 등록할 디스코드 유저", 계좌번호="FiveM 계좌번호")
async def account_register(
    interaction: discord.Interaction,
    유저: discord.Member,
    계좌번호: str,
):
    if not is_manager(interaction.user):
        await interaction.response.send_message(
            "❌ 관리자만 다른 유저의 계좌번호를 등록할 수 있습니다.",
            ephemeral=True,
        )
        return

    account = 계좌번호.strip()
    if not account:
        await interaction.response.send_message("계좌번호를 입력해주세요.", ephemeral=True)
        return

    await db.set_account(유저.id, account)
    await interaction.response.send_message(
        f"✅ {유저.mention} 계좌번호를 `{account}`로 등록했습니다.",
        ephemeral=True,
    )


@bot.tree.command(name="계좌확인", description="등록된 계좌번호를 확인합니다.")
@app_commands.describe(유저="확인할 디스코드 유저")
async def account_check(
    interaction: discord.Interaction,
    유저: discord.Member | None = None,
):
    target = 유저 or interaction.user

    # 본인 계좌는 누구나 확인, 다른 사람 계좌는 관리자만
    if target.id != interaction.user.id and not is_manager(interaction.user):
        await interaction.response.send_message(
            "❌ 다른 사람의 계좌번호는 관리자만 확인할 수 있습니다.",
            ephemeral=True,
        )
        return

    account = await db.get_account(target.id)
    if not account:
        await interaction.response.send_message(
            f"❌ {target.mention}에게 등록된 계좌번호가 없습니다.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        f"🏦 {target.mention} 계좌번호: `{account}`",
        ephemeral=True,
    )


@bot.tree.command(name="수기등록", description="진행 중인 골든벨 티켓에 기존 횟수를 수기로 추가합니다.")
@app_commands.describe(
    티켓번호="골든벨 티켓 번호",
    유저="횟수를 추가할 디스코드 유저",
    횟수="추가할 횟수",
)
async def manual_register(
    interaction: discord.Interaction,
    티켓번호: int,
    유저: discord.Member,
    횟수: app_commands.Range[int, 1, 100000],
):
    if not is_manager(interaction.user):
        await interaction.response.send_message("❌ 관리자만 사용할 수 있습니다.", ephemeral=True)
        return

    account = await db.get_account(유저.id)
    if not account:
        await interaction.response.send_message(
            f"❌ {유저.mention}의 계좌번호가 등록되어 있지 않습니다. 먼저 `/계좌등록`을 해주세요.",
            ephemeral=True,
        )
        return

    ticket, status = await db.manual_add(티켓번호, 유저.id, 횟수)

    if status == "closed":
        await interaction.response.send_message("❌ 존재하지 않거나 이미 마감된 티켓입니다.", ephemeral=True)
        return
    if status == "over":
        remaining = ticket["target_count"] - ticket["current_count"]
        await interaction.response.send_message(
            f"❌ 남은 횟수는 **{remaining}회**입니다. 목표 횟수를 초과할 수 없습니다.",
            ephemeral=True,
        )
        return

    if ticket["current_count"] >= ticket["target_count"]:
        ticket = await db.close_ticket(티켓번호)

    # 원래 티켓 메시지도 즉시 갱신
    try:
        channel = bot.get_channel(ticket["channel_id"]) or await bot.fetch_channel(ticket["channel_id"])
        message = await channel.fetch_message(ticket["message_id"])
        final = ticket["status"] == "closed"
        await message.edit(
            embed=await build_ticket_embed(ticket, final=final),
            view=GoldenBellView(티켓번호, ticket["kind"], disabled=final),
        )
    except Exception as e:
        print(f"수기등록 티켓 메시지 갱신 실패: {e}")

    await interaction.response.send_message(
        f"✅ {유저.mention}에게 **{횟수}회**를 수기 등록했습니다.\n"
        f"현재 진행: **{ticket['current_count']}/{ticket['target_count']}회**"
        + ("\n🔒 목표 횟수에 도달하여 자동 마감되었습니다." if ticket["status"] == "closed" else ""),
        ephemeral=True,
    )


@bot.tree.command(name="수기차감", description="진행 중인 골든벨 티켓에서 유저의 횟수를 수기로 차감합니다.")
@app_commands.describe(
    티켓번호="골든벨 티켓 번호",
    유저="횟수를 차감할 디스코드 유저",
    횟수="차감할 횟수",
)
async def manual_subtract(
    interaction: discord.Interaction,
    티켓번호: int,
    유저: discord.Member,
    횟수: app_commands.Range[int, 1, 100000],
):
    if not is_manager(interaction.user):
        await interaction.response.send_message("❌ 관리자만 사용할 수 있습니다.", ephemeral=True)
        return

    ticket, status = await db.manual_subtract(티켓번호, 유저.id, 횟수)

    if status == "closed":
        await interaction.response.send_message("❌ 존재하지 않거나 이미 마감된 티켓입니다.", ephemeral=True)
        return
    if status == "not_enough":
        totals = await db.get_ticket_totals_by_user(티켓번호)
        current_user_count = next((r["count"] for r in totals if r["user_id"] == 유저.id), 0)
        await interaction.response.send_message(
            f"❌ {유저.mention}의 현재 기록은 **{current_user_count}회**라서 **{횟수}회**를 차감할 수 없습니다.",
            ephemeral=True,
        )
        return

    try:
        channel = bot.get_channel(ticket["channel_id"]) or await bot.fetch_channel(ticket["channel_id"])
        message = await channel.fetch_message(ticket["message_id"])
        await message.edit(
            embed=await build_ticket_embed(ticket),
            view=GoldenBellView(티켓번호, ticket["kind"]),
        )
    except Exception as e:
        print(f"수기차감 티켓 메시지 갱신 실패: {e}")

    await interaction.response.send_message(
        f"✅ {유저.mention}에게서 **{횟수}회**를 차감했습니다.\n"
        f"현재 진행: **{ticket['current_count']}/{ticket['target_count']}회**",
        ephemeral=True,
    )


@bot.event
async def on_ready():
    print(f"로그인 완료: {bot.user} ({bot.user.id})")


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN 환경변수가 없습니다.")
    bot.run(TOKEN)
