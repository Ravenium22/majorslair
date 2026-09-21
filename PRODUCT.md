# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One primary user: Major, the owner of the Major's Lair Discord community, working alone from a desktop browser. He opens the dashboard a few times a month, mostly around the monthly engagement cycle, and occasionally to check a single member someone complained about. He is not a developer; plain-language explanations of what the bot did and why matter more than density.

Secondary, confirmed: community members interact only through Discord slash commands (`/link-twitter`, `/my-score`, `/my-history`, `/leaderboard`). They never see the dashboard.

Occasional: the developer (Ravenium) uses the dashboard to diagnose scoring questions.

## Product Purpose

Measure how much each community member engages with the two X accounts `@m_m3l` and `@majorslair` (replies, quotes, retweets, mentions), turn that into a points leaderboard, and use it for two decisions of equal weight every cycle:

- reward the most engaged members (roles, recognition, top of the leaderboard);
- identify and remove inactive members fairly.

Success means both lists are accurate and defensible: nobody active is missing, nobody protected or new is on the removal list, and any single result can be explained down to the tweet. Removal itself happens outside this product (Major's own script); this product only produces the lists and reports.

## Positioning

The bot explains every point. Each scored action carries the tweet, the rule that fired, and the points, and every scan leaves a permanent report with everyone's standings. Hidden replies that X filters from a post are still found through search and, on demand, from a member's own timeline. A generic engagement counter cannot say why a member has 0 points; this one can, and Major can prove a decision to the member concerned.

## Operating Context

- Data source is twitterapi.io (paid credits, roughly $0.15 to $0.30 per full scan, up to a few dollars for a year-long scan). Cost visibility before an action is a real requirement, not a nicety.
- Member identity is Discord-first: a member exists once they are in the registry (imported from the community sheet or synced from the server), with or without an X account. The X link is verified through twitterapi.io and stored by stable X user id.
- Monthly ritual: run a scan, review who gained, review the low-activity report, give roles to the top, hand the bottom list to the removal script, optionally reset the cycle (which freezes a snapshot).
- Ad hoc: "why isn't this reply counted?" investigations, single-member scans, manual point adjustments and transfers, protecting members by Discord role.
- Admin access is Discord OAuth restricted to server administrators and configured roles; access is re-checked against Discord roles on every request.

## Capabilities and Constraints

Confirmed functionality: engagement scans over any window up to a year (dashboard and Discord), per-scan options (skip protected members, verify X accounts, deep timeline check with chosen depth), scan cost estimate before starting, permanent scan reports with per-member standings and CSV export, leaderboard by cycle or trailing window, member registry with filters (X state, protection, points, join date, active), per-member history and edit, single-member deep scan, manual point adjustments and transfers, bulk Discord role give/remove, CSV import from the community sheet, Discord member sync (join dates, handle changes, role-based protection), X account verification (suspended, deleted, renamed), tweet diagnosis, low-activity report with protection and newcomer grace, audit trail of every admin action, leaderboard reset snapshots.

Constraints and terminology:

- "Protected" (special role) members are never on the low-activity report; "newcomer grace" (default 30 days since joining Discord) does the same for new members.
- Points are recomputed from logged actions on every rescore; manual adjustments are stored separately and survive it.
- Likes cannot be tracked (no API for who liked a post).
- X hides some replies from both the post and search; only a member's own timeline reveals them, at extra cost.
- A cycle reset zeroes scores after saving a snapshot; scan reports are never deleted.

Explicitly undecided: whether the visual identity stays (see Brand Commitments); whether the dashboard will ever be used from a phone (assumed desktop-only for now).

## Brand Commitments

Name: "Major's Lair" and the bot name as it appears in Discord. Voice in Discord replies and on the dashboard: plain, direct, explains what happened.

The current visual treatment (dark surfaces, amber accent, "Engagement Control" naming, small uppercase kicker labels) is a first draft and explicitly open to change, provided the result stays professional and calm. No logo or brand assets exist in the repository; do not invent any.

## Evidence on Hand

- Real production data: about 251 members, 240 linked X accounts, several completed scans with reports, a 1-year scan, live twitterapi.io credit usage figures. All in the production database, not in the repository.
- Community sheet export (CSV with Discord id, X handle, special-role columns) held locally by the developer; contains wallet addresses and must never be committed.
- No testimonials, screenshots for marketing, or press. There is no marketing surface; do not fabricate one.

## Product Principles

1. Every number must be explainable to the member it concerns, down to the tweet and the rule.
2. Nothing irreversible or costly happens without showing the cost or asking first; nothing is ever silently deleted.
3. Protected and new members are never punished by an automated list.
4. Prefer one clear path over options; the user runs this a few times a month and should not need to relearn it.
5. Discord and dashboard must agree: same windows, same names, same numbers.

## Accessibility & Inclusion

No formal standard was set. Practical requirement: readable at desktop sizes without strain for a non-technical user (minimum 11px labels, clear contrast, visible focus), and keyboard-closable dialogs. Member handles include non-Latin and decorated Unicode names that must render and sort without breaking.
