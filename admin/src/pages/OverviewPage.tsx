import { useState } from 'react'
import { Activity, ArrowUpRight, Bot, Clock3, Play, Radar, Sparkles, Trophy, Users } from 'lucide-react'
import useSWR from 'swr'
import { api, formatDate, formatScore, mutateApi } from '../api'
import { Empty, PageHeader, Status, Toast } from '../components'
import type { Overview, ScanEstimate, Session } from '../types'

const PERIOD_LABEL: Record<string, string> = { '24h': 'last 24 hours', '7d': 'last 7 days', '30d': 'last 30 days' }

export default function OverviewPage({ session }: { session: Session }) {
  const { data, mutate, isLoading } = useSWR<Overview>('/api/overview', api, { refreshInterval: 10000 })
  const [period, setPeriod] = useState('24h')
  const [notice, setNotice] = useState<{ text: string; kind: 'success' | 'error' | 'loading' }>()
  const [estimate, setEstimate] = useState<ScanEstimate>()
  const [verifyX, setVerifyX] = useState(true)
  const [skipProtected, setSkipProtected] = useState(false)
  const [starting, setStarting] = useState(false)

  const openScan = async () => {
    try {
      setEstimate(await api<ScanEstimate>(`/api/scans/estimate?period=${encodeURIComponent(period)}`))
    } catch (error) {
      setNotice({ text: error instanceof Error ? error.message : 'Could not prepare the scan', kind: 'error' })
    }
  }

  const scan = async () => {
    setStarting(true)
    setNotice({ text: `Starting ${period} engagement scan…`, kind: 'loading' })
    try {
      await mutateApi('/api/scans', session.csrf_token, 'POST', { period, verify_x: verifyX, skip_protected: skipProtected })
      setEstimate(undefined)
      setNotice({ text: 'Scan queued. Results will appear here automatically.', kind: 'success' })
      await mutate()
    } catch (error) {
      setNotice({ text: error instanceof Error ? error.message : 'Could not start scan', kind: 'error' })
    } finally { setStarting(false) }
  }

  const verifyCount = estimate ? estimate.linked_members - (skipProtected ? estimate.protected_linked : 0) : 0
  const verifyCredits = verifyX ? verifyCount * (estimate?.verification_credits_per_account ?? 10) : 0

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
          <button className="button primary" onClick={openScan} disabled={data?.last_scan?.status === 'running'}><Play size={17} />{data?.last_scan?.status === 'running' ? 'Scan running' : 'Run engagement scan'}</button>
          <small><Clock3 size={13} /> Last completed {formatDate(data?.last_scan?.completed_at)}</small>
        </aside>
      </section>
      {estimate && <div className="modal-backdrop" onMouseDown={() => { if (!starting) setEstimate(undefined) }}><div className="modal" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-icon"><Play /></div><p className="eyebrow">Engagement scan</p><h2>Scan the {PERIOD_LABEL[period] ?? estimate.period}</h2>
        <p>The bot collects replies, quotes, retweets, and mentions on the tracked accounts' posts, then scores every linked member who shows up. Cost depends on how many posts and replies there are, not on the member count.</p>
        <dl className="estimate-grid">
          <div><dt>Members who can score</dt><dd>{estimate.linked_members}<small>{estimate.protected_linked} protected</small></dd></div>
          <div><dt>Members without X</dt><dd>{estimate.unlinked_members}<small>not scanned</small></dd></div>
          <div><dt>Tracked posts on file</dt><dd>{estimate.tracked_posts}<small>new ones are discovered</small></dd></div>
          <div><dt>Last {estimate.period} scan</dt><dd>{estimate.previous_scan?.credits != null ? `≈ ${estimate.previous_scan.credits.toLocaleString()} cr` : '—'}<small>{estimate.previous_scan ? `${estimate.previous_scan.discovered} actions · ${estimate.previous_scan.api_requests} requests · ≈ $${((estimate.previous_scan.credits ?? 0) / 100000).toFixed(2)}` : 'no previous scan of this window'}</small></dd></div>
        </dl>
        <label className="check-row"><input type="checkbox" checked={verifyX} onChange={(e) => setVerifyX(e.target.checked)} disabled={starting} /> Verify X accounts during this scan ({verifyCount} accounts · ≈ {verifyCredits.toLocaleString()} credits)</label>
        {verifyX && <label className="check-row nested"><input type="checkbox" checked={skipProtected} onChange={(e) => setSkipProtected(e.target.checked)} disabled={starting} /> Skip protected members ({estimate.protected_linked})</label>}
        <div className="modal-actions"><button type="button" className="button ghost" onClick={() => setEstimate(undefined)} disabled={starting}>Cancel</button><button className="button primary" onClick={scan} disabled={starting}><Play size={16} />{starting ? 'Starting…' : 'Start scan'}</button></div>
      </div></div>}
      <section className="panel scan-history">
        <div className="panel-head"><div><p className="eyebrow">System activity</p><h2>Recent scan runs</h2></div><Bot size={21} /></div>
        <div className="table-wrap"><table><thead><tr><th>Status</th><th>Window</th><th>Source</th><th>Started</th><th>Matched</th><th>X issues</th><th>Requests</th></tr></thead><tbody>
          {data?.recent_scans.map((item) => <tr key={item.scan_id}><td><Status state={item.status} /></td><td className="mono">{item.period}</td><td>{item.source}</td><td>{formatDate(item.started_at)}</td><td>{String(item.summary?.discovered ?? '—')}</td><td>{Array.isArray(item.summary?.x_unavailable) ? (item.summary.x_unavailable.length ? <a href="#members" className="issue-link">{item.summary.x_unavailable.length} suspended</a> : '0') : '—'}</td><td>{String(item.summary?.api_requests ?? '—')}</td></tr>)}
        </tbody></table></div>
      </section>
    </div>
  )
}
