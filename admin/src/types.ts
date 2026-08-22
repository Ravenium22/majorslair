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
}

export type Scan = {
  scan_id: string
  period: string
  status: 'running' | 'complete' | 'failed'
  triggered_by: string
  source: string
  started_at: string
  completed_at: string
  summary: Record<string, number | string | string[]>
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
