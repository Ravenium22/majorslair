import { useEffect, useRef, useState } from 'react'
import { Activity, AlertTriangle, ArrowUpRight, Bot, Check, Clock3, LoaderCircle, Play, Radar, RotateCcw, Trophy, Users } from 'lucide-react'
import useSWR from 'swr'
import { api, formatCount, formatDate, formatScore, formatUsd, mutateApi } from '../api'
import { Empty, PageHeader, Status, Toast, useEscape } from '../components'
import type { LinkedUser, LowActivityReport, Overview, ScanEstimate, Session } from '../types'

const WINDOWS = [['cycle', 'Whole cycle'], ['30d', 'Last 30 days'], ['60d', 'Last 60 days'], ['90d', 'Last 90 days'], ['180d', 'Last 6 months'], ['365d', 'Last 12 months']] as const

const PERIOD_LABEL: Record<string, string> = { '24h': 'last 24 hours', '7d': 'last 7 days', '30d': 'last 30 days', '60d': 'last 60 days', '90d': 'last 90 days', '180d': 'last 6 months', '365d': 'last 12 months' }
const periodLabel = (value: string) => PERIOD_LABEL[value] ?? (/^\d{1,3}d$/.test(value) ? `last ${Number(value.slice(0, -1))} days` : value)

export default function OverviewPage({ session }: { session: Session }) {
  const { data, mutate, isLoading } = useSWR<Overview>('/api/overview', api, { refreshInterval: 10000 })
  const [window, setWindow] = useState<(typeof WINDOWS)[number][0]>('cycle')
  const { data: windowed } = useSWR<{ window: string; items: LinkedUser[] }>(window === 'cycle' ? null : `/api/leaderboard?window=${window}&limit=8`, api, { refreshInterval: 30000 })
  const board = window === 'cycle' ? data?.leaderboard ?? [] : windowed?.items ?? []
  // Scaling every bar against the leader made ranks 4-8 identical 3% stubs whenever one
  // member ran away with the cycle. The bars span the range actually on screen instead, and
  // the exact points sit beside every one of them, so nothing is read off the bar alone.
  const boardTop = Math.max(...board.map((u) => u.score), 0)
  const boardFloor = Math.min(...board.map((u) => u.score), boardTop)
  const barWidth = (score: number) =>
    boardTop <= boardFloor ? 100 : 16 + ((score - boardFloor) / (boardTop - boardFloor)) * 84
  const [period, setPeriod] = useState('24h')
  // The backend takes any window from 1 to 366 days, so the presets are a shortcut rather
  // than the whole choice. A cycle that started mid-month needs a number nobody preset.
  const [customDays, setCustomDays] = useState('')
  const periodValid = Boolean(PERIOD_LABEL[period]) || (/^\d{1,3}d$/.test(period) && Number(period.slice(0, -1)) >= 1 && Number(period.slice(0, -1)) <= 366)
  const [notice, setNotice] = useState<{ text: string; kind: 'success' | 'error' | 'loading' }>()
  const [estimate, setEstimate] = useState<ScanEstimate>()
  const [verifyX, setVerifyX] = useState(true)
  const [skipProtected, setSkipProtected] = useState(false)
  const [readTimelines, setReadTimelines] = useState(false)
  const [timelineTweets, setTimelineTweets] = useState(20)
  const timelinePages = Math.min(250, Math.max(1, Math.ceil(timelineTweets / 20)))
  const [starting, setStarting] = useState(false)
  const [showReset, setShowReset] = useState(false)
  const [confirmation, setConfirmation] = useState('')
  const [resetting, setResetting] = useState(false)
  const { data: lowActivity } = useSWR<LowActivityReport>('/api/low-activity', api)
  const [now, setNow] = useState(() => Date.now())
  const running = data?.last_scan?.status === 'running'
  const wasRunning = useRef(false)
  // A long scan used to show nothing but a pulsing dot. Tick a clock while it runs and
  // announce the result once, so leaving the page and coming back still makes sense.
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [running])
  useEffect(() => {
    if (running) { wasRunning.current = true; return }
    if (!wasRunning.current || !data?.last_scan) return
    wasRunning.current = false
    const scan = data.last_scan
    const matched = Number(scan.summary?.discovered ?? 0)
    setNotice(scan.status === 'failed'
      ? { text: `The ${scan.period} scan failed: ${scan.error || 'unknown error'}`, kind: 'error' }
      : { text: `The ${scan.period} scan finished: ${matched} actions matched. Open Scan reports for the full report.`, kind: 'success' })
  }, [running, data?.last_scan])
  const elapsed = data?.last_scan?.started_at ? Math.max(0, Math.floor((now - new Date(data.last_scan.started_at).getTime()) / 1000)) : 0
  const elapsedLabel = elapsed < 60 ? `${elapsed}s` : `${Math.floor(elapsed / 60)} min ${elapsed % 60}s`

  const [loadingEstimate, setLoadingEstimate] = useState(false)

  const openScan = async () => {
    setLoadingEstimate(true)
    try {
      const next = await api<ScanEstimate>(`/api/scans/estimate?period=${encodeURIComponent(period)}`)
      setSkipProtected(next.skip_protected_default)
      setReadTimelines(false)
      setEstimate(next)
    } catch (error) {
      setNotice({ text: error instanceof Error ? error.message : 'Could not prepare the scan', kind: 'error' })
    } finally { setLoadingEstimate(false) }
  }

  const scan = async () => {
    setStarting(true)
    setNotice({ text: `Starting ${period} engagement scan…`, kind: 'loading' })
    try {
      await mutateApi('/api/scans', session.csrf_token, 'POST', { period, verify_x: verifyX, skip_protected: skipProtected, read_timelines: readTimelines, timeline_pages: readTimelines ? timelinePages : undefined })
      setEstimate(undefined)
      setNotice({ text: 'Scan queued. Results will appear here automatically.', kind: 'success' })
      await mutate()
    } catch (error) {
      setNotice({ text: error instanceof Error ? error.message : 'Could not start scan', kind: 'error' })
    } finally { setStarting(false) }
  }

  useEscape(Boolean(estimate) && !starting, () => setEstimate(undefined))
  useEscape(showReset && !resetting, () => setShowReset(false))

  const reset = async () => {
    setResetting(true)
    setNotice({ text: 'Freezing the standings and starting a new cycle…', kind: 'loading' })
    try {
      const result = await mutateApi<{ snapshots: number }>('/api/reset', session.csrf_token, 'POST', { confirmation })
      setNotice({ text: `New cycle started. ${result.snapshots} member standings frozen; open Scan reports to see them.`, kind: 'success' })
      setShowReset(false)
      setConfirmation('')
      await mutate()
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Reset failed', kind: 'error' }) }
    finally { setResetting(false) }
  }

  const cycleStarted = data?.cycle_started_at
  const cycleDays = cycleStarted ? Math.max(0, Math.floor((Date.now() - new Date(cycleStarted).getTime()) / 86400000)) : null
  const lastScan = data?.last_scan
  const scanned = lastScan?.status === 'complete'
  const scannedThisCycle = scanned && (!cycleStarted || new Date(lastScan.started_at) >= new Date(cycleStarted))
  const verifyCount = estimate ? estimate.linked_members - (skipProtected ? estimate.protected_linked : 0) : 0
  const verifyCredits = verifyX ? verifyCount * (estimate?.verification_credits_per_account ?? 10) : 0
  const scanEstimate = estimate && !estimate.estimate.error ? estimate.estimate : undefined
  const timelineMembers = scanEstimate ? scanEstimate.timeline_members - (skipProtected ? Math.min(scanEstimate.timeline_members, estimate?.protected_linked ?? 0) : 0) : 0
  const timelineCredits = readTimelines && scanEstimate ? timelineMembers * timelinePages * 20 * 15 : 0
  // The scan itself, before the two optional extras. The server's high bound already
  // carries a timeline allowance, which we replace with the depth actually chosen here.
  const baseLow = scanEstimate?.credits_low ?? 0
  const baseHigh = Math.max(baseLow, (scanEstimate?.credits_high ?? 0) - (scanEstimate?.timeline_credits_max ?? 0))
  const totalLow = baseLow + verifyCredits + timelineCredits
  const totalHigh = baseHigh + verifyCredits + timelineCredits
  const usd = formatUsd

  const metrics = [
    { label: 'Members scoring', value: data?.linked_members ?? 0, icon: Users, detail: 'have linked an X account' },
    { label: 'Points this cycle', value: formatScore(data?.total_score ?? 0), icon: Trophy, detail: 'awarded since the last reset' },
    { label: 'Actions counted', value: data?.active_actions ?? 0, icon: Activity, detail: 'replies, quotes, retweets, mentions' },
    { label: 'Posts watched', value: data?.tracked_posts ?? 0, icon: Radar, detail: 'from the two tracked accounts' },
  ]

  return (
    <div className="page">
      <PageHeader
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
      <section className="panel cycle-panel">
        <div>
          <h2>This cycle</h2>
          <dl className="cycle-facts">
            <dt>Running since</dt>
            <dd>{cycleStarted ? formatDate(cycleStarted) : 'the beginning'}<small>{cycleDays === null ? 'no reset yet' : `${cycleDays} days`}</small></dd>
            <dt>Points awarded so far</dt>
            <dd>{formatScore(data?.total_score ?? 0)}<small>across {data?.linked_members ?? 0} members with an X account</small></dd>
            <dt>Last scan</dt>
            <dd>{lastScan ? formatDate(lastScan.started_at) : 'never'}<small>{lastScan ? `${lastScan.period} window · ${String(lastScan.summary?.discovered ?? 0)} actions matched` : 'run one below'}</small></dd>
          </dl>
        </div>
        <div>
          <h2 className="sub-heading">The monthly round</h2>
          <ol className="cycle-steps">
            <li className={scannedThisCycle ? 'done' : ''}>Scan the window {scannedThisCycle && <small>done {formatDate(lastScan?.started_at)}</small>}</li>
            <li className={scannedThisCycle ? 'done' : ''}>Check what moved <a href="#scans">Scan reports</a></li>
            <li>Review who is inactive {lowActivity && <small>{lowActivity.items.length} on the list</small>}<a href="#low-activity">Low-activity report</a></li>
            <li>Reward the top <a href="#members">Give role</a></li>
            <li>Start the next cycle {cycleDays !== null && cycleDays >= 28 && <small>due</small>}<button className="link-button" onClick={() => setShowReset(true)}>Reset the leaderboard</button></li>
          </ol>
        </div>
      </section>
      <section className="overview-grid">
        <article className="panel leaderboard-panel">
          <div className="panel-head"><div><h2>Top contributors</h2></div><div className="panel-tools"><select value={window} onChange={(e) => setWindow(e.target.value as typeof window)} aria-label="Leaderboard window">{WINDOWS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select><a href="#members">All members <ArrowUpRight size={15} /></a></div></div>
          {board.length ? (
            <div className="leader-list">
              {board.map((user, index) => (
                <div className="leader-row" key={user.discord_user_id}>
                  <span className={`rank rank-${index + 1}`}>{String(index + 1).padStart(2, '0')}</span>
                  <div className="leader-avatar">{user.discord_username.slice(0, 1).toUpperCase()}</div>
                  <span className="member-cell"><strong>{user.discord_username}</strong><small>@{user.twitter_handle}</small></span>
                  <div className="score-bar" aria-hidden="true"><i style={{ width: `${barWidth(user.score)}%` }} /></div>
                  <strong className="score">{formatScore(user.score)}</strong>
                </div>
              ))}
            </div>
          ) : <Empty title="No leaderboard signal yet" copy={window === 'cycle' ? 'Members appear here after linking an X account and completing a scan.' : 'No scored activity in this window yet. Points only appear for periods a scan has covered.'} />}
          {window !== 'cycle' && <p className="muted small window-note">Points earned on activity in the {WINDOWS.find(([id]) => id === window)?.[1].toLowerCase()}, taken from scans already run. Run a scan covering that window first if it looks empty.</p>}
        </article>
        <aside className="scan-card">
          <h2>Run a scan</h2>
          <p>Collect recent replies, quotes, retweets, and organic mentions from tracked accounts.</p>
          <label>Lookback window<select value={PERIOD_LABEL[period] ? period : 'custom'} onChange={(event) => { const next = event.target.value; if (next === 'custom') { const days = customDays || String(cycleDays && cycleDays > 0 ? cycleDays : 30); setCustomDays(days); setPeriod(`${days}d`) } else { setPeriod(next) } }}><option value="24h">Last 24 hours</option><option value="7d">Last 7 days</option><option value="30d">Last 30 days</option><option value="60d">Last 60 days</option><option value="90d">Last 90 days</option><option value="180d">Last 6 months</option><option value="365d">Last 12 months</option><option value="custom">An exact number of days…</option></select></label>
          {!PERIOD_LABEL[period] && <label className="custom-days">Days to look back<input type="number" min={1} max={366} step={1} inputMode="numeric" value={customDays} onChange={(event) => { const days = event.target.value.replace(/[^0-9]/g, '').slice(0, 3); setCustomDays(days); setPeriod(days ? `${days}d` : '') }} /><small className="field-hint">1 to 366. {cycleDays !== null && cycleDays > 0 ? `This cycle started ${cycleDays} day${cycleDays === 1 ? '' : 's'} ago, so ${cycleDays} covers all of it.` : 'Covers the whole cycle when it is at least as long as the cycle.'}</small></label>}
          <p className="field-hint window-clamp">A scan never reaches back past the start of the current cycle, so a window longer than the cycle simply covers the whole cycle. Points from before the last reset cannot be counted twice.</p>
          <button className="button primary" onClick={openScan} disabled={running || loadingEstimate || !periodValid} title={periodValid ? undefined : 'Enter a number of days between 1 and 366'}><Play size={17} />{running ? 'Scan running' : loadingEstimate ? 'Estimating cost…' : 'Run engagement scan'}</button>
          {running
            ? <div className="scan-progress"><p><LoaderCircle className="spin" size={15} /> Scanning the {data?.last_scan?.period} window · {elapsedLabel} so far</p><small>A long window can take 15 minutes. You can leave this page; the report appears under Scan reports when it finishes.</small></div>
            : <small><Clock3 size={13} /> {data?.last_scan?.status === 'failed' ? 'Last scan failed' : 'Last completed'} {formatDate(data?.last_scan?.completed_at)}{data?.last_scan?.status === 'complete' && <> · <Check size={12} /> {String(data.last_scan.summary?.discovered ?? 0)} actions matched</>}</small>}
        </aside>
      </section>
      {estimate && <div className="modal-backdrop" onMouseDown={() => { if (!starting) setEstimate(undefined) }}><div className="modal" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-icon"><Play /></div><h2>Scan the {periodLabel(period || estimate.period)}</h2>
        <p>The bot collects replies, quotes, retweets, and mentions on the tracked accounts' posts, then scores every linked member who shows up. Cost depends on how many posts and replies there are, not on the member count.</p>
        <dl className="estimate-grid" data-dialog-focus tabIndex={-1} aria-live="polite">
          <div><dt>Members who will be scored</dt><dd>{estimate.linked_members - (skipProtected ? estimate.protected_linked : 0)}<small>{skipProtected ? `${estimate.protected_linked} protected skipped` : `${estimate.protected_linked} of them protected`} · {estimate.unlinked_members} without X</small></dd></div>
          <div><dt>Posts in this window</dt><dd>{scanEstimate ? scanEstimate.source_posts : '—'}<small>{scanEstimate ? `${formatCount(scanEstimate.engagement_items)} replies, quotes & retweets to read` : estimate.estimate.error ?? 'could not read the tracked accounts'}</small></dd></div>
          <div className="wide cost-cell"><dt>This scan will cost at most</dt><dd>{scanEstimate ? usd(totalHigh) : '—'}<small>{scanEstimate ? `${usd(totalLow)} if the feeds are short · ${formatCount(totalLow)} to ${formatCount(totalHigh)} credits` : ''}{estimate.previous_scan?.credits != null ? ` · the last ${estimate.period} scan really cost ${usd(estimate.previous_scan.credits)}` : ''}</small></dd></div>
        </dl>
        {scanEstimate && <p className="cost-breakdown">Made up of: posts and engagement {formatCount(scanEstimate.engagement_credits + scanEstimate.source_credits)} cr · mentions up to {formatCount(scanEstimate.mentions_credits_max)} cr · hidden-reply sweep up to {formatCount(scanEstimate.sweep_credits_max)} cr{verifyX ? <> · X checks {formatCount(verifyCredits)} cr</> : null}{readTimelines ? <> · member timelines up to {formatCount(timelineCredits)} cr</> : null}</p>}
        {scanEstimate?.warnings.length ? <p className="estimate-warning">{scanEstimate.warnings.join(' ')}</p> : null}
        <label className="check-row"><input type="checkbox" checked={skipProtected} onChange={(e) => setSkipProtected(e.target.checked)} disabled={starting} /> Skip protected members ({estimate.protected_linked}): not scored, not X-checked. Their existing points stay as they are.</label>
        <label className="check-row"><input type="checkbox" checked={verifyX} onChange={(e) => setVerifyX(e.target.checked)} disabled={starting} /> Verify X accounts during this scan ({verifyCount} accounts)<strong className="cost-delta">{verifyX ? `included: ${usd(verifyCount * (estimate.verification_credits_per_account ?? 10))}` : `adds ${usd(verifyCount * (estimate.verification_credits_per_account ?? 10))}`}</strong></label>
        <label className="check-row"><input type="checkbox" checked={readTimelines} onChange={(e) => setReadTimelines(e.target.checked)} disabled={starting} /> Deep check: also read every member's own timeline to catch replies X hides everywhere else ({timelineMembers} members). Off again next time.<strong className="cost-delta">{readTimelines ? `included: up to ${usd(timelineCredits)}` : `adds up to ${usd(timelineMembers * timelinePages * 20 * 15)}`}</strong></label>
        {readTimelines && <div className="check-row nested depth-row"><span>Latest tweets per member:</span><div className="segmented">{[20, 100, 300, 500, 1000].map((n) => <button type="button" key={n} className={timelineTweets === n ? 'active' : ''} onClick={() => setTimelineTweets(n)} disabled={starting}>{formatCount(n)}</button>)}</div><label className="depth-custom">custom<input type="number" min={20} max={5000} step={20} value={timelineTweets} onChange={(e) => setTimelineTweets(Math.min(5000, Math.max(20, Number(e.target.value) || 20)))} disabled={starting} /></label><span className="muted">{usd(timelineCredits)} at most, less when it reaches the window start first</span></div>}
        <div className="modal-actions"><button type="button" className="button ghost" onClick={() => setEstimate(undefined)} disabled={starting}>Cancel</button><button className="button primary" onClick={scan} disabled={starting}><Play size={16} />{starting ? 'Starting…' : 'Start scan'}</button></div>
      </div></div>}
      {showReset && <div className="modal-backdrop" onMouseDown={() => { if (!resetting) setShowReset(false) }}><div className="modal" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-icon danger-icon"><AlertTriangle /></div><h2>Start a new cycle?</h2>
        <p>This freezes where everyone stands right now and sets every score back to zero. Nothing is deleted: the frozen table stays under Scan reports forever, and so does every scan report.</p>
        <dl className="cycle-facts freeze-summary">
          <dt>About to be frozen</dt>
          <dd>{data?.linked_members ?? 0} members · {formatScore(data?.total_score ?? 0)} points<small>{data?.leaderboard?.length ? `led by ${data.leaderboard.slice(0, 3).map((u) => `${u.discord_username} (${formatScore(u.score)})`).join(', ')}` : 'no standings yet'}</small></dd>
          <dt>Cycle being closed</dt>
          <dd className="mono">{data?.cycle_id || '—'}<small>{cycleStarted ? `started ${formatDate(cycleStarted)}` : 'the first cycle'}</small></dd>
        </dl>
        <label>Type RESET LEADERBOARD to confirm<input autoFocus value={confirmation} onChange={(e) => setConfirmation(e.target.value)} placeholder="RESET LEADERBOARD" disabled={resetting} /></label>
        <div className="modal-actions"><button className="button ghost" onClick={() => setShowReset(false)} disabled={resetting}>Cancel</button><button className="button danger" disabled={confirmation !== 'RESET LEADERBOARD' || resetting} onClick={reset}><RotateCcw size={16} /> {resetting ? 'Freezing…' : 'Freeze and start a new cycle'}</button></div>
      </div></div>}
      <section className="panel scan-history">
        <div className="panel-head"><div><h2>Recent scans</h2></div><Bot size={21} /></div>
        <div className="table-wrap"><table><thead><tr><th>Status</th><th>Window</th><th>Source</th><th>Started</th><th>Matched</th><th>X issues</th><th>Requests</th></tr></thead><tbody>
          {data?.recent_scans.map((item) => <tr key={item.scan_id}><td><Status state={item.status} /></td><td className="mono">{item.period}</td><td>{item.source}</td><td>{formatDate(item.started_at)}</td><td>{String(item.summary?.discovered ?? '—')}</td><td>{Array.isArray(item.summary?.x_unavailable) ? (item.summary.x_unavailable.length ? <a href="#members?view=xissues" className="issue-link">{item.summary.x_unavailable.length} suspended</a> : '0') : '—'}</td><td>{String(item.summary?.api_requests ?? '—')}</td></tr>)}
        </tbody></table></div>
      </section>
    </div>
  )
}
