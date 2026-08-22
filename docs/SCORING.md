# Community scoring rules

This is the default public scoring policy. The live source of truth is the admin site's
**Scoring rules** page; saving changes validates the rules and recalculates the current cycle.

## Base points

| Action | `@m_m3l` | `@majorslair` |
|---|---:|---:|
| Thoughtful reply | 12 | 5 |
| Quote tweet with commentary | 15 | 7 |
| Retweet | 7 | 3 |
| Organic tweet/mention | 10 | 4 |

If one tweet mentions both tracked accounts, it counts once at the primary `@m_m3l` weight. A reply
or quote already counted against a tracked post is not counted again as an organic mention.

## Quality controls

- Empty text and content made entirely of blacklisted low-effort words earn 0.
- Non-blacklisted text under 3 normalized words earns 10% of base points.
- 8–19 words: +2.
- 20+ words: +3.
- A question: +1.
- A research, DYOR, or on-chain reference: +2.
- Attached media: +2.
- A supporting link: +1.
- Total quality bonus is capped at +8.
- Repeated normalized text from the same X account scores once; later copies earn 0.
- By default, only the first 20 positive-scoring actions per member per UTC day earn points. Later
  actions remain visible in history with a 0-point explanation.

Retweets have no commentary to assess, so they receive only their configured base points.

## Default blacklist

`lfg`, `gm`, `gn`, `alpha`, `bullish`, `fire`, `moon`, `send it`, `lets go`, `let's go`

Normalization removes case, URLs, mentions, punctuation, and emoji before comparison. Admins can edit
the comma-separated `blacklist` value in `Config`.

## Transparency and fairness

Every action stores its text, normalized text, points, and a human-readable reason in `ActionsLog`.
Members can view the same reason with `/my-history`. Re-scans do not duplicate points. Missing public
content is deactivated only after the relevant twitterapi.io endpoint completed its scan; page-cap or
API errors never remove points.

Leaderboard reset snapshots preserve the previous rank and score. The new cycle starts at the reset
timestamp, so engagement from the previous cycle cannot be re-awarded.
