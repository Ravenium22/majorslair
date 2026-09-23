from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any

from .database import DatabaseRepository, LinkConflictError
from .models import (
    ActionType,
    EngagementAction,
    LinkedUser,
    ReconcileScope,
    ScanSummary,
    Tweet,
)
from .scoring import DEFAULT_CONFIG, ScoringEngine, ScoringRules, content_fingerprint
from .twitter_client import (
    TwitterApiClient,
    TwitterApiError,
    parse_tweet,
    parse_twitter_user,
)
from .utils import (
    isoformat,
    normalize_handle,
    parse_bool,
    parse_datetime,
    parse_period,
    parse_status_url,
    utc_now,
)

LOGGER = logging.getLogger(__name__)


class EngagementService:
    def __init__(self, repository: DatabaseRepository, twitter: TwitterApiClient) -> None:
        self.repository = repository
        self.twitter = twitter
        self._scan_lock = asyncio.Lock()
        self._estimate_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    async def link_user(
        self, *, discord_user_id: str, discord_username: str, handle: str
    ) -> tuple[str, str, str]:
        normalized = normalize_handle(handle)
        profile = await self.twitter.get_user_info(normalized)
        twitter_user_id, canonical_handle, _ = parse_twitter_user(profile)
        canonical_handle = canonical_handle or normalized
        if not twitter_user_id:
            raise TwitterApiError("X profile lookup did not return a stable user ID")
        old_handle, new_handle = await self.repository.link_user(
            discord_user_id=discord_user_id,
            discord_username=discord_username,
            twitter_handle=canonical_handle,
            twitter_user_id=twitter_user_id,
        )
        await self.repository.append_audit(
            event_type="twitter_link_changed" if old_handle else "twitter_linked",
            actor_discord_id=discord_user_id,
            subject_discord_id=discord_user_id,
            old_value=f"@{old_handle}" if old_handle else "",
            new_value=f"@{new_handle}",
            details={"twitter_user_id": twitter_user_id},
        )
        return old_handle, new_handle, twitter_user_id

    async def import_links(
        self,
        rows: list[dict[str, Any]],
        *,
        actor_discord_id: str,
        concurrency: int = 4,
    ) -> list[dict[str, str]]:
        """Bulk-register members from a spreadsheet and link the ones with an X handle.

        Each row is a dict with ``discord_user_id``, ``discord_username``, ``twitter_handle``
        and optionally ``special_role`` / ``special_role_names``. Every member in the sheet
        ends up in the registry; those without a handle are ``registered`` and can link
        later. Every row gets a ``status`` of linked, relinked, unchanged, registered,
        conflict, or failed, in input order. Rows already linked to the same handle never
        spend a twitterapi.io lookup, and a rejected key or an empty balance aborts the
        remaining lookups instead of failing them one by one.
        """
        semaphore = asyncio.Semaphore(max(1, concurrency))
        abort_message: str | None = None

        async def process(row: dict[str, Any]) -> dict[str, str]:
            nonlocal abort_message
            discord_user_id = str(row["discord_user_id"])
            discord_username = str(row.get("discord_username") or discord_user_id)
            raw_handle = str(row.get("twitter_handle") or "")
            result = {
                "discord_user_id": discord_user_id,
                "discord_username": discord_username,
                "twitter_handle": raw_handle.strip().removeprefix("@"),
            }
            await self.repository.register_member(
                discord_user_id=discord_user_id,
                discord_username=discord_username,
                special_role=row.get("special_role"),
                special_role_names=row.get("special_role_names"),
            )
            if not raw_handle.strip():
                return {**result, "status": "registered", "message": "No X handle yet"}
            try:
                normalized = normalize_handle(raw_handle)
            except ValueError as exc:
                return {**result, "status": "failed", "message": str(exc)}
            result["twitter_handle"] = normalized
            existing = await self.repository.get_user(discord_user_id)
            if existing and existing.active and existing.twitter_handle == normalized:
                return {**result, "status": "unchanged", "message": "Already linked"}
            async with semaphore:
                if abort_message:
                    return {**result, "status": "failed", "message": abort_message}
                try:
                    old_handle, new_handle, _ = await self.link_user(
                        discord_user_id=discord_user_id,
                        discord_username=discord_username,
                        handle=normalized,
                    )
                except LinkConflictError as exc:
                    return {**result, "status": "conflict", "message": str(exc)}
                except TwitterApiError as exc:
                    if exc.status in {401, 402}:
                        abort_message = str(exc)
                    if exc.status == 404 or "not found" in str(exc).lower():
                        return {
                            **result,
                            "status": "failed",
                            "message": "X account not found; registered without a link",
                        }
                    return {**result, "status": "failed", "message": str(exc)}
            result["twitter_handle"] = new_handle
            if old_handle and old_handle != new_handle:
                return {**result, "status": "relinked", "message": f"Was @{old_handle}"}
            return {**result, "status": "linked", "message": "Linked and verified"}

        results = list(await asyncio.gather(*(process(row) for row in rows)))
        counts: dict[str, int] = {}
        for item in results:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        await self.repository.append_audit(
            event_type="admin_members_imported",
            actor_discord_id=actor_discord_id,
            details={"rows": len(rows), **counts},
        )
        return results

    async def diagnose_tweet(self, url_or_id: str) -> dict[str, Any]:
        """Explain why a tweet is or is not counted, checking every path the scan uses.

        Costs a handful of twitterapi.io requests. Returns plain-language findings plus the
        raw facts so an admin can see whether the miss is matching, filtering, or scoring.
        """
        raw_value = url_or_id.strip()
        tweet_id = raw_value if raw_value.isdigit() else parse_status_url(raw_value)
        findings: list[str] = []
        result: dict[str, Any] = {"tweet_id": tweet_id, "findings": findings}
        config = await self.repository.get_config()
        rules = ScoringRules.from_mapping(config)
        targets = {rules.primary_handle, rules.secondary_handle} - {""}

        tweets = await self.twitter.get_tweets([tweet_id])
        if not tweets:
            findings.append(
                "X returned nothing for this id: the tweet is deleted, the account is "
                "suspended or private, or the id is wrong."
            )
            return result
        tweet = tweets[0]
        result["tweet"] = {
            "author_handle": tweet.author_handle,
            "author_id": tweet.author_id,
            "created_at": isoformat(tweet.created_at),
            "text": tweet.text,
            "is_reply": tweet.is_reply,
            "reply_to_tweet_id": tweet.reply_to_tweet_id,
            "quoted_tweet_id": tweet.quoted_tweet_id,
            "is_retweet": tweet.is_retweet,
            "url": tweet.url,
        }

        users = await self.repository.list_users(active_only=True)
        by_id, by_handle = self._user_indexes(users)
        member = self._match_user(tweet.author_id, tweet.author_handle, by_id, by_handle)
        result["member"] = asdict(member) if member else None
        if member is None:
            findings.append(
                f"@{tweet.author_handle} is not linked to any active member (stable X id "
                f"{tweet.author_id}). Nothing by this account can score until they run "
                "/link-twitter or an admin links them."
            )
        elif member.special_role:
            findings.append(
                f"Author is linked to {member.discord_username} and is a protected member. "
                "Scans run with 'skip protected' leave them unscored."
            )
        else:
            findings.append(f"Author is linked to {member.discord_username}.")

        logged = await self.repository.actions_for_tweet(tweet_id)
        result["actions"] = [asdict(action) for action in logged]
        own = [a for a in logged if a.action_tweet_id == tweet_id]
        if own:
            for action in own:
                findings.append(
                    f"Logged as a {action.action_type.value} on @{action.target_handle}: "
                    f"{action.points:g} points ({action.reason})"
                    + ("" if action.active else " [inactive]")
                )
        else:
            findings.append("This tweet id is not in the actions log for any cycle.")

        # Scoring preview, independent of whether it was matched.
        if member is not None and (tweet.is_reply or tweet.quoted_tweet_id):
            preview = self._make_action(
                cycle_id="preview",
                user=member,
                action_type=ActionType.QUOTE if tweet.quoted_tweet_id else ActionType.REPLY,
                target_handle=next(iter(targets), ""),
                source_post_id=tweet.reply_to_tweet_id or tweet.quoted_tweet_id,
                action_tweet_id=tweet.tweet_id,
                action_url=tweet.url,
                text=tweet.text,
                has_media=tweet.has_media,
                occurred_at=tweet.created_at,
            )
            points, reason = ScoringEngine(rules).score_one(preview)
            result["score_preview"] = {"points": points, "reason": reason}
            findings.append(f"If matched today it would score {points:g} points: {reason}.")

        parent_id = tweet.reply_to_tweet_id
        if not parent_id:
            lowered = tweet.text.lower()
            mentioned = next((t for t in sorted(targets) if f"@{t}" in lowered), "")
            if not mentioned:
                findings.append(
                    "This is a standalone post that does not @-mention a tracked account, so it "
                    "can never count. Naming the account without the @ is not a mention."
                )
                return result
            window_since = tweet.created_at - timedelta(days=1)
            window_until = tweet.created_at + timedelta(days=1)
            feed = await self.twitter.get_mentions(
                mentioned, since=window_since, until=window_until, max_pages=5
            )
            feed_ids = set()
            for raw in feed.items:
                try:
                    feed_ids.add(parse_tweet(raw).tweet_id)
                except (TypeError, ValueError):
                    continue
            in_feed = tweet_id in feed_ids
            cap_pages = self._config_int(config, "max_mention_pages")
            result["mention_feed"] = {
                "target": mentioned,
                "found": in_feed,
                "returned": len(feed_ids),
                "cap_pages": cap_pages,
            }
            if in_feed:
                findings.append(
                    f"X's mention feed for @{mentioned} does return this post around its date. "
                    f"A scan misses it only when the mention page cap ({cap_pages} pages, "
                    f"about {cap_pages * 20} newest mentions) runs out before reaching "
                    f"{tweet.created_at.date().isoformat()}. Raise max_mention_pages for long "
                    "windows, or run a single-member scan."
                )
            else:
                findings.append(
                    f"X's mention feed for @{mentioned} does NOT return this post "
                    f"({len(feed_ids)} mentions read around its date): X filters it, the "
                    "same way it hides low-quality replies. Only a single-member scan or the "
                    "Deep check can see it."
                )
            return result

        parents = await self.twitter.get_tweets([parent_id])
        parent = parents[0] if parents else None
        tracked = {
            post["tweet_id"] for post in await self.repository.list_tracked_posts(active_only=False)
        }
        result["parent"] = {
            "tweet_id": parent_id,
            "author_handle": parent.author_handle if parent else "",
            "created_at": isoformat(parent.created_at) if parent else "",
            "url": parent.url if parent else f"https://x.com/i/status/{parent_id}",
            "tracked": parent_id in tracked,
            "reply_count": parent.reply_count if parent else None,
        }
        if parent is None:
            findings.append("The parent post could not be fetched (deleted or private).")
            return result
        if parent.author_handle not in targets:
            findings.append(
                f"The parent post is by @{parent.author_handle}, which is not a tracked "
                f"account ({', '.join('@' + t for t in sorted(targets))}). Replies to other "
                "accounts never score."
            )
            return result
        if parent_id not in tracked:
            findings.append(
                "The parent post is by a tracked account but has never been fetched by a scan: "
                "no scan window included its date, or the source page cap was reached. It is "
                "only reachable through the reply sweep."
            )

        window_since = tweet.created_at - timedelta(days=1)
        window_until = tweet.created_at + timedelta(days=1)
        replies = await self.twitter.get_replies(
            parent_id,
            since=window_since,
            until=window_until,
            max_pages=self._config_int(config, "max_action_pages_per_post"),
        )
        reply_ids = set()
        for raw in replies.items:
            try:
                reply_ids.add(parse_tweet(raw).tweet_id)
            except (TypeError, ValueError):
                continue
        in_reply_list = tweet_id in reply_ids
        result["reply_endpoint"] = {
            "found": in_reply_list,
            "returned": len(reply_ids),
            "complete": replies.complete,
        }
        findings.append(
            "The parent's reply list returned this reply."
            if in_reply_list
            else f"The parent's reply list does NOT include this reply ({len(reply_ids)} "
            "replies returned). X hides low-quality replies from that list."
        )

        sweep = await self.twitter.search_replies_to(
            parent.author_handle, since=window_since, until=window_until, max_pages=5
        )
        sweep_ids = set()
        for raw in sweep.items:
            try:
                sweep_ids.add(parse_tweet(raw).tweet_id)
            except (TypeError, ValueError):
                continue
        in_sweep = tweet_id in sweep_ids
        result["sweep"] = {
            "found": in_sweep,
            "returned": len(sweep_ids),
            "complete": sweep.complete,
        }
        findings.append(
            "The reply sweep (search to:@account) returned it, so the next scan will log it."
            if in_sweep
            else f"The reply sweep also does NOT return it ({len(sweep_ids)} replies in a "
            "two-day window). X excludes it from search as well."
        )

        timeline, _ = await self.twitter.get_user_timeline_with_replies(
            tweet.author_handle, since=window_since, until=window_until, max_pages=3
        )
        in_timeline = any(item.tweet_id == tweet_id for item in timeline)
        try:
            by_author = await self.twitter.search_from_user(
                tweet.author_handle, since=window_since, until=window_until, max_pages=3
            )
        except TwitterApiError:
            by_author = []
        in_author_search = any(item.tweet_id == tweet_id for item in by_author)
        result["author_search"] = {"found": in_author_search, "returned": len(by_author)}
        timeline_pages = self._config_int(config, "member_timeline_pages", minimum=0)
        result["timeline"] = {
            "found": in_timeline,
            "returned": len(timeline),
            "enabled": timeline_pages > 0,
        }
        if in_timeline and timeline_pages > 0:
            findings.append(
                "The author's own timeline shows it and the timeline path is on, so the next "
                "scan will log it."
            )
        elif in_timeline:
            findings.append(
                "The author's own timeline shows it. Set member_timeline_pages in Scoring rules "
                "to 1 or more and the next scan will log it (about 300 credits per member per "
                "page)."
            )
        elif in_author_search:
            findings.append(
                "The author's timeline feed does not reach it, but a search for the author's "
                "own posts (from:@handle) does. A single-member scan will log it: it now falls "
                "back to that search when the timeline runs dry."
            )
        else:
            findings.append(
                "Neither the author's timeline nor a search for their own posts returns it; "
                "the bot has no way to see it."
            )
        return result

    async def scan_member(
        self,
        *,
        discord_user_id: str,
        period: str,
        actor_discord_id: str,
        max_pages: int = 25,
    ) -> dict[str, Any]:
        """Deep-check one member: read their own timeline for the window and score it.

        Reads only this member's tweets (replies included), so it costs a few hundred
        credits at most. Counts replies to the tracked accounts, quotes of tracked posts and
        standalone mentions; retweets cannot be attributed from a timeline. Nothing is ever
        deactivated by this scan, it only adds or refreshes this member's actions.
        """
        duration, period_label = parse_period(period)
        member = await self.repository.get_user(discord_user_id)
        if member is None:
            raise ValueError("Member not found")
        if not member.twitter_user_id or not member.twitter_handle:
            raise ValueError(f"{member.discord_username} has no X account linked")
        if not member.active:
            raise ValueError(f"{member.discord_username} is inactive")
        if self._scan_lock.locked():
            raise ValueError("An engagement scan is running; try again when it finishes.")
        run_id = await self.repository.create_scan_run(
            period=period_label, triggered_by=actor_discord_id, source="member"
        )
        try:
            outcome = await self._scan_member_impl(
                member=member,
                duration=duration,
                period_label=period_label,
                actor_discord_id=actor_discord_id,
                max_pages=max_pages,
            )
        except Exception as exc:
            await self.repository.finish_scan_run(run_id, error=str(exc))
            raise
        report = {
            **outcome,
            "member_scan": True,
            "discovered": outcome["matched"],
            "tweets_returned": outcome["items_returned"],
        }
        await self.repository.finish_scan_run(run_id, summary=report)
        outcome["scan_id"] = run_id
        return outcome

    async def _scan_member_impl(
        self,
        *,
        member: LinkedUser,
        duration: timedelta,
        period_label: str,
        actor_discord_id: str,
        max_pages: int,
    ) -> dict[str, Any]:
        discord_user_id = member.discord_user_id
        async with self._scan_lock:
            config = await self.repository.get_config()
            rules = ScoringRules.from_mapping(config)
            cycle_id = config.get("current_cycle_id", DEFAULT_CONFIG["current_cycle_id"])
            until = utc_now()
            since = until - duration
            cycle_started_at = config.get("cycle_started_at", "").strip()
            if cycle_started_at:
                since = max(since, parse_datetime(cycle_started_at))
            targets = {rules.primary_handle, rules.secondary_handle} - {""}
            tracked = {
                post["tweet_id"]: post["source_handle"]
                for post in await self.repository.list_tracked_posts(active_only=False)
            }
            self.twitter.reset_usage()
            tweets, complete = await self.twitter.get_user_timeline_with_replies(
                member.twitter_handle, since=since, until=until, max_pages=max_pages
            )
            timeline_count = len(tweets)
            oldest = min((t.created_at for t in tweets), default=None)
            timeline_ended_early = complete and (oldest is None or oldest > since)
            search_filled = 0
            if timeline_ended_early:
                # X's timeline endpoint stopped before the window start (it often serves
                # only the newest few dozen items); fill the rest through search.
                fill_until = oldest if oldest is not None else until
                known = {t.tweet_id for t in tweets}
                try:
                    extra = await self.twitter.search_from_user(
                        member.twitter_handle, since=since, until=fill_until, max_pages=max_pages
                    )
                except TwitterApiError as exc:
                    LOGGER.warning("from: search failed for @%s: %s", member.twitter_handle, exc)
                    extra = []
                for t in extra:
                    if t.tweet_id not in known:
                        known.add(t.tweet_id)
                        tweets.append(t)
                        search_filled += 1
            actions: list[EngagementAction] = []
            seen: set[str] = set()
            for tweet in tweets:
                if tweet.tweet_id in seen or tweet.is_retweet:
                    continue
                seen.add(tweet.tweet_id)
                if tweet.reply_to_tweet_id and tweet.reply_to_handle in targets:
                    action_type, target, source = (
                        ActionType.REPLY,
                        tweet.reply_to_handle,
                        tweet.reply_to_tweet_id,
                    )
                elif tweet.quoted_tweet_id and tweet.quoted_tweet_id in tracked:
                    action_type, target, source = (
                        ActionType.QUOTE,
                        tracked[tweet.quoted_tweet_id],
                        tweet.quoted_tweet_id,
                    )
                elif not tweet.reply_to_tweet_id:
                    lowered = tweet.text.lower()
                    mentioned = next((t for t in targets if f"@{t}" in lowered), "")
                    if not mentioned:
                        continue
                    action_type, target, source = ActionType.MENTION, mentioned, ""
                else:
                    continue
                actions.append(
                    self._make_action(
                        cycle_id=cycle_id,
                        user=member,
                        action_type=action_type,
                        target_handle=target,
                        source_post_id=source,
                        action_tweet_id=tweet.tweet_id,
                        action_url=tweet.url,
                        text=tweet.text,
                        has_media=tweet.has_media,
                        occurred_at=tweet.created_at,
                    )
                )
            existing_keys = {
                a.action_key
                for a in await self.repository.list_actions(
                    cycle_id=cycle_id, include_inactive=True
                )
                if a.discord_user_id == discord_user_id
            }
            new_actions = [a for a in actions if a.action_key not in existing_keys]
            await self.repository.reconcile_actions(
                cycle_id=cycle_id, discovered=actions, scopes=[]
            )
            await self.rescore_current_cycle(config=config)
            after = await self.repository.get_user(discord_user_id)
            outcome = {
                "discord_user_id": discord_user_id,
                "discord_username": member.discord_username,
                "twitter_handle": member.twitter_handle,
                "period": period_label,
                "tweets_read": len(tweets),
                "timeline_read": timeline_count,
                "timeline_ended_at": isoformat(oldest) if oldest else "",
                "timeline_ended_early": timeline_ended_early,
                "search_filled": search_filled,
                "complete": complete,
                "matched": len(actions),
                "new_actions": len(new_actions),
                "replies": sum(a.action_type is ActionType.REPLY for a in actions),
                "quotes": sum(a.action_type is ActionType.QUOTE for a in actions),
                "mentions": sum(a.action_type is ActionType.MENTION for a in actions),
                "points_before": member.score,
                "points_after": after.score if after else member.score,
                "api_requests": self.twitter.request_count,
                "items_returned": self.twitter.items_returned,
            }
            await self.repository.append_audit(
                event_type="member_scan",
                actor_discord_id=actor_discord_id,
                subject_discord_id=discord_user_id,
                details=outcome,
            )
            return outcome

    async def check_follows(
        self, *, actor_discord_id: str, max_pages: int = 400, include_protected: bool = True
    ) -> dict[str, Any]:
        """Check which linked members follow the primary and secondary accounts.

        Reads the follower list of each tracked account once and matches members against it,
        rather than asking about each member, which is both far cheaper and one pass.

        A follower list that hit the page cap is incomplete, and an incomplete list can only
        confirm a follow, never rule one out, so nothing is recorded as "no" in that case.
        """
        async with self._scan_lock:
            config = await self.repository.get_config()
            rules = ScoringRules.from_mapping(config)
            members = [
                user
                for user in await self.repository.list_users(active_only=True)
                if user.twitter_user_id and (include_protected or not user.special_role)
            ]
            self.twitter.reset_usage()
            accounts: dict[str, dict[str, Any]] = {}
            for slot, handle in (("primary", rules.primary_handle), ("secondary", rules.secondary_handle)):
                if not handle:
                    continue
                try:
                    ids, handles, complete = await self.twitter.get_followers(
                        handle, max_pages=max_pages
                    )
                except TwitterApiError as exc:
                    LOGGER.warning("follower list failed for @%s: %s", handle, exc)
                    accounts[slot] = {"handle": handle, "error": str(exc), "complete": False}
                    continue
                accounts[slot] = {
                    "handle": handle,
                    "followers": len(ids),
                    "complete": complete,
                    "ids": ids,
                    "handles": handles,
                }

            def verdict(slot: str, user: LinkedUser) -> str:
                account = accounts.get(slot)
                if not account or account.get("error"):
                    return ""
                follows = user.twitter_user_id in account["ids"] or (
                    user.twitter_handle.lower() in account["handles"]
                )
                if follows:
                    return "yes"
                # Only a complete list can prove somebody is missing from it.
                return "no" if account["complete"] else ""

            results = {
                user.discord_user_id: (verdict("primary", user), verdict("secondary", user))
                for user in members
            }
            await self.repository.record_follow_check(results)

            def tally(index: int, value: str) -> int:
                return sum(1 for pair in results.values() if pair[index] == value)

            summary = {
                "checked": len(members),
                "primary": {
                    k: v for k, v in accounts.get("primary", {}).items() if k not in {"ids", "handles"}
                },
                "secondary": {
                    k: v for k, v in accounts.get("secondary", {}).items() if k not in {"ids", "handles"}
                },
                "follows_primary": tally(0, "yes"),
                "follows_secondary": tally(1, "yes"),
                "follows_both": sum(1 for pair in results.values() if pair == ("yes", "yes")),
                "missing_primary": tally(0, "no"),
                "missing_secondary": tally(1, "no"),
                "unknown": sum(1 for pair in results.values() if "" in pair),
                "api_requests": self.twitter.request_count,
                "items_returned": self.twitter.items_returned,
                "credits": self.twitter.items_returned,
            }
            await self.repository.append_audit(
                event_type="follow_check",
                actor_discord_id=actor_discord_id,
                details=summary,
            )
            return summary

    async def verify_linked_accounts(
        self,
        *,
        actor_discord_id: str = "",
        summary: ScanSummary | None = None,
        include_protected: bool = True,
    ) -> dict[str, Any]:
        """Check linked X accounts by their stable ID.

        Suspended or deleted accounts are flagged on the member, renamed accounts get their
        handle updated automatically, and the outcome is written to ``summary`` when a scan
        is running. ``include_protected=False`` leaves special-role members out. A
        twitterapi.io failure becomes a warning instead of failing the scan.
        """
        users = [
            user
            for user in await self.repository.list_users(active_only=True)
            if user.twitter_user_id and (include_protected or not user.special_role)
        ]
        outcome: dict[str, Any] = {
            "checked": 0,
            "unavailable": [],
            "renamed": [],
            "include_protected": include_protected,
        }
        if not users:
            return outcome
        try:
            profiles = await self.twitter.get_users_by_ids([u.twitter_user_id for u in users])
        except TwitterApiError as exc:
            message = f"Could not verify X accounts: {exc}"
            LOGGER.warning(message)
            if summary is not None:
                summary.warnings.append(message)
            outcome["error"] = str(exc)
            return outcome

        records: list[dict[str, str]] = []
        for user in users:
            profile = profiles.get(user.twitter_user_id)
            if profile is None or profile.get("unavailable"):
                reason = str(
                    (profile or {}).get("unavailableReason")
                    or (profile or {}).get("message")
                    or "account not found"
                ).strip()
                status = "suspended" if "suspend" in reason.lower() else "unavailable"
                records.append({"discord_user_id": user.discord_user_id, "status": status})
                outcome["unavailable"].append(
                    {
                        "discord_user_id": user.discord_user_id,
                        "discord_username": user.discord_username,
                        "twitter_handle": user.twitter_handle,
                        "status": status,
                        "reason": reason[:120],
                    }
                )
                continue
            _, handle, _ = parse_twitter_user(profile)
            handle = (handle or "").lower()
            record = {"discord_user_id": user.discord_user_id, "status": "ok"}
            if handle and handle != user.twitter_handle.lower():
                record["twitter_handle"] = handle
                outcome["renamed"].append(
                    {
                        "discord_user_id": user.discord_user_id,
                        "discord_username": user.discord_username,
                        "old_handle": user.twitter_handle,
                        "new_handle": handle,
                    }
                )
            records.append(record)
        await self.repository.record_x_verification(records)
        outcome["checked"] = len(users)
        if summary is not None:
            summary.x_checked = len(users)
            summary.x_unavailable = list(outcome["unavailable"])
            summary.x_renamed = list(outcome["renamed"])
        await self.repository.append_audit(
            event_type="x_accounts_verified",
            actor_discord_id=actor_discord_id,
            details={
                "checked": len(users),
                "unavailable": len(outcome["unavailable"]),
                "renamed": len(outcome["renamed"]),
                "include_protected": include_protected,
            },
        )
        return outcome

    async def unlink_user(self, *, discord_user_id: str) -> str:
        old_handle = await self.repository.unlink_user(discord_user_id)
        if old_handle:
            await self.repository.append_audit(
                event_type="twitter_unlinked",
                actor_discord_id=discord_user_id,
                subject_discord_id=discord_user_id,
                old_value=f"@{old_handle}",
            )
        return old_handle

    async def track_post(self, *, url: str, actor_discord_id: str) -> Tweet:
        tweet_id = parse_status_url(url)
        tweets = await self.twitter.get_tweets([tweet_id])
        if not tweets:
            raise ValueError("The X post was not found or is no longer public")
        tweet = tweets[0]
        config = await self.repository.get_config()
        rules = ScoringRules.from_mapping(config)
        if tweet.author_handle not in {rules.primary_handle, rules.secondary_handle}:
            raise ValueError(
                f"Only posts by @{rules.primary_handle} or @{rules.secondary_handle} can be tracked"
            )
        now = isoformat()
        await self.repository.upsert_tracked_posts(
            [
                {
                    "tweet_id": tweet.tweet_id,
                    "url": tweet.url,
                    "source_handle": tweet.author_handle,
                    "discovered_at": now,
                    "origin": "manual",
                    "active": "TRUE",
                    "last_checked_at": "",
                    "post_created_at": isoformat(tweet.created_at),
                }
            ]
        )
        await self.repository.append_audit(
            event_type="post_tracked",
            actor_discord_id=actor_discord_id,
            new_value=tweet.url,
            details={"tweet_id": tweet.tweet_id, "source_handle": tweet.author_handle},
        )
        return tweet

    @staticmethod
    def _config_int(config: dict[str, str], key: str, *, minimum: int = 1) -> int:
        """Integer setting with a floor; settings where 0 means "off" pass ``minimum=0``."""
        try:
            value = int(str(config.get(key, DEFAULT_CONFIG[key])).strip() or 0)
        except ValueError:
            value = int(DEFAULT_CONFIG[key])
        return max(minimum, value)

    @staticmethod
    def _user_indexes(
        users: list[LinkedUser],
    ) -> tuple[dict[str, LinkedUser], dict[str, LinkedUser]]:
        by_id = {user.twitter_user_id: user for user in users if user.twitter_user_id}
        by_handle = {user.twitter_handle.lower(): user for user in users if user.twitter_handle}
        return by_id, by_handle

    @staticmethod
    def _match_user(
        twitter_user_id: str,
        twitter_handle: str,
        by_id: dict[str, LinkedUser],
        by_handle: dict[str, LinkedUser],
    ) -> LinkedUser | None:
        return by_id.get(twitter_user_id) or by_handle.get(twitter_handle.lower())

    @staticmethod
    def _make_action(
        *,
        cycle_id: str,
        user: LinkedUser,
        action_type: ActionType,
        target_handle: str,
        source_post_id: str,
        action_tweet_id: str,
        action_url: str,
        text: str,
        has_media: bool,
        occurred_at: datetime,
    ) -> EngagementAction:
        normalized, fingerprint = content_fingerprint(text)
        stable_actor = user.twitter_user_id or user.twitter_handle
        object_id = action_tweet_id or stable_actor
        action_key = ":".join(
            [cycle_id, action_type.value, target_handle, source_post_id, stable_actor, object_id]
        )
        return EngagementAction(
            action_key=action_key,
            cycle_id=cycle_id,
            discord_user_id=user.discord_user_id,
            twitter_user_id=user.twitter_user_id,
            twitter_handle=user.twitter_handle,
            action_type=action_type,
            target_handle=target_handle,
            source_post_id=source_post_id,
            action_tweet_id=action_tweet_id,
            action_url=action_url,
            text=text,
            normalized_text=normalized,
            content_hash=fingerprint,
            has_media=has_media,
            occurred_at=isoformat(occurred_at),
        )

    async def _load_source_posts(
        self,
        *,
        rules: ScoringRules,
        since: datetime,
        until: datetime,
        max_pages: int,
        summary: ScanSummary,
    ) -> tuple[list[Tweet], list[tuple[str, str]]]:
        indexed: dict[str, Tweet] = {}
        deleted_sources: list[tuple[str, str]] = []
        now_iso = isoformat()
        for handle in (rules.primary_handle, rules.secondary_handle):
            try:
                tweets, complete = await self.twitter.get_recent_tweets(
                    handle, since=since, until=until, max_pages=max_pages
                )
            except TwitterApiError as exc:
                summary.warnings.append(f"Could not fetch @{handle} posts: {exc}")
                continue
            if not complete:
                summary.warnings.append(f"Source post page cap reached for @{handle}")
            indexed.update({tweet.tweet_id: tweet for tweet in tweets})

        auto_rows = [
            {
                "tweet_id": tweet.tweet_id,
                "url": tweet.url,
                "source_handle": tweet.author_handle,
                "discovered_at": now_iso,
                "origin": "auto",
                "active": "TRUE",
                "last_checked_at": now_iso,
                "post_created_at": isoformat(tweet.created_at),
            }
            for tweet in indexed.values()
        ]
        await self.repository.upsert_tracked_posts(auto_rows)

        tracked = await self.repository.list_tracked_posts(active_only=True)
        candidates = []
        for row in tracked:
            origin = str(row.get("origin", "auto")).lower()
            created_raw = row.get("post_created_at") or row.get("discovered_at")
            created = parse_datetime(created_raw)
            if origin == "manual" or created >= since:
                candidates.append(row)

        missing_ids = [
            str(row.get("tweet_id", ""))
            for row in candidates
            if row.get("tweet_id") and str(row.get("tweet_id")) not in indexed
        ]
        if missing_ids:
            try:
                fetched = await self.twitter.get_tweets(missing_ids)
            except TwitterApiError as exc:
                summary.warnings.append(f"Could not verify tracked posts: {exc}")
                fetched = []
                missing_ids = []  # Do not mark anything deleted after an API failure.
            for tweet in fetched:
                if tweet.author_handle in {rules.primary_handle, rules.secondary_handle}:
                    indexed[tweet.tweet_id] = tweet
            returned_ids = {tweet.tweet_id for tweet in fetched}
            deleted_ids = set(missing_ids).difference(returned_ids)
            if deleted_ids:
                deleted_sources.extend(
                    (
                        str(row.get("tweet_id", "")),
                        str(row.get("source_handle", "")).removeprefix("@").lower(),
                    )
                    for row in candidates
                    if str(row.get("tweet_id", "")) in deleted_ids
                )
                await self.repository.upsert_tracked_posts(
                    [
                        {
                            **row,
                            "active": "FALSE",
                            "last_checked_at": now_iso,
                        }
                        for row in candidates
                        if str(row.get("tweet_id", "")) in deleted_ids
                    ]
                )
                summary.warnings.append(
                    f"{len(deleted_ids)} tracked source post(s) are no longer public"
                )
        return list(indexed.values()), deleted_sources

    async def _collect_post_actions(
        self,
        *,
        post: Tweet,
        cycle_id: str,
        since: datetime,
        until: datetime,
        max_pages: int,
        by_id: dict[str, LinkedUser],
        by_handle: dict[str, LinkedUser],
        summary: ScanSummary,
    ) -> tuple[list[EngagementAction], list[ReconcileScope]]:
        actions: list[EngagementAction] = []
        scopes: list[ReconcileScope] = []

        async def safe_call(label: str, awaitable: Any) -> Any:
            try:
                return await awaitable
            except TwitterApiError as exc:
                summary.warnings.append(f"{label} failed for {post.tweet_id}: {exc}")
                return None

        replies_result, quotes_result, retweeters_result = await asyncio.gather(
            safe_call(
                "Replies",
                self.twitter.get_replies(
                    post.tweet_id, since=since, until=until, max_pages=max_pages
                ),
            ),
            safe_call(
                "Quotes",
                self.twitter.get_quotes(post.tweet_id, since=since, max_pages=max_pages),
            ),
            safe_call(
                "Retweeters", self.twitter.get_retweeters(post.tweet_id, max_pages=max_pages)
            ),
        )

        for action_type, result in (
            (ActionType.REPLY, replies_result),
            (ActionType.QUOTE, quotes_result),
            (ActionType.RETWEET, retweeters_result),
        ):
            complete = bool(result and result.complete)
            if result is not None and not result.complete:
                summary.incomplete_scopes += 1
                summary.capped_posts += 1
            scopes.append(
                ReconcileScope(
                    action_type=action_type,
                    target_handle=post.author_handle,
                    source_post_id=post.tweet_id,
                    since_iso="" if action_type == ActionType.RETWEET else isoformat(since),
                    until_iso="" if action_type == ActionType.RETWEET else isoformat(until),
                    complete=complete,
                )
            )

        for raw in replies_result.items if replies_result else []:
            try:
                tweet = parse_tweet(raw)
            except (TypeError, ValueError):
                continue
            if not (since <= tweet.created_at <= until):
                continue
            user = self._match_user(tweet.author_id, tweet.author_handle, by_id, by_handle)
            if user is None:
                summary.skipped_unlinked += 1
                continue
            actions.append(
                self._make_action(
                    cycle_id=cycle_id,
                    user=user,
                    action_type=ActionType.REPLY,
                    target_handle=post.author_handle,
                    source_post_id=post.tweet_id,
                    action_tweet_id=tweet.tweet_id,
                    action_url=tweet.url,
                    text=tweet.text,
                    has_media=tweet.has_media,
                    occurred_at=tweet.created_at,
                )
            )

        for raw in quotes_result.items if quotes_result else []:
            try:
                tweet = parse_tweet(raw)
            except (TypeError, ValueError):
                continue
            if not (since <= tweet.created_at <= until):
                continue
            user = self._match_user(tweet.author_id, tweet.author_handle, by_id, by_handle)
            if user is None:
                summary.skipped_unlinked += 1
                continue
            actions.append(
                self._make_action(
                    cycle_id=cycle_id,
                    user=user,
                    action_type=ActionType.QUOTE,
                    target_handle=post.author_handle,
                    source_post_id=post.tweet_id,
                    action_tweet_id=tweet.tweet_id,
                    action_url=tweet.url,
                    text=tweet.text,
                    has_media=tweet.has_media,
                    occurred_at=tweet.created_at,
                )
            )

        for raw in retweeters_result.items if retweeters_result else []:
            twitter_user_id, handle, _ = parse_twitter_user(raw)
            user = self._match_user(twitter_user_id, handle, by_id, by_handle)
            if user is None:
                summary.skipped_unlinked += 1
                continue
            actions.append(
                self._make_action(
                    cycle_id=cycle_id,
                    user=user,
                    action_type=ActionType.RETWEET,
                    target_handle=post.author_handle,
                    source_post_id=post.tweet_id,
                    action_tweet_id="",
                    action_url=post.url,
                    text="",
                    has_media=False,
                    # X does not expose when a retweet happened. Dating it at the post's own
                    # time keeps a long scan from piling every retweet onto one day, which
                    # would trip the daily cap and mis-place them in windowed leaderboards.
                    occurred_at=max(post.created_at, since),
                )
            )
        return actions, scopes

    async def _tweets_still_public(self, tweet_ids: list[str]) -> set[str]:
        """IDs X still returns; anything missing was deleted, hidden by suspension, or private."""
        try:
            return {tweet.tweet_id for tweet in await self.twitter.get_tweets(tweet_ids)}
        except TwitterApiError as exc:
            LOGGER.warning("Could not confirm deletions, keeping %s rows: %s", len(tweet_ids), exc)
            return set(tweet_ids)

    async def _collect_reply_sweep(
        self,
        *,
        rules: ScoringRules,
        cycle_id: str,
        since: datetime,
        until: datetime,
        max_pages: int,
        by_id: dict[str, LinkedUser],
        by_handle: dict[str, LinkedUser],
        already_counted_tweet_ids: set[str],
        summary: ScanSummary,
    ) -> list[EngagementAction]:
        """Second reply path: search ``to:@account`` for replies the reply list hid."""
        actions: list[EngagementAction] = []
        if max_pages <= 0:
            return actions
        for target in (rules.primary_handle, rules.secondary_handle):
            if not target:
                continue
            try:
                result = await self.twitter.search_replies_to(
                    target, since=since, until=until, max_pages=max_pages
                )
            except TwitterApiError as exc:
                summary.warnings.append(f"Reply sweep failed for @{target}: {exc}")
                continue
            if not result.complete:
                summary.warnings.append(
                    f"Reply sweep for @{target} hit the page cap (max_reply_search_pages)"
                )
            for raw in result.items:
                try:
                    tweet = parse_tweet(raw)
                except (TypeError, ValueError):
                    continue
                if tweet.tweet_id in already_counted_tweet_ids:
                    continue
                if not tweet.reply_to_tweet_id or tweet.is_retweet:
                    continue
                if not (since <= tweet.created_at <= until):
                    continue
                if tweet.author_handle == target:
                    continue
                user = self._match_user(tweet.author_id, tweet.author_handle, by_id, by_handle)
                if user is None:
                    summary.skipped_unlinked += 1
                    continue
                already_counted_tweet_ids.add(tweet.tweet_id)
                summary.swept_replies += 1
                actions.append(
                    self._make_action(
                        cycle_id=cycle_id,
                        user=user,
                        action_type=ActionType.REPLY,
                        target_handle=target,
                        source_post_id=tweet.reply_to_tweet_id,
                        action_tweet_id=tweet.tweet_id,
                        action_url=tweet.url,
                        text=tweet.text,
                        has_media=tweet.has_media,
                        occurred_at=tweet.created_at,
                    )
                )
        return actions

    async def _collect_member_timelines(
        self,
        *,
        rules: ScoringRules,
        cycle_id: str,
        since: datetime,
        until: datetime,
        max_pages: int,
        users: list[LinkedUser],
        already_counted_tweet_ids: set[str],
        summary: ScanSummary,
        concurrency: int = 4,
    ) -> list[EngagementAction]:
        """Third reply path: each member's own timeline, which X never filters."""
        if max_pages <= 0:
            return []
        targets = {rules.primary_handle, rules.secondary_handle} - {""}
        linked = [user for user in users if user.twitter_user_id and user.twitter_handle]
        semaphore = asyncio.Semaphore(max(1, concurrency))
        actions: list[EngagementAction] = []
        failures = 0

        async def one(user: LinkedUser) -> list[EngagementAction]:
            nonlocal failures
            async with semaphore:
                try:
                    tweets, _ = await self.twitter.get_user_timeline_with_replies(
                        user.twitter_handle, since=since, until=until, max_pages=max_pages
                    )
                except TwitterApiError:
                    failures += 1
                    return []
            found: list[EngagementAction] = []
            for tweet in tweets:
                if tweet.is_retweet or not tweet.reply_to_tweet_id:
                    continue
                if tweet.reply_to_handle not in targets:
                    continue
                if tweet.tweet_id in already_counted_tweet_ids:
                    continue
                already_counted_tweet_ids.add(tweet.tweet_id)
                found.append(
                    self._make_action(
                        cycle_id=cycle_id,
                        user=user,
                        action_type=ActionType.REPLY,
                        target_handle=tweet.reply_to_handle,
                        source_post_id=tweet.reply_to_tweet_id,
                        action_tweet_id=tweet.tweet_id,
                        action_url=tweet.url,
                        text=tweet.text,
                        has_media=tweet.has_media,
                        occurred_at=tweet.created_at,
                    )
                )
            return found

        for batch in await asyncio.gather(*(one(user) for user in linked)):
            actions.extend(batch)
        summary.timeline_members_checked = len(linked)
        summary.timeline_replies = len(actions)
        if failures:
            summary.warnings.append(f"Timeline check failed for {failures} member(s)")
        return actions

    async def _collect_mentions(
        self,
        *,
        rules: ScoringRules,
        cycle_id: str,
        since: datetime,
        until: datetime,
        max_pages: int,
        by_id: dict[str, LinkedUser],
        by_handle: dict[str, LinkedUser],
        already_counted_tweet_ids: set[str],
        tracked_post_ids: set[str],
        summary: ScanSummary,
    ) -> tuple[list[EngagementAction], list[ReconcileScope]]:
        actions: list[EngagementAction] = []
        scopes: list[ReconcileScope] = []
        seen_mentions: set[str] = set()
        for target in (rules.primary_handle, rules.secondary_handle):
            try:
                result = await self.twitter.get_mentions(
                    target, since=since, until=until, max_pages=max_pages
                )
            except TwitterApiError as exc:
                summary.warnings.append(f"Mentions failed for @{target}: {exc}")
                scopes.append(
                    ReconcileScope(
                        action_type=ActionType.MENTION,
                        target_handle=target,
                        since_iso=isoformat(since),
                        until_iso=isoformat(until),
                        complete=False,
                    )
                )
                continue
            if not result.complete:
                summary.incomplete_scopes += 1
                oldest = None
                for raw in result.items:
                    try:
                        created = parse_tweet(raw).created_at
                    except (TypeError, ValueError):
                        continue
                    oldest = created if oldest is None or created < oldest else created
                reached = (
                    f"; oldest mention read is from {oldest.date().isoformat()}"
                    if oldest is not None
                    else ""
                )
                summary.warnings.append(
                    f"Mention feed of @{target} hit the page cap "
                    f"(max_mention_pages={max_pages}){reached}. Older shout-outs in this "
                    "window were not read; raise the setting for long windows."
                )
            scopes.append(
                ReconcileScope(
                    action_type=ActionType.MENTION,
                    target_handle=target,
                    since_iso=isoformat(since),
                    until_iso=isoformat(until),
                    complete=result.complete,
                )
            )
            for raw in result.items:
                try:
                    tweet = parse_tweet(raw)
                except (TypeError, ValueError):
                    continue
                if not (since <= tweet.created_at <= until):
                    continue
                if tweet.tweet_id in seen_mentions or tweet.tweet_id in already_counted_tweet_ids:
                    continue
                if tweet.reply_to_tweet_id in tracked_post_ids:
                    continue
                if tweet.quoted_tweet_id in tracked_post_ids:
                    continue
                user = self._match_user(tweet.author_id, tweet.author_handle, by_id, by_handle)
                if user is None:
                    summary.skipped_unlinked += 1
                    continue
                actions.append(
                    self._make_action(
                        cycle_id=cycle_id,
                        user=user,
                        action_type=ActionType.MENTION,
                        target_handle=target,
                        source_post_id="",
                        action_tweet_id=tweet.tweet_id,
                        action_url=tweet.url,
                        text=tweet.text,
                        has_media=tweet.has_media,
                        occurred_at=tweet.created_at,
                    )
                )
                seen_mentions.add(tweet.tweet_id)
        return actions, scopes

    async def scan(
        self,
        *,
        period: str,
        actor_discord_id: str,
        source: str = "discord",
        scan_id: str | None = None,
        verify_x: bool = True,
        include_protected: bool | None = None,
        read_timelines: bool | None = None,
        timeline_pages: int | None = None,
    ) -> ScanSummary:
        """Run a scan. ``include_protected=None`` follows the skip_protected_members setting."""
        parse_period(period)
        if self._scan_lock.locked():
            raise ValueError(
                "An engagement scan is already running. Wait for it to finish, then try again."
            )
        if include_protected is None:
            config = await self.repository.get_config()
            include_protected = not parse_bool(
                config.get("skip_protected_members", DEFAULT_CONFIG["skip_protected_members"])
            )
        run_id = scan_id or await self.repository.create_scan_run(
            period=period, triggered_by=actor_discord_id, source=source
        )
        try:
            summary = await self._scan_impl(
                period=period,
                actor_discord_id=actor_discord_id,
                verify_x=verify_x,
                include_protected=include_protected,
                read_timelines=read_timelines,
                timeline_pages=timeline_pages,
            )
            summary.scan_id = run_id
            await self.repository.finish_scan_run(run_id, summary=asdict(summary))
            return summary
        except Exception as exc:
            await self.repository.finish_scan_run(run_id, error=str(exc))
            raise

    async def _scan_impl(
        self,
        *,
        period: str,
        actor_discord_id: str,
        verify_x: bool = True,
        include_protected: bool = True,
        read_timelines: bool | None = None,
        timeline_pages: int | None = None,
    ) -> ScanSummary:
        duration, period_label = parse_period(period)
        async with self._scan_lock:
            config = await self.repository.get_config()
            rules = ScoringRules.from_mapping(config)
            cycle_id = config.get("current_cycle_id", DEFAULT_CONFIG["current_cycle_id"])
            until = utc_now()
            since = until - duration
            cycle_started_at = config.get("cycle_started_at", "").strip()
            if cycle_started_at:
                since = max(since, parse_datetime(cycle_started_at))

            summary = ScanSummary(period_label=period_label)
            users = await self.repository.list_users(active_only=True)
            scores_before = {user.discord_user_id: user.score for user in users}
            skipped_ids: set[str] = set()
            if not include_protected:
                skipped_ids = {user.discord_user_id for user in users if user.special_role}
                users = [user for user in users if not user.special_role]
                summary.skipped_protected = len(skipped_ids)
            by_id, by_handle = self._user_indexes(users)
            self.twitter.reset_usage()

            source_posts, deleted_sources = await self._load_source_posts(
                rules=rules,
                since=since,
                until=until,
                max_pages=self._config_int(config, "max_source_pages"),
                summary=summary,
            )
            summary.source_posts = len(source_posts)
            discovered: list[EngagementAction] = []
            scopes: list[ReconcileScope] = [
                ReconcileScope(
                    action_type=action_type,
                    target_handle=target,
                    source_post_id=tweet_id,
                    complete=True,
                )
                for tweet_id, target in deleted_sources
                for action_type in (ActionType.REPLY, ActionType.QUOTE, ActionType.RETWEET)
            ]
            action_page_cap = self._config_int(config, "max_action_pages_per_post")
            for post in source_posts:
                post_actions, post_scopes = await self._collect_post_actions(
                    post=post,
                    cycle_id=cycle_id,
                    since=since,
                    until=until,
                    max_pages=action_page_cap,
                    by_id=by_id,
                    by_handle=by_handle,
                    summary=summary,
                )
                discovered.extend(post_actions)
                scopes.extend(post_scopes)

            if summary.capped_posts:
                summary.warnings.append(
                    f"{summary.capped_posts} reply/quote/retweet list(s) hit the per-post page "
                    "cap (max_action_pages_per_post); very busy posts may be under-counted."
                )
            counted_ids = {
                action.action_tweet_id for action in discovered if action.action_tweet_id
            }
            swept = await self._collect_reply_sweep(
                rules=rules,
                cycle_id=cycle_id,
                since=since,
                until=until,
                max_pages=self._config_int(config, "max_reply_search_pages"),
                by_id=by_id,
                by_handle=by_handle,
                already_counted_tweet_ids=counted_ids,
                summary=summary,
            )
            discovered.extend(swept)
            from_timelines = await self._collect_member_timelines(
                rules=rules,
                cycle_id=cycle_id,
                since=since,
                until=until,
                # Per-scan switch: off = nothing; on = the depth chosen for this scan (or the
                # setting, at least 1 page); None = whatever the setting says.
                max_pages=(
                    self._config_int(config, "member_timeline_pages", minimum=0)
                    if read_timelines is None
                    else (
                        max(
                            1,
                            timeline_pages
                            or self._config_int(config, "member_timeline_pages", minimum=0),
                        )
                        if read_timelines
                        else 0
                    )
                ),
                users=users,
                already_counted_tweet_ids=counted_ids,
                summary=summary,
            )
            discovered.extend(from_timelines)
            mentions, mention_scopes = await self._collect_mentions(
                rules=rules,
                cycle_id=cycle_id,
                since=since,
                until=until,
                max_pages=self._config_int(config, "max_mention_pages"),
                by_id=by_id,
                by_handle=by_handle,
                already_counted_tweet_ids=counted_ids,
                tracked_post_ids={post.tweet_id for post in source_posts},
                summary=summary,
            )
            discovered.extend(mentions)
            scopes.extend(mention_scopes)

            unique = {action.action_key: action for action in discovered}
            discovered = list(unique.values())
            summary.discovered = len(discovered)
            summary.replies = sum(a.action_type == ActionType.REPLY for a in discovered)
            summary.quotes = sum(a.action_type == ActionType.QUOTE for a in discovered)
            summary.retweets = sum(a.action_type == ActionType.RETWEET for a in discovered)
            summary.mentions = sum(a.action_type == ActionType.MENTION for a in discovered)
            summary.changed_actions = await self.repository.reconcile_actions(
                cycle_id=cycle_id,
                discovered=discovered,
                scopes=scopes,
                ignore_discord_ids=skipped_ids,
                still_public=self._tweets_still_public,
            )
            await self.rescore_current_cycle(config=config)
            changes = []
            standings = []
            for user in await self.repository.list_users(active_only=True):
                before = scores_before.get(user.discord_user_id, 0.0)
                standings.append(
                    {
                        "discord_user_id": user.discord_user_id,
                        "discord_username": user.discord_username,
                        "twitter_handle": user.twitter_handle,
                        "before": round(before, 2),
                        "score": round(user.score, 2),
                        "special_role": user.special_role,
                        "x_status": user.x_status,
                    }
                )
                if abs(user.score - before) > 1e-9:
                    changes.append(
                        {
                            "discord_user_id": user.discord_user_id,
                            "discord_username": user.discord_username,
                            "twitter_handle": user.twitter_handle,
                            "before": round(before, 2),
                            "after": round(user.score, 2),
                        }
                    )
            changes.sort(key=lambda item: item["after"] - item["before"], reverse=True)
            summary.score_changes_total = len(changes)
            summary.score_changes = changes[:100]
            standings.sort(key=lambda item: (-item["score"], item["discord_username"].lower()))
            summary.standings = standings
            if verify_x:
                await self.verify_linked_accounts(
                    actor_discord_id=actor_discord_id,
                    summary=summary,
                    include_protected=include_protected,
                )
            summary.api_requests = self.twitter.request_count
            summary.tweets_returned = self.twitter.items_returned

            await self.repository.upsert_tracked_posts(
                [
                    {
                        "tweet_id": post.tweet_id,
                        "url": post.url,
                        "source_handle": post.author_handle,
                        "active": "TRUE",
                        "last_checked_at": isoformat(),
                        "post_created_at": isoformat(post.created_at),
                    }
                    for post in source_posts
                ]
            )
            await self.repository.append_audit(
                event_type="engagement_scan",
                actor_discord_id=actor_discord_id,
                details={
                    "period": period_label,
                    "since": isoformat(since),
                    "until": isoformat(until),
                    "source_posts": summary.source_posts,
                    "discovered": summary.discovered,
                    "changed_actions": summary.changed_actions,
                    "api_requests": summary.api_requests,
                    "items_returned": summary.tweets_returned,
                    "incomplete_scopes": summary.incomplete_scopes,
                    "warnings": summary.warnings,
                    "x_checked": summary.x_checked,
                    "x_unavailable": len(summary.x_unavailable),
                    "x_renamed": len(summary.x_renamed),
                    "skipped_protected": summary.skipped_protected,
                },
            )
            return summary

    async def estimate_scan(self, period: str, *, max_age_seconds: int = 600) -> dict[str, Any]:
        """Estimate what a scan of ``period`` will cost, from the source posts' own counters.

        Fetches the tracked accounts' posts for the window (the same pages a scan fetches
        first) and sums their reply, quote, and retweet counters, capped by the configured
        page limits. Mentions cannot be counted ahead of time, so they are reported as an
        upper bound. Results are cached briefly so reopening the dialog is free.
        """
        duration, period_label = parse_period(period)
        cached = self._estimate_cache.get(period_label)
        now_ts = utc_now().timestamp()
        if cached and now_ts - cached[0] < max_age_seconds:
            return {**cached[1], "cached": True}

        config = await self.repository.get_config()
        rules = ScoringRules.from_mapping(config)
        until = utc_now()
        since = until - duration
        cycle_started_at = config.get("cycle_started_at", "").strip()
        if cycle_started_at:
            since = max(since, parse_datetime(cycle_started_at))
        page_size = 20
        action_cap = self._config_int(config, "max_action_pages_per_post") * page_size
        mention_cap = self._config_int(config, "max_mention_pages") * page_size
        source_pages = self._config_int(config, "max_source_pages")

        before = self.twitter.request_count
        posts: dict[str, Tweet] = {}
        warnings: list[str] = []
        for handle in (rules.primary_handle, rules.secondary_handle):
            if not handle:
                continue
            try:
                tweets, complete = await self.twitter.get_recent_tweets(
                    handle, since=since, until=until, max_pages=source_pages
                )
            except TwitterApiError as exc:
                warnings.append(f"Could not read @{handle}: {exc}")
                continue
            if not complete:
                warnings.append(
                    f"Only the newest {len(tweets)} posts of @{handle} fit in the page cap; "
                    "older posts in this window will be missed. Raise max_source_pages in "
                    "Scoring rules for long windows."
                )
            posts.update({tweet.tweet_id: tweet for tweet in tweets if not tweet.is_retweet})
        estimate_requests = self.twitter.request_count - before

        credit_per_item = 15
        engagement_items = 0
        for post in posts.values():
            for count in (post.reply_count, post.quote_count, post.retweet_count):
                engagement_items += max(1, min(count, action_cap))
        source_credits = max(len(posts), estimate_requests) * credit_per_item
        engagement_credits = engagement_items * credit_per_item
        mentions_credits_max = mention_cap * credit_per_item * 2
        # Project whether the mention cap can cover the window: read one page per account and
        # measure how many days it spans. Costs about 300 credits per account, cached below.
        mention_pages_cap = self._config_int(config, "max_mention_pages")
        window_days = max(duration.total_seconds() / 86400, 1 / 24)
        mention_coverage: list[dict[str, Any]] = []
        for handle in (rules.primary_handle, rules.secondary_handle):
            if not handle:
                continue
            try:
                sample = await self.twitter.get_mentions(
                    handle, since=since, until=until, max_pages=1
                )
            except TwitterApiError as exc:
                warnings.append(f"Could not sample the mention feed of @{handle}: {exc}")
                continue
            dates = []
            for raw in sample.items:
                try:
                    dates.append(parse_tweet(raw).created_at)
                except (TypeError, ValueError):
                    continue
            if len(dates) < 2 or sample.complete:
                # Fewer than a page of mentions in the window: the cap is irrelevant.
                mention_coverage.append(
                    {"handle": handle, "pages_needed": 1, "covered_days": window_days}
                )
                continue
            span_days = max((max(dates) - min(dates)).total_seconds() / 86400, 1 / 24)
            per_page_days = span_days
            pages_needed = int(window_days / per_page_days) + 1
            covered_days = round(per_page_days * mention_pages_cap, 1)
            mention_coverage.append(
                {
                    "handle": handle,
                    "per_page_days": round(per_page_days, 2),
                    "pages_needed": pages_needed,
                    "covered_days": covered_days,
                }
            )
            if pages_needed > mention_pages_cap:
                warnings.append(
                    f"@{handle} gets a page of mentions every ~{per_page_days:.1f} days, so "
                    f"max_mention_pages={mention_pages_cap} covers only about "
                    f"{covered_days:g} of the {window_days:.0f} days in this window. Set it to "
                    f"about {pages_needed} in Scoring rules → Scan guardrails before this scan "
                    f"(≈ {pages_needed * page_size * credit_per_item:,} credits for this account)."
                )
        sweep_cap = self._config_int(config, "max_reply_search_pages") * page_size
        sweep_credits_max = sweep_cap * credit_per_item * 2
        timeline_pages = self._config_int(config, "member_timeline_pages", minimum=0)
        linked_members = sum(
            1 for user in await self.repository.list_users(active_only=True) if user.twitter_user_id
        )
        timeline_credits_max = (
            linked_members * timeline_pages * page_size * credit_per_item if timeline_pages else 0
        )
        low = (
            source_credits
            + engagement_credits
            + (linked_members * credit_per_item if timeline_pages else 0)
        )
        high = low + mentions_credits_max + sweep_credits_max + timeline_credits_max
        result = {
            "period": period_label,
            "since": isoformat(since),
            "until": isoformat(until),
            "source_posts": len(posts),
            "engagement_items": engagement_items,
            "source_credits": source_credits,
            "engagement_credits": engagement_credits,
            "mentions_credits_max": mentions_credits_max,
            "mention_coverage": mention_coverage,
            "sweep_credits_max": sweep_credits_max,
            "timeline_credits_max": timeline_credits_max,
            "timeline_pages": timeline_pages,
            "timeline_credits_if_enabled": linked_members
            * max(1, timeline_pages)
            * page_size
            * credit_per_item,
            "timeline_members": linked_members,
            "credits_low": low,
            "credits_high": high,
            "usd_low": round(low / 100_000, 2),
            "usd_high": round(high / 100_000, 2),
            "estimate_requests": estimate_requests,
            "warnings": warnings,
            "computed_at": isoformat(until),
            "cached": False,
        }
        self._estimate_cache[period_label] = (now_ts, result)
        return result

    async def rescore_current_cycle(self, *, config: dict[str, str] | None = None) -> int:
        values = config or await self.repository.get_config()
        cycle_id = values.get("current_cycle_id", DEFAULT_CONFIG["current_cycle_id"])
        actions = await self.repository.list_actions(cycle_id=cycle_id, include_inactive=True)
        scored = ScoringEngine(ScoringRules.from_mapping(values)).score_all(actions)
        await self.repository.save_scored_actions(cycle_id=cycle_id, scored_actions=scored)
        return len(scored)
