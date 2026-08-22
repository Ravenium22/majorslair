from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from .engagement import EngagementService
from .scoring import DEFAULT_CONFIG
from .settings import Settings
from .sheets import GoogleSheetRepository, LinkConflictError
from .twitter_client import TwitterApiClient, TwitterApiError

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
        old_handle = await self.bot.service.unlink_user(discord_user_id=str(interaction.user.id))
        if not old_handle:
            await interaction.response.send_message(
                "You do not have an active linked X account.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Unlinked **@{old_handle}**. Your history remains preserved.", ephemeral=True
        )
        await self.bot.audit(
            "X account unlinked", f"<@{interaction.user.id}> unlinked **@{old_handle}**"
        )

    @app_commands.command(
        name="leaderboard", description="Show the current public engagement leaderboard"
    )
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        users = await self.bot.repository.list_users(active_only=True)
        ranked = sorted(users, key=lambda user: (-user.score, user.discord_username.lower()))
        if not ranked:
            await interaction.response.send_message("No linked members yet.")
            return
        lines = [
            f"**{index}.** <@{user.discord_user_id}> · "
            f"**{score_label(user.score)}** pts · `@{user.twitter_handle}`"
            for index, user in enumerate(ranked[:25], start=1)
        ]
        embed = discord.Embed(
            title="Major's Lair · Engagement Leaderboard",
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
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="my-score", description="Show your score and rank for this cycle")
    @app_commands.guild_only()
    async def my_score(self, interaction: discord.Interaction) -> None:
        users = await self.bot.repository.list_users(active_only=True)
        ranked = sorted(users, key=lambda user: (-user.score, user.discord_username.lower()))
        rank = next(
            (
                index
                for index, user in enumerate(ranked, start=1)
                if user.discord_user_id == str(interaction.user.id)
            ),
            None,
        )
        if rank is None:
            await interaction.response.send_message(
                "Link an X account first with `/link-twitter`.", ephemeral=True
            )
            return
        user = ranked[rank - 1]
        await interaction.response.send_message(
            f"You are **#{rank}** of {len(ranked)} with "
            f"**{score_label(user.score)} points** (`@{user.twitter_handle}`).",
            ephemeral=True,
        )

    @app_commands.command(name="my-history", description="Show how your latest actions were scored")
    @app_commands.guild_only()
    async def my_history(self, interaction: discord.Interaction) -> None:
        config = await self.bot.repository.get_config()
        cycle_id = config.get("current_cycle_id", DEFAULT_CONFIG["current_cycle_id"])
        history = await self.bot.repository.user_history(str(interaction.user.id), cycle_id, 10)
        if not history:
            await interaction.response.send_message(
                "No engagement actions are logged for you in this cycle yet.", ephemeral=True
            )
            return
        embed = discord.Embed(title="Your recent engagement scoring", color=discord.Color.blurple())
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
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _run_scan(self, interaction: discord.Interaction, period: str) -> None:
        await interaction.response.defer(thinking=True)
        summary = await self.bot.service.scan(
            period=period, actor_discord_id=str(interaction.user.id)
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
            ),
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
        name="check-engagement", description="Scan and score a chosen recent period"
    )
    @app_commands.describe(period="Examples: 24h, 7d, 30d (maximum 31d)")
    @app_commands.guild_only()
    @admin_only()
    async def check_engagement(self, interaction: discord.Interaction, period: str = "7d") -> None:
        await self._run_scan(interaction, period)

    @app_commands.command(
        name="refresh-engagement", description="Run the configured short refresh scan"
    )
    @app_commands.guild_only()
    @admin_only()
    async def refresh_engagement(self, interaction: discord.Interaction) -> None:
        config = await self.bot.repository.get_config()
        period = config.get("default_refresh_period", DEFAULT_CONFIG["default_refresh_period"])
        await self._run_scan(interaction, period)

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
    @app_commands.describe(threshold="Optional point threshold; defaults to the Config sheet")
    @app_commands.guild_only()
    @admin_only()
    async def low_activity_report(
        self, interaction: discord.Interaction, threshold: float | None = None
    ) -> None:
        config = await self.bot.repository.get_config()
        effective = threshold
        if effective is None:
            effective = float(
                config.get("low_activity_threshold", DEFAULT_CONFIG["low_activity_threshold"])
            )
        users = await self.bot.repository.low_activity(effective)
        if users:
            lines = [
                f"<@{user.discord_user_id}> · {score_label(user.score)} pts · "
                f"`@{user.twitter_handle}`"
                for user in users[:50]
            ]
            suffix = f"\n…and {len(users) - 50} more." if len(users) > 50 else ""
            description = "\n".join(lines) + suffix
        else:
            description = "No active linked members are at or below this threshold."
        embed = discord.Embed(
            title=f"Low activity · ≤ {score_label(effective)} points",
            description=description,
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
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

    @app_commands.command(
        name="sync-sheet", description="Ensure Sheet tabs/columns and recalculate current scores"
    )
    @app_commands.guild_only()
    @admin_only()
    async def sync_sheet(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.repository.ensure_schema()
        count = await self.bot.service.rescore_current_cycle()
        await self.bot.repository.append_audit(
            event_type="sheet_synced",
            actor_discord_id=str(interaction.user.id),
            details={"rescored_actions": count},
        )
        await interaction.followup.send(
            f"Sheet schema is current; recalculated {count} action rows.", ephemeral=True
        )
        await self.bot.audit(
            "Sheet synchronized", f"<@{interaction.user.id}> synchronized schema and scores."
        )


class EngagementBot(commands.Bot):
    settings: Settings

    def __init__(
        self,
        *,
        settings: Settings,
        repository: GoogleSheetRepository,
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
