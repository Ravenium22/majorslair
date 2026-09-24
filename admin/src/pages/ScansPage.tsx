import { Fragment, useState, type ReactNode } from 'react'
import { ChevronDown, ChevronRight, Download, ScrollText } from 'lucide-react'
import useSWR from 'swr'
import { api, formatCount, formatDate, formatScore, formatUsd } from '../api'
import { Empty, Loading, PageHeader, Pagination, Status } from '../components'
import type { LowActivityReport, Paginated, ScanRun, Snapshot } from '../types'

const PAGE_SIZE = 25

function credits(summary: ScanRun['summary']) {
  const items = Number(summary.tweets_returned ?? 0)
  const requests = Number(summary.api_requests ?? 0)
  const checked = Number(summary.x_checked ?? 0)
  return Math.max(items, requests) * 15 + checked * 10
}

const csvCell = (value: unknown) => { const text = String(value ?? ''); return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text }

function downloadStandings(run: ScanRun) {
  const rows = run.summary.standings ?? []
  const header = ['rank', 'discord_username', 'discord_id', 'x_handle', 'points', 'points_before_scan', 'change', 'protected', 'x_status']
  const lines = rows.map((row, index) => [index + 1, row.discord_username, row.discord_user_id, row.twitter_handle, row.score, row.before, +(row.score - row.before).toFixed(2), row.special_role ? 'YES' : 'NO', row.x_status || 'ok'].map(csvCell).join(','))
  const blob = new Blob(['﻿' + [header.join(','), ...lines].join('\r\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `majors-lair-points-${run.period}-${run.started_at.slice(0, 16).replace(/[:T]/g, '-')}.csv`
  anchor.click()
  URL.revokeObjectURL(url)
}

function duration(run: ScanRun) {
  if (!run.completed_at) return '—'
  const seconds = Math.max(0, (new Date(run.completed_at).getTime() - new Date(run.started_at).getTime()) / 1000)
  return seconds < 90 ? `${Math.round(seconds)}s` : `${Math.round(seconds / 60)} min`
}

function downloadSnapshot(snapshot: Snapshot) {
  const header = ['rank', 'discord_username', 'discord_id', 'x_handle', 'points']
  const lines = snapshot.members.map((m) => [m.rank, m.discord_username, m.discord_user_id, m.twitter_handle, m.score].map(csvCell).join(','))
  const blob = new Blob(['\uFEFF' + [header.join(','), ...lines].join('\r\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `majors-lair-reset-${snapshot.reset_at.slice(0, 10)}.csv`
  anchor.click()
  URL.revokeObjectURL(url)
}

type Moved = { id: string; name: string; before: number; after: number; delta: number; protectedMember: boolean }

/** What a scan changed, which is what gets checked after one: who went up, who went down,
 *  and who crossed the low-activity line in either direction. Worked out from the standings
 *  the scan saved (points before and after for everyone), or from its list of changes on
 *  scans that predate the standings. */
function WhatChanged({ run, threshold }: { run: ScanRun; threshold?: number }) {
  const s = run.summary ?? {}
  const standings = Array.isArray(s.standings) ? s.standings : []
  const fallback = Array.isArray(s.score_changes) ? s.score_changes : []
  const moved: Moved[] = standings.length
    ? standings.map((row) => ({ id: row.discord_user_id, name: row.discord_username, before: Number(row.before), after: Number(row.score), delta: Number(row.score) - Number(row.before), protectedMember: Boolean(row.special_role) }))
    : fallback.map((row) => ({ id: String(row.discord_user_id), name: String(row.discord_username), before: Number(row.before), after: Number(row.after), delta: Number(row.after) - Number(row.before), protectedMember: false }))
  const gained = moved.filter((m) => m.delta > 0).sort((a, b) => b.delta - a.delta)
  const lost = moved.filter((m) => m.delta < 0).sort((a, b) => a.delta - b.delta)
  const crossedDown = threshold === undefined || !standings.length ? [] : moved.filter((m) => !m.protectedMember && m.before > threshold && m.after <= threshold)
  const crossedUp = threshold === undefined || !standings.length ? [] : moved.filter((m) => m.before <= threshold && m.after > threshold)
  const person = (m: Moved, value: number) => <li key={m.id}><a href={`#member?id=${m.id}`}>{m.name}</a><span className={`score ${value >= 0 ? 'gain' : 'loss'}`}>{value > 0 ? '+' : ''}{formatScore(value)}</span></li>
  const list = (items: Moved[], render: (m: Moved) => ReactNode, limit = 8) => <ul className="moved-list">{items.slice(0, limit).map(render)}{items.length > limit && <li className="more">and {items.length - limit} more</li>}</ul>

  if (!moved.length) return <p className="changed-lead">Nobody's points changed in this scan.</p>
  return <section className="what-changed">
    <p className="changed-lead">
      <strong>{gained.length}</strong> member{gained.length === 1 ? '' : 's'} gained points{lost.length ? <>, <strong>{lost.length}</strong> lost some</> : null}
      {threshold !== undefined && standings.length ? <>. At today's threshold of {formatScore(threshold)} points, <strong>{crossedDown.length}</strong> dropped onto the low-activity list and <strong>{crossedUp.length}</strong> climbed off it.</> : '.'}
    </p>
    <div className="changed-grid">
      {gained.length > 0 && <div><h4>Gained the most</h4>{list(gained, (m) => person(m, m.delta))}</div>}
      {lost.length > 0 && <div><h4>Lost points</h4><p className="changed-note">Usually a tweet that was deleted or made private.</p>{list(lost, (m) => person(m, m.delta))}</div>}
      {crossedDown.length > 0 && <div><h4>Dropped onto the low-activity list</h4>{list(crossedDown, (m) => person(m, m.after))}</div>}
      {crossedUp.length > 0 && <div><h4>Climbed off it</h4>{list(crossedUp, (m) => person(m, m.after))}</div>}
    </div>
  </section>
}

export default function ScansPage() {
  const [page, setPage] = useState(1)
  const [open, setOpen] = useState<string>()
  const [openSnapshot, setOpenSnapshot] = useState<string>()
  const { data: snapshots } = useSWR<Snapshot[]>('/api/snapshots', api)
  const { data, isLoading } = useSWR<Paginated<ScanRun>>(`/api/scan-runs?page=${page}&page_size=${PAGE_SIZE}`, api, { refreshInterval: 15000 })
  const { data: lowActivity } = useSWR<LowActivityReport>('/api/low-activity', api)

  return <div className="page">
    <PageHeader title="Scan reports" copy="Every engagement scan ever run, with what it matched, what it cost, which X accounts could not be verified, and who gained points. Reports are never deleted, not even by a leaderboard reset." />
    <section className="panel">
      <div className="table-wrap"><table className="scan-table"><thead><tr><th /><th>Status</th><th>Window</th><th>Started</th><th>Took</th><th>Run from</th><th>Posts</th><th>Matched</th><th>Points moved</th><th>X issues</th><th>≈ Cost</th></tr></thead><tbody>
        {data?.items.map((run) => {
          const s = run.summary ?? {}
          const changes = Array.isArray(s.score_changes) ? s.score_changes : []
          const unavailable = Array.isArray(s.x_unavailable) ? s.x_unavailable : []
          const renamed = Array.isArray(s.x_renamed) ? s.x_renamed : []
          const warnings = Array.isArray(s.warnings) ? (s.warnings as string[]) : []
          const standings = Array.isArray(s.standings) ? s.standings : []
          const cost = credits(s)
          const expanded = open === run.scan_id
          return <Fragment key={run.scan_id}>
            <tr className={`scan-row ${expanded ? 'open' : ''}`} tabIndex={0} role="button" aria-expanded={expanded} aria-label={`${run.period} scan from ${formatDate(run.started_at)}`} onClick={() => setOpen(expanded ? undefined : run.scan_id)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen(expanded ? undefined : run.scan_id) } }}>
              <td className="chev">{expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</td>
              <td><Status state={run.status} /></td>
              <td className="mono">{run.period}</td>
              <td>{formatDate(run.started_at)}</td>
              <td>{duration(run)}</td>
              <td>{run.source === 'member' ? <>Member scan<small className="block">{String(s.discord_username ?? '')}</small></> : <>{run.source === 'admin' ? 'Dashboard' : 'Discord'}<small className="block">{run.triggered_by_name || run.triggered_by}</small></>}</td>
              <td>{String(s.source_posts ?? '—')}</td>
              <td>{String(s.discovered ?? '—')}</td>
              <td>{(() => { const moved = standings.length ? standings.filter((row) => Number(row.score) !== Number(row.before)).length : changes.length; return run.status === 'complete' ? (moved ? `${moved} members` : '0') : '—' })()}</td>
              <td>{unavailable.length ? <span className="status failed"><i />{unavailable.length}</span> : run.status === 'complete' && s.x_checked ? '0' : '—'}</td>
              <td>{run.status === 'complete' ? `${formatUsd(cost)} · ${formatCount(cost)} cr` : '—'}</td>
            </tr>
            {expanded && <tr className="scan-detail"><td colSpan={11}>
              {run.error && <p className="estimate-warning">Failed: {run.error}</p>}
              {!s.member_scan && run.status === 'complete' && <WhatChanged run={run} threshold={lowActivity?.threshold} />}
              {s.member_scan ? <div className="report-grid"><section><h4>Single-member scan · {String(s.discord_username ?? '')} (@{String(s.twitter_handle ?? '')})</h4><dl>
                <div><dt>Tweets read</dt><dd>{String(s.tweets_read ?? 0)}</dd></div>
                <div><dt>From the timeline feed</dt><dd>{String(s.timeline_read ?? s.tweets_read ?? 0)}{s.timeline_ended_early ? <small>feed stopped at {s.timeline_ended_at ? formatDate(String(s.timeline_ended_at)) : 'an earlier date'}; search added {String(s.search_filled ?? 0)}</small> : null}</dd></div>
                <div><dt>Matched</dt><dd>{String(s.matched ?? 0)}<small>{String(s.replies ?? 0)} replies · {String(s.quotes ?? 0)} quotes · {String(s.mentions ?? 0)} mentions</small></dd></div>
                <div><dt>New actions</dt><dd>{String(s.new_actions ?? 0)}</dd></div>
                <div><dt>Points</dt><dd>{formatScore(Number(s.points_before ?? 0))} → {formatScore(Number(s.points_after ?? 0))}</dd></div>
                <div><dt>Approx. cost</dt><dd>{formatUsd(cost)} <small>{formatCount(cost)} credits</small></dd></div>
              </dl></section></div> : <details className="report-details"><summary>What the scan read, and what it cost</summary><div className="report-grid">
                <section>
                  <h4>Matched this scan</h4>
                  <dl>
                    <div><dt>Replies</dt><dd>{String(s.replies ?? 0)}</dd></div>
                    <div><dt>Quotes</dt><dd>{String(s.quotes ?? 0)}</dd></div>
                    <div><dt>Retweets</dt><dd>{String(s.retweets ?? 0)}</dd></div>
                    <div><dt>Mentions</dt><dd>{String(s.mentions ?? 0)}</dd></div>
                    <div><dt>Replies found only by sweep</dt><dd>{String(s.swept_replies ?? 0)}</dd></div>
                    <div><dt>Replies found only in member timelines</dt><dd>{String(s.timeline_replies ?? 0)}<small>{s.timeline_members_checked ? ` of ${String(s.timeline_members_checked)} members checked` : ''}</small></dd></div>
                    <div><dt>Source posts</dt><dd>{String(s.source_posts ?? 0)}</dd></div>
                    <div><dt>Log entries changed</dt><dd>{String(s.changed_actions ?? 0)}</dd></div>
                    <div><dt>Actions from unlinked people</dt><dd>{String(s.skipped_unlinked ?? 0)}</dd></div>
                    <div><dt>Protected members skipped</dt><dd>{String(s.skipped_protected ?? 0)}</dd></div>
                  </dl>
                </section>
                <section>
                  <h4>Cost & coverage</h4>
                  <dl>
                    <div><dt>API requests</dt><dd>{String(s.api_requests ?? 0)}</dd></div>
                    <div><dt>Items returned</dt><dd>{String(s.tweets_returned ?? 0)}</dd></div>
                    <div><dt>Approx. cost</dt><dd>{formatUsd(cost)} <small>{formatCount(cost)} credits</small></dd></div>
                    <div><dt>Capped scopes</dt><dd>{String(s.incomplete_scopes ?? 0)}</dd></div>
                    <div><dt>X accounts checked</dt><dd>{String(s.x_checked ?? 0)}</dd></div>
                    <div><dt>Renamed on X</dt><dd>{renamed.length}</dd></div>
                  </dl>
                </section>
              </div></details>}
              {unavailable.length > 0 && <section className="report-block"><h4>Could not verify these X accounts</h4><div className="table-wrap"><table><tbody>{unavailable.map((item) => <tr key={String(item.discord_user_id)}><td><span className="member-cell"><strong>{String(item.discord_username)}</strong><small className="mono">{String(item.discord_user_id)}</small></span></td><td><a href={`https://x.com/${String(item.twitter_handle)}`} target="_blank">@{String(item.twitter_handle)}</a></td><td><span className="status failed"><i />{String(item.status)}</span></td><td className="muted">{String(item.reason ?? '')}</td></tr>)}</tbody></table></div></section>}
              {renamed.length > 0 && <section className="report-block"><h4>Handles updated automatically</h4><div className="table-wrap"><table><tbody>{renamed.map((item) => <tr key={String(item.discord_user_id)}><td><span className="member-cell"><strong>{String(item.discord_username)}</strong><small className="mono">{String(item.discord_user_id)}</small></span></td><td className="muted">@{String(item.old_handle)} → @{String(item.new_handle)}</td></tr>)}</tbody></table></div></section>}
              {standings.length > 0 && <details className="report-details"><summary>Everyone's points after this scan ({standings.length} members)</summary><div className="report-head"><span /><button className="button" onClick={(e) => { e.stopPropagation(); downloadStandings(run) }}><Download size={15} /> Download sheet (CSV)</button></div><div className="table-wrap standings-table"><table><thead><tr><th>#</th><th>Discord</th><th>X</th><th>Points</th><th>This scan</th><th>Protected</th></tr></thead><tbody>{standings.map((row, index) => { const delta = row.score - row.before; return <tr key={row.discord_user_id}><td className="mono">{index + 1}</td><td><a className="member-cell linked-cell" href={`#member?id=${row.discord_user_id}`}><strong>{row.discord_username}</strong><small className="mono">{row.discord_user_id}</small></a></td><td>{row.twitter_handle ? <a href={`https://x.com/${row.twitter_handle}`} target="_blank" rel="noreferrer">@{row.twitter_handle}</a> : <span className="muted">not linked</span>}</td><td className="score">{formatScore(row.score)}</td><td className={`score ${delta > 0 ? 'gain' : delta < 0 ? 'loss' : 'muted'}`}>{delta === 0 ? '—' : `${delta > 0 ? '+' : ''}${formatScore(delta)}`}</td><td>{row.special_role ? <span className="status complete"><i />Yes</span> : <span className="muted">—</span>}</td></tr> })}</tbody></table></div></details>}
              {warnings.length > 0 && <section className="report-block"><h4>Warnings</h4><ul className="warning-list">{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></section>}
            </td></tr>}
          </Fragment>
        })}
      </tbody></table></div>
      {isLoading && !data && <Loading label="Loading scan reports…" />}
      {!isLoading && !data?.items.length && <Empty title="No scans yet" copy="Run an engagement scan from the Overview page or with /check-engagement in Discord." />}
      <Pagination page={page} size={PAGE_SIZE} total={data?.total ?? 0} onChange={setPage} />
    </section>
    <p className="muted small page-note"><ScrollText size={13} /> Cost is approximate: 15 credits per item or request, 10 per X account check.</p>
    <section className="panel reset-panel">
      <div className="panel-head"><div><h2>Frozen standings from every reset</h2></div></div>
      {snapshots?.length ? <div className="table-wrap"><table><thead><tr><th /><th>Reset on</th><th>Cycle closed</th><th>Members</th><th>Top member</th><th>Reset by</th><th /></tr></thead><tbody>
        {snapshots.map((snap) => { const expanded = openSnapshot === snap.snapshot_id; return <Fragment key={snap.snapshot_id}>
          <tr className={`scan-row ${expanded ? 'open' : ''}`} tabIndex={0} role="button" aria-expanded={expanded} onClick={() => setOpenSnapshot(expanded ? undefined : snap.snapshot_id)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpenSnapshot(expanded ? undefined : snap.snapshot_id) } }}>
            <td className="chev">{expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</td>
            <td>{formatDate(snap.reset_at)}</td><td className="mono">{snap.cycle_id}</td><td>{snap.members.length}</td>
            <td>{snap.members[0] ? `${snap.members[0].discord_username} · ${formatScore(snap.members[0].score)} pts` : '—'}</td>
            <td className="mono">{snap.reset_by_discord_id}</td>
            <td><button className="button" onClick={(e) => { e.stopPropagation(); downloadSnapshot(snap) }}><Download size={15} /> CSV</button></td>
          </tr>
          {expanded && <tr className="scan-detail"><td colSpan={7}><div className="table-wrap standings-table"><table><thead><tr><th>#</th><th>Discord</th><th>X</th><th>Points</th></tr></thead><tbody>{snap.members.map((m) => <tr key={m.discord_user_id}><td className="mono">{m.rank}</td><td><span className="member-cell"><strong>{m.discord_username}</strong><small className="mono">{m.discord_user_id}</small></span></td><td>{m.twitter_handle ? <a href={`https://x.com/${m.twitter_handle}`} target="_blank">@{m.twitter_handle}</a> : <span className="muted">—</span>}</td><td className="score">{formatScore(m.score)}</td></tr>)}</tbody></table></div></td></tr>}
        </Fragment> })}
      </tbody></table></div> : <Empty title="No resets yet" copy="When an admin runs /reset-leaderboard, or starts a new cycle from the Overview page, the standings at that moment are frozen here forever." />}
    </section>
  </div>
}
