export type Session = {
  user: { id: string; username: string; avatar_url: string }
  csrf_token: string
  expires_at: string
}

export type LinkedUser = {
  discord_user_id: string
  discord_username: string
  twitter_handle: string
  twitter_user_id: string
  linked_at: string
  updated_at: string
  active: boolean
  score: number
  last_active_at: string
  handle_history: string
  special_role: boolean
  special_role_names: string
  x_status: '' | 'ok' | 'suspended' | 'unavailable'
  x_checked_at: string
}

export type Scan = {
  scan_id: string
  period: string
  status: 'running' | 'complete' | 'failed'
  triggered_by: string
  source: string
  started_at: string
  completed_at: string
  summary: Record<string, number | string | string[] | Record<string, string>[]>
  error: string
}

export type ScanRun = {
  scan_id: string
  period: string
  status: 'running' | 'complete' | 'failed'
  triggered_by: string
  source: string
  started_at: string
  completed_at: string
  summary: Record<string, unknown> & {
    score_changes?: { discord_user_id: string; discord_username: string; twitter_handle: string; before: number; after: number }[]
    x_unavailable?: { discord_user_id: string; discord_username: string; twitter_handle: string; status: string; reason: string }[]
    x_renamed?: { discord_user_id: string; discord_username: string; old_handle: string; new_handle: string }[]
    warnings?: string[]
    standings?: { discord_user_id: string; discord_username: string; twitter_handle: string; before: number; score: number; special_role: boolean; x_status: string }[]
  }
  error: string
}

export type Overview = {
  linked_members: number
  total_score: number
  active_actions: number
  tracked_posts: number
  cycle_id: string
  bot_connected: boolean
  last_scan: Scan | null
  leaderboard: LinkedUser[]
  recent_scans: Scan[]
}

export type Paginated<T> = { items: T[]; page: number; page_size: number; total: number }

export type Action = {
  action_key: string
  discord_user_id: string
  twitter_handle: string
  action_type: string
  target_handle: string
  action_url: string
  text: string
  points: number
  reason: string
  active: boolean
  occurred_at: string
}

export type TrackedPost = {
  tweet_id: string
  url: string
  source_handle: string
  discovered_at: string
  origin: string
  active: boolean
  last_checked_at: string
  post_created_at: string
}

export type ConfigEntry = {
  key: string
  value: string
  description: string
  updated_at: string
  updated_by: string
}

export type AuditEntry = {
  event_id: string
  event_type: string
  actor_discord_id: string
  subject_discord_id: string
  old_value: string
  new_value: string
  details: Record<string, unknown>
  created_at: string
}

export type ImportStatus = 'linked' | 'relinked' | 'unchanged' | 'registered' | 'skipped' | 'conflict' | 'failed'

export type ImportResult = {
  discord_user_id: string
  discord_username: string
  twitter_handle: string
  status: ImportStatus
  message: string
}

export type ImportResponse = { summary: Partial<Record<ImportStatus, number>>; results: ImportResult[] }

export type ScanEstimate = {
  period: string
  linked_members: number
  protected_linked: number
  unlinked_members: number
  tracked_posts: number
  verification_credits_per_account: number
  skip_protected_default: boolean
  estimate: {
    source_posts: number
    engagement_items: number
    source_credits: number
    engagement_credits: number
    mentions_credits_max: number
    sweep_credits_max: number
    timeline_credits_max: number
    timeline_pages: number
    timeline_credits_if_enabled: number
    timeline_members: number
    credits_low: number
    credits_high: number
    usd_low: number
    usd_high: number
    estimate_requests: number
    warnings: string[]
    cached: boolean
    error?: string
  } & { error?: string }
  previous_scan: { completed_at: string; discovered: number; api_requests: number; items_returned: number; credits: number | null } | null
}

export type VerifyResponse = {
  checked: number
  include_protected: boolean
  unavailable: { discord_user_id: string; discord_username: string; twitter_handle: string; status: string; reason: string }[]
  renamed: { discord_user_id: string; discord_username: string; old_handle: string; new_handle: string }[]
}

export type DiscordSyncResponse = {
  discord_members: number
  bots_skipped: number
  already_registered: number
  already_registered_active: number
  already_registered_inactive: number
  registry_active: number
  registry_inactive: number
  added: { discord_user_id: string; discord_username: string; roles?: string }[]
  renamed: { discord_user_id: string; old: string; discord_username: string }[]
  protected_by_role: { discord_user_id: string; discord_username: string; roles: string }[]
  protected_roles_configured: string[]
  left_server: { discord_user_id: string; discord_username: string }[]
}

export type Snapshot = {
  snapshot_id: string
  cycle_id: string
  reset_at: string
  reset_by_discord_id: string
  members: { rank: number; discord_user_id: string; discord_username: string; twitter_handle: string; score: number }[]
}

export type MemberScanResult = {
  discord_user_id: string
  discord_username: string
  twitter_handle: string
  period: string
  tweets_read: number
  complete: boolean
  matched: number
  new_actions: number
  replies: number
  quotes: number
  mentions: number
  points_before: number
  points_after: number
  api_requests: number
  items_returned: number
}

export type Diagnosis = {
  tweet_id: string
  findings: string[]
  tweet?: { author_handle: string; author_id: string; created_at: string; text: string; is_reply: boolean; reply_to_tweet_id: string; quoted_tweet_id: string; is_retweet: boolean; url: string }
  member?: LinkedUser | null
  actions: (Action & { action_tweet_id: string; source_post_id: string })[]
  score_preview?: { points: number; reason: string }
  parent?: { tweet_id: string; author_handle: string; created_at: string; url: string; tracked: boolean; reply_count: number | null }
  reply_endpoint?: { found: boolean; returned: number; complete: boolean }
  sweep?: { found: boolean; returned: number; complete: boolean }
  timeline?: { found: boolean; returned: number; enabled: boolean }
}

