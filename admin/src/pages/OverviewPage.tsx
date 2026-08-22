import { useState } from 'react'
import { Activity, ArrowUpRight, Bot, Clock3, Play, Radar, Sparkles, Trophy, Users } from 'lucide-react'
import useSWR from 'swr'
import { api, formatDate, formatScore, mutateApi } from '../api'
import { Empty, PageHeader, Status, Toast } from '../components'
import type { Overview, Session } from '../types'

export default function OverviewPage({ session }: { session: Session }) {
  const { data, mutate, isLoading } = useSWR<Overview>('/api/overview', api, { refreshInterval: 10000 })
  const [period, setPeriod] = useState('24h')
  const [notice, setNotice] = useState<{ text: string; kind: 'success' | 'error' | 'loading' }>()

  const scan = async () => {
    setNotice({ text: `Starting ${period} engagement scan…`, kind: 'loading' })
    try {
      await mutateApi('/api/scans', session.csrf_token, 'POST', { period })
      setNotice({ text: 'Scan queued. Results will appear here automatically.', kind: 'success' })
      await mutate()
    } catch (error) {
      setNotice({ text: error instanceof Error ? error.message : 'Could not start scan', kind: 'error' })
    }
  }

  const metrics = [
    { label: 'Linked members', value: data?.linked_members ?? 0, icon: Users, detail: 'Active accounts' },
    { label: 'Cycle score', value: formatScore(data?.total_score ?? 0), icon: Trophy, detail: data?.cycle_id || 'No cycle' },
    { label: 'Valid actions', value: data?.active_actions ?? 0, icon: Activity, detail: 'Current signal' },
    { label: 'Tracked posts', value: data?.tracked_posts ?? 0, icon: Radar, detail: 'Active sources' },
  ]

  return (
    <div className="page">
      <PageHeader
        eyebrow="Live operations"
        title="Engagement overview"
        copy="The current leaderboard cycle, scoring activity, and scan health at a glance."
        actions={<div className="system-pill"><i className={data?.bot_connected ? '' : 'offline'} /> Discord bot {data?.bot_connected ? 'online' : 'starting'}</div>}
      />
      {notice && <Toast message={notice.text} kind={notice.kind} />}
      <section className="metric-grid">
        {metrics.map(({ label, value, icon: Icon, detail }) => (
          <article className="metric" key={label}>
            <div><span>{label}</span><Icon size={18} /></div>
            <strong>{isLoading ? '—' : value}</strong>
            <small>{detail}</small>
          </article>
        ))}
      </section>
      <section className="overview-grid">
        <article className="panel leaderboard-panel">
          <div className="panel-head"><div><p className="eyebrow">Current standings</p><h2>Top contributors</h2></div><a href="#members">All members <ArrowUpRight size={15} /></a></div>
          {data?.leaderboard.length ? (
            <div className="leader-list">
              {data.leaderboard.map((user, index) => (
                <div className="leader-row" key={user.discord_user_id}>
                  <span className={`rank rank-${index + 1}`}>{String(index + 1).padStart(2, '0')}</span>
                  <div className="leader-avatar">{user.discord_username.slice(0, 1).toUpperCase()}</div>
                  <span className="member-cell"><strong>{user.discord_username}</strong><small>@{user.twitter_handle}</small></span>
                  <div className="score-bar"><i style={{ width: `${Math.max(3, (user.score / Math.max(data.leaderboard[0]?.score || 1, 1)) * 100)}%` }} /></div>
                  <strong className="score">{formatScore(user.score)}</strong>
                </div>
              ))}
            </div>
          ) : <Empty title="No leaderboard signal yet" copy="Members appear here after linking an X account and completing a scan." />}
        </article>
        <aside className="scan-card">
          <div className="scan-visual"><Sparkles /><div className="orbit one" /><div className="orbit two" /></div>
          <p className="eyebrow">Manual scan</p>
          <h2>Refresh the signal</h2>
          <p>Collect recent replies, quotes, retweets, and organic mentions from tracked accounts.</p>
          <label>Lookback window<select value={period} onChange={(event) => setPeriod(event.target.value)}><option value="24h">Last 24 hours</option><option value="7d">Last 7 days</option><option value="30d">Last 30 days</option></select></label>
          <button className="button primary" onClick={scan} disabled={data?.last_scan?.status === 'running'}><Play size={17} />{data?.last_scan?.status === 'running' ? 'Scan running' : 'Run engagement scan'}</button>
          <small><Clock3 size={13} /> Last completed {formatDate(data?.last_scan?.completed_at)}</small>
        </aside>
      </section>
      <section className="panel scan-history">
        <div className="panel-head"><div><p className="eyebrow">System activity</p><h2>Recent scan runs</h2></div><Bot size={21} /></div>
        <div className="table-wrap"><table><thead><tr><th>Status</th><th>Window</th><th>Source</th><th>Started</th><th>Matched</th><th>Requests</th></tr></thead><tbody>
          {data?.recent_scans.map((item) => <tr key={item.scan_id}><td><Status state={item.status} /></td><td className="mono">{item.period}</td><td>{item.source}</td><td>{formatDate(item.started_at)}</td><td>{item.summary?.discovered ?? '—'}</td><td>{item.summary?.api_requests ?? '—'}</td></tr>)}
        </tbody></table></div>
      </section>
    </div>
  )
}
