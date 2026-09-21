from __future__ import annotations

import contextlib
import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from .database import DatabaseRepository, DatabaseRepositoryError, LinkConflictError
from .engagement import EngagementService
from .scoring import DEFAULT_CONFIG
from .settings import Settings
from .twitter_client import TwitterApiClient, TwitterApiError
from .utils import parse_period, utc_now

# The same windows the dashboard offers, so every command shows one consistent list.
PERIOD_CHOICES = [
    app_commands.Choice(name="Last 24 hours", value="24h"),
    app_commands.Choice(name="Last 7 days", value="7d"),
    app_commands.Choice(name="Last 30 days", value="30d"),
    app_commands.Choice(name="Last 60 days", value="60d"),
    app_commands.Choice(name="Last 90 days", value="90d"),
    app_commands.Choice(name="Last 6 months", value="180d"),
    app_commands.Choice(name="Last 12 months", value="365d"),
]

LOGGER = logging.getLogger(__name__)


def admin_only() -> Any:
    async def predicate(interaction: discord.Interaction) -> bool:
        settings: Settings = interaction.client.settings  # type: ignore[attr-defined]
        member = interaction.user
        if isinstance(member, discord.Member):
            if member.guild_permissions.administrator:
                return True
            if settings.admin_role_ids.intersection(role.id for role in member.roles):
                return True
        raise app_commands.CheckFailure("This command is restricted to engagement admins")

    return app_commands.check(predicate)


def score_label(value: float) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".")


class ResetConfirmation(discord.ui.View):
    def __init__(
        self,
        *,
        bot: EngagementBot,
        requester_id: int,
    ) -> None:
        super().__init__(timeout=60)
        self.bot = bot
        self.requester_id = requester_id
        self.completed = False
        self.message: discord.Message | None = None

    async def on_timeout(self) -> None:
        if self.completed:
            return
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message is not None:
            with contextlib.suppress(discord.HTTPException):
                await self.message.edit(
                    content="Leaderboard reset confirmation expired.", view=self
                )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Only the admin who opened this confirmation can use it.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Reset leaderboard", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button[Any]
    ) -> None:
        if self.completed:
            return
        self.completed = True
        await interaction.response.defer()
        old_cycle, new_cycle, snapshots = await self.bot.repository.reset_leaderboard(
            str(interaction.user.id)
        )
        await self.bot.repository.append_audit(
            event_type="leaderboard_reset",
            actor_discord_id=str(interaction.user.id),
            old_value=old_cycle,
            new_value=new_cycle,
            details={"snapshots": snapshots},
        )
        await self.bot.audit(
            "Leaderboard reset",
            f"<@{interaction.user.id}> reset `{old_cycle}`; {snapshots} member snapshots saved. "
            f"New cycle: `{new_cycle}`.",
        )
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await interaction.edit_original_response(
            content=f"Leaderboard reset complete. Saved {snapshots} snapshots.", view=self
        )
        if interaction.channel is not None:
            await interaction.channel.send(
                "🏁 A new Major's Lair engagement cycle has begun. Scores are reset—"
                "let's help Major grow with thoughtful engagement!"
            )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button[Any]
    ) -> None:
        self.completed = True
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await interaction.response.edit_message(content="Leaderboard reset cancelled.", view=self)
        self.stop()


class EngagementCog(commands.Cog):
    def __init__(self, bot: EngagementBot) -> None:
        self.bot = bot

    @app_commands.command(
        name="link-twitter", description="Link your Discord account to one X account"
    )
    @app_commands.describe(handle="Your X handle, with or without @")
    @app_commands.guild_only()
    async def link_twitter(self, interaction: discord.Interaction, handle: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        old_handle, new_handle, _ = await self.bot.service.link_user(
            discord_user_id=str(interaction.user.id),
            discord_username=str(interaction.user),
            handle=handle,
        )
        if old_handle and old_handle != new_handle:
            message = f"Updated your linked X account from **@{old_handle}** to **@{new_handle}**."
        else:
            message = f"Linked your Discord account to **@{new_handle}**."
        await interaction.followup.send(message, ephemeral=True)
        await self.bot.audit("X link updated", f"<@{interaction.user.id}> → **@{new_handle}**")

    @app_commands.command(name="unlink-twitter", description="Deactivate your linked X account")
    @app_commands.guild_only()
    async def unlink_twitter(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        old_handle = await self.bot.service.unlink_user(discord_user_id=str(interaction.user.id))
        if not old_handle:
            await interaction.followup.send(
                "You do not have an active linked X account.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"Unlinked **@{old_handle}**. Your history remains preserved.", ephemeral=True
        )
        await self.bot.audit(
            "X account unlinked", f"<@{interaction.user.id}> unlinked **@{old_handle}**"
        )

    @app_commands.command(
        name="leaderboard", description="Show the current public engagement leaderboard"
    )
    @app_commands.describe(period="Optional window; default is the whole cycle")
    @app_commands.choices(period=PERIOD_CHOICES)
    @app_commands.guild_only()
    async def leaderboard(
        self, interaction: discord.Interaction, period: str | None = None
    ) -> None:
        await interaction.response.defer(thinking=True)
        window_label = ""
        if period:
            try:
                duration, window_label = parse_period(period)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            config = await self.bot.repository.get_config()
            cycle_id = config.get("current_cycle_id", DEFAULT_CONFIG["current_cycle_id"])
            ranked = await self.bot.repository.leaderboard_window(
                cycle_id=cycle_id, since=utc_now() - duration, limit=500
            )
        else:
            users = await self.bot.repository.list_users(active_only=True)
            linked = [user for user in users if user.twitter_user_id]
            ranked = sorted(linked, key=lambda user: (-user.score, user.discord_username.lower()))
        if not ranked:
            await interaction.followup.send("No linked members yet.")
            return
        lines = [
            f"**{index}.** <@{user.discord_user_id}> · "
            f"**{score_label(user.score)}** pts · `@{user.twitter_handle}`"
            for index, user in enumerate(ranked[:25], start=1)
        ]
        embed = discord.Embed(
            title="Major's Lair · Engagement Leaderboard"
            + (f" · last {window_label}" if window_label else ""),
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        own_rank = next(
            (
                index
                for index, user in enumerate(ranked, start=1)
                if user.discord_user_id == str(interaction.user.id)
            ),
            None,
        )
        if own_rank:
            own = ranked[own_rank - 1]
            embed.set_footer(text=f"Your rank: #{own_rank} · {score_label(own.score)} points")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="my-score", description="Show your score and rank for this cycle")
    @app_commands.guild_only()
    async def my_score(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        users = await self.bot.repository.list_users(active_only=True)
        linked = [user for user in users if user.twitter_user_id]
        ranked = sorted(linked, key=lambda user: (-user.score, user.discord_username.lower()))
        rank = next(
            (
                index
                for index, user in enumerate(ranked, start=1)
                if user.discord_user_id == str(interaction.user.id)
            ),
            None,
        )
        if rank is None:
            await interaction.followup.send(
                "Link an X account first with `/link-twitter`.", ephemeral=True
            )
            return
        user = ranked[rank - 1]
        await interaction.followup.send(
            f"You are **#{rank}** of {len(ranked)} with "
            f"**{score_label(user.score)} points** (`@{user.twitter_handle}`).",
            ephemeral=True,
        )

    async def _history_embed(
        self, discord_user_id: str, *, title: str, limit: int = 10
    ) -> discord.Embed | None:
        config = await self.bot.repository.get_config()
        cycle_id = config.get("current_cycle_id", DEFAULT_CONFIG["current_cycle_id"])
        history = await self.bot.repository.user_history(discord_user_id, cycle_id, limit)
        if not history:
            return None
        embed = discord.Embed(title=title, color=discord.Color.blurple())
        for action in history:
            status = "active" if action.active else "no longer public"
            name = (
                f"{action.action_type.value.title()} · {score_label(action.points)} pts · {status}"
            )
            link = f"[View on X]({action.action_url}) · " if action.action_url else ""
            embed.add_field(
                name=name[:256],
                value=f"{link}{action.reason}"[:1024],
                inline=False,
            )
        return embed

    @app_commands.command(name="my-history", description="Show how your latest actions were scored")
    @app_commands.guild_only()
    async def my_history(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        embed = await self._history_embed(
            str(interaction.user.id), title="Your recent engagement scoring"
        )
        if embed is None:
            await interaction.followup.send(
                "No engagement actions are logged for you in this cycle yet.", ephemeral=True
            )
            return
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="user-history", description="Admin: see how a member's latest actions were scored"
    )
    @app_commands.describe(member="The Discord member to audit", limit="How many actions (max 25)")
    @app_commands.guild_only()
    @admin_only()
    async def user_history(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        limit: app_commands.Range[int, 1, 25] = 15,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        linked = await self.bot.repository.get_user(str(member.id))
        if linked is None:
            await interaction.followup.send(
                f"{member.mention} is not in the registry yet.", ephemeral=True
            )
            return
        header = (
            f"{member.mention} · "
            + (f"`@{linked.twitter_handle}`" if linked.twitter_user_id else "*no X linked*")
            + f" · **{score_label(linked.score)} pts** this cycle"
            + (" · protected" if linked.special_role else "")
            + (f" · X {linked.x_status}" if linked.x_status in {"suspended", "unavailable"} else "")
        )
        embed = await self._history_embed(
            str(member.id), title=f"Engagement history · {linked.discord_username}", limit=limit
        )
        if embed is None:
            await interaction.followup.send(
                f"{header}\nNo engagement actions logged in this cycle.", ephemeral=True
            )
            return
        embed.description = header
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _run_scan(
        self,
        interaction: discord.Interaction,
        period: str | None,
        skip_protected: bool | None = None,
        read_timelines: bool | None = None,
        timeline_depth: int | None = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        if period is None:
            config = await self.bot.repository.get_config()
            period = config.get("default_refresh_period", DEFAULT_CONFIG["default_refresh_period"])
        summary = await self.bot.service.scan(
            period=period,
            actor_discord_id=str(interaction.user.id),
            include_protected=None if skip_protected is None else not skip_protected,
            read_timelines=read_timelines,
            timeline_pages=None if timeline_depth is None else max(1, timeline_depth // 20),
        )
        approximate_cost = summary.tweets_returned * 0.00018
        embed = discord.Embed(
            title="Engagement scan complete",
            description=(
                f"Checked **{summary.source_posts} source posts** for "
                f"**{summary.period_label}** and "
                f"matched **{summary.discovered} current actions**."
            ),
            color=discord.Color.green() if not summary.warnings else discord.Color.orange(),
        )
        embed.add_field(
            name="Matched",
            value=(
                f"Replies: **{summary.replies}**\nQuotes: **{summary.quotes}**\n"
                f"Retweets: **{summary.retweets}**\nOrganic mentions: **{summary.mentions}**"
            ),
        )
        embed.add_field(
            name="Efficiency",
            value=(
                f"API requests: **{summary.api_requests}**\n"
                f"Items returned: **{summary.tweets_returned}**\n"
                f"Approx. upper cost: **${approximate_cost:.4f}**"
            ),
        )
        embed.add_field(
            name="Reconciliation",
            value=(
                f"Changed log entries: **{summary.changed_actions}**\n"
                f"Incomplete capped scopes: **{summary.incomplete_scopes}**"
                + (
                    f"\nProtected members skipped: **{summary.skipped_protected}**"
                    if summary.skipped_protected
                    else ""
                )
                + (
                    f"\nHidden replies via sweep: **{summary.swept_replies}**"
                    if summary.swept_replies
                    else ""
                )
                + (
                    f"\nHidden replies via timelines: **{summary.timeline_replies}** "
                    f"({summary.timeline_members_checked} members read)"
                    if summary.timeline_members_checked
                    else ""
                )
            ),
        )
        if summary.x_checked:
            embed.add_field(
                name="X accounts",
                value=(
                    f"Checked: **{summary.x_checked}**\n"
                    f"Suspended or gone: **{len(summary.x_unavailable)}**\n"
                    f"Renamed (auto-updated): **{len(summary.x_renamed)}**"
                ),
            )
        if summary.x_unavailable:
            lines = [
                f"• <@{item['discord_user_id']}> · `@{item['twitter_handle']}` · {item['status']}"
                for item in summary.x_unavailable[:20]
            ]
            if len(summary.x_unavailable) > 20:
                lines.append(f"…and {len(summary.x_unavailable) - 20} more (see the dashboard).")
            embed.add_field(
                name="Could not verify these X accounts",
                value="\n".join(lines)[:1024],
                inline=False,
            )
        if summary.warnings:
            embed.add_field(
                name="Warnings",
                value="\n".join(f"• {warning}" for warning in summary.warnings)[:1024],
                inline=False,
            )
        await interaction.followup.send(embed=embed)
        await self.bot.audit(
            "Engagement scan",
            f"<@{interaction.user.id}> scanned `{summary.period_label}`: "
            f"{summary.discovered} matched actions, {summary.api_requests} API requests.",
        )

    @app_commands.command(
        name="adjust-points", description="Admin: add or remove points for a member"
    )
    @app_commands.describe(
        member="The member", points="Positive to add, negative to remove", reason="Why"
    )
    @app_commands.guild_only()
    @admin_only()
    async def adjust_points(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        points: float,
        reason: str = "",
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        config = await self.bot.repository.get_config()
        cycle_id = config.get("current_cycle_id", DEFAULT_CONFIG["current_cycle_id"])
        try:
            await self.bot.repository.adjust_points(
                cycle_id=cycle_id,
                discord_user_id=str(member.id),
                points=points,
                reason=reason,
                actor_discord_id=str(interaction.user.id),
            )
        except DatabaseRepositoryError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        updated = await self.bot.repository.get_user(str(member.id))
        await self.bot.repository.append_audit(
            event_type="admin_points_adjusted",
            actor_discord_id=str(interaction.user.id),
            subject_discord_id=str(member.id),
            new_value=f"{points:+g}",
            details={"reason": reason},
        )
        await interaction.followup.send(
            f"{points:+g} points for {member.mention}"
            + (f" ({reason})" if reason else "")
            + f". Now **{score_label(updated.score if updated else 0)} pts**.",
            ephemeral=True,
        )
        await self.bot.audit(
            "Points adjusted",
            f"<@{interaction.user.id}> gave **{points:+g}** to {member.mention}"
            + (f": {reason}" if reason else ""),
        )

    @app_commands.command(
        name="transfer-points", description="Admin: move points from one member to another"
    )
    @app_commands.describe(
        sender="Member losing the points",
        receiver="Member gaining them",
        points="Amount",
        reason="Why",
    )
    @app_commands.guild_only()
    @admin_only()
    async def transfer_points(
        self,
        interaction: discord.Interaction,
        sender: discord.Member,
        receiver: discord.Member,
        points: app_commands.Range[float, 0.01, 100000.0],
        reason: str = "",
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        config = await self.bot.repository.get_config()
        cycle_id = config.get("current_cycle_id", DEFAULT_CONFIG["current_cycle_id"])
        try:
            await self.bot.repository.adjust_points(
                cycle_id=cycle_id,
                discord_user_id=str(sender.id),
                points=points,
                reason=reason,
                actor_discord_id=str(interaction.user.id),
                transfer_to=str(receiver.id),
            )
        except DatabaseRepositoryError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await self.bot.repository.append_audit(
            event_type="admin_points_transferred",
            actor_discord_id=str(interaction.user.id),
            subject_discord_id=str(sender.id),
            new_value=f"-{points:g}",
            details={"reason": reason, "transfer_to": str(receiver.id)},
        )
        await interaction.followup.send(
            f"Moved **{points:g}** points from {sender.mention} to {receiver.mention}"
            + (f" ({reason})" if reason else "")
            + ".",
            ephemeral=True,
        )
        await self.bot.audit(
            "Points transferred",
            f"<@{interaction.user.id}> moved **{points:g}** from {sender.mention} to "
            f"{receiver.mention}" + (f": {reason}" if reason else ""),
        )

    @app_commands.command(
        name="scan-member", description="Admin: deep-check one member's own timeline and score it"
    )
    @app_commands.describe(
        member="The member to scan",
        period="Window to read (default: last 30 days)",
        depth="How many of their latest tweets to read at most (default 500, max 5000)",
    )
    @app_commands.choices(period=PERIOD_CHOICES)
    @app_commands.guild_only()
    @admin_only()
    async def scan_member(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        period: str = "30d",
        depth: app_commands.Range[int, 20, 5000] = 500,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            outcome = await self.bot.service.scan_member(
                discord_user_id=str(member.id),
                period=period,
                actor_discord_id=str(interaction.user.id),
                max_pages=max(1, depth // 20),
            )
        except (ValueError, TwitterApiError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(
            f"Scanned {member.mention} (`@{outcome['twitter_handle']}`) over "
            f"**{outcome['period']}**: read {outcome['tweets_read']} tweets, matched "
            f"**{outcome['matched']}** ({outcome['replies']} replies, {outcome['quotes']} quotes, "
            f"{outcome['mentions']} mentions), {outcome['new_actions']} new. Points "
            f"**{score_label(outcome['points_before'])} → {score_label(outcome['points_after'])}**."
            + ("" if outcome["complete"] else " Timeline page cap reached; older tweets skipped."),
            ephemeral=True,
        )

    @app_commands.command(
        name="check-engagement", description="Scan and score a chosen recent period"
    )
    @app_commands.describe(
        period="Window to scan (default: last 7 days)",
        skip_protected=(
            "Leave special-role members out of scoring and X checks "
            "(default: the skip_protected_members setting)"
        ),
        read_timelines=(
            "Also read every member's own timeline to catch replies X hides "
            "(about 300 credits per member; default: the member_timeline_pages setting)"
        ),
        timeline_depth="With read_timelines: how many latest tweets per member (20-5000)",
    )
    @app_commands.choices(period=PERIOD_CHOICES)
    @app_commands.guild_only()
    @admin_only()
    async def check_engagement(
        self,
        interaction: discord.Interaction,
        period: str = "7d",
        skip_protected: bool | None = None,
        read_timelines: bool | None = None,
        timeline_depth: app_commands.Range[int, 20, 5000] | None = None,
    ) -> None:
        await self._run_scan(interaction, period, skip_protected, read_timelines, timeline_depth)

    @app_commands.command(
        name="refresh-engagement", description="Run the configured short refresh scan"
    )
    @app_commands.guild_only()
    @admin_only()
    async def refresh_engagement(self, interaction: discord.Interaction) -> None:
        await self._run_scan(interaction, None)

    @app_commands.command(
        name="track-post", description="Manually track an important Major/Lair X post"
    )
    @app_commands.describe(url="Full x.com or twitter.com status URL")
    @app_commands.guild_only()
    @admin_only()
    async def track_post(self, interaction: discord.Interaction, url: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        tweet = await self.bot.service.track_post(
            url=url, actor_discord_id=str(interaction.user.id)
        )
        await interaction.followup.send(
            f"Now tracking [this @{tweet.author_handle} post]({tweet.url}).", ephemeral=True
        )
        await self.bot.audit(
            "Post tracked", f"<@{interaction.user.id}> added [post {tweet.tweet_id}]({tweet.url})."
        )

    @app_commands.command(
        name="low-activity-report",
        description="List linked members at or below the score threshold",
    )
    @app_commands.describe(threshold="Optional point threshold; defaults to admin configuration")
    @app_commands.guild_only()
    @admin_only()
    async def low_activity_report(
        self, interaction: discord.Interaction, threshold: float | None = None
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        config = await self.bot.repository.get_config()
        effective = threshold
        if effective is None:
            effective = float(
                config.get("low_activity_threshold", DEFAULT_CONFIG["low_activity_threshold"])
            )
        grace = int(config.get("newcomer_grace_days", DEFAULT_CONFIG["newcomer_grace_days"]))
        users = await self.bot.repository.low_activity(effective, grace_days=grace)
        if users:
            lines = [
                f"<@{user.discord_user_id}> · {score_label(user.score)} pts · "
                + (f"`@{user.twitter_handle}`" if user.twitter_user_id else "*no X linked*")
                + (f" ⚠ X {user.x_status}" if user.x_status in {"suspended", "unavailable"} else "")
                for user in users[:50]
            ]
            suffix = f"\n…and {len(users) - 50} more." if len(users) > 50 else ""
            description = "\n".join(lines) + suffix
        else:
            description = "No active members are at or below this threshold."
        unlinked = sum(1 for user in users if not user.twitter_user_id)
        embed = discord.Embed(
            title=f"Low activity · ≤ {score_label(effective)} points",
            description=description,
            color=discord.Color.orange(),
        )
        embed.set_footer(
            text=(
                f"{len(users)} members · {unlinked} without an X account · "
                "special-role members excluded"
                + (f" · joined < {grace} days ago excluded" if grace > 0 else "")
            )
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        await self.bot.repository.append_audit(
            event_type="low_activity_report",
            actor_discord_id=str(interaction.user.id),
            details={"threshold": effective, "members": len(users)},
        )
        await self.bot.audit(
            "Low-activity report",
            f"<@{interaction.user.id}> generated a report at ≤ {score_label(effective)} points "
            f"({len(users)} members).",
        )

    @app_commands.command(
        name="reset-leaderboard", description="Snapshot and zero all scores after confirmation"
    )
    @app_commands.guild_only()
    @admin_only()
    async def reset_leaderboard(self, interaction: discord.Interaction) -> None:
        view = ResetConfirmation(bot=self.bot, requester_id=interaction.user.id)
        await interaction.response.send_message(
            "This will snapshot every active member, zero all current scores, and start a "
            "new cycle. Historical logs stay intact.",
            view=view,
            ephemeral=True,
        )
        view.message = await interaction.original_response()

    @app_commands.command(
        name="sync-database", description="Ensure database tables and recalculate current scores"
    )
    @app_commands.guild_only()
    @admin_only()
    async def sync_database(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.repository.ensure_schema()
        count = await self.bot.service.rescore_current_cycle()
        await self.bot.repository.append_audit(
            event_type="database_synced",
            actor_discord_id=str(interaction.user.id),
            details={"rescored_actions": count},
        )
        await interaction.followup.send(
            f"Database schema is current; recalculated {count} action rows.", ephemeral=True
        )
        await self.bot.audit(
            "Database synchronized", f"<@{interaction.user.id}> synchronized schema and scores."
        )


class EngagementBot(commands.Bot):
    settings: Settings

    def __init__(
        self,
        *,
        settings: Settings,
        repository: DatabaseRepository,
        twitter: TwitterApiClient,
    ) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.settings = settings
        self.repository = repository
        self.twitter = twitter
        self.service = EngagementService(repository, twitter)

    async def setup_hook(self) -> None:
        await self.twitter.start()
        await self.repository.ensure_schema()
        await self.add_cog(EngagementCog(self))
        self.tree.on_error = self.on_app_command_error
        if self.settings.discord_guild_id:
            guild = discord.Object(id=self.settings.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            LOGGER.info("Synced %s guild commands", len(synced))
        else:
            synced = await self.tree.sync()
            LOGGER.info("Synced %s global commands", len(synced))

    async def close(self) -> None:
        await self.twitter.close()
        await super().close()

    async def on_ready(self) -> None:
        if self.user:
            LOGGER.info("Connected as %s (%s)", self.user, self.user.id)

    async def audit(self, title: str, description: str) -> None:
        channel_id = self.settings.discord_audit_channel_id
        if not channel_id:
            return
        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                LOGGER.exception("Could not fetch audit channel %s", channel_id)
                return
        if not isinstance(channel, discord.abc.Messageable):
            LOGGER.error("Configured audit channel %s is not messageable", channel_id)
            return
        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.dark_teal(),
            timestamp=discord.utils.utcnow(),
        )
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not send to audit channel %s", channel_id)

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        cause: Exception = error
        if isinstance(error, app_commands.CommandInvokeError):
            cause = error.original
        if isinstance(cause, app_commands.CheckFailure):
            message = str(cause) or "You do not have permission to use this command."
        elif isinstance(cause, LinkConflictError):
            message = str(cause)
        elif isinstance(cause, TwitterApiError):
            message = f"X data request failed: {cause}"
        elif isinstance(cause, ValueError):
            message = str(cause)
        else:
            LOGGER.exception("Unhandled app command error", exc_info=cause)
            message = "The command failed unexpectedly. The error has been logged."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            LOGGER.exception("Could not report command error to Discord")
