import { Fragment, useState } from 'react'
import { ChevronDown, ChevronRight, Download, ScrollText } from 'lucide-react'
import useSWR from 'swr'
import { api, formatDate, formatScore } from '../api'
import { Empty, PageHeader, Pagination, Status } from '../components'
import type { Paginated, ScanRun } from '../types'

const PAGE_SIZE = 25
const CREDIT_USD = 1 / 100000

function credits(summary: ScanRun['summary']) {
  const items = Number(summary.tweets_returned ?? 0)
  const requests = Number(summary.api_requests ?? 0)
  const checked = Number(summary.x_checked ?? 0)
  return Math.max(items, requests) * 15 + checked * 10
}

const csvCell = (value: unknown) => { const text = String(value ?? ''); return /[",
]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text }

function downloadStandings(run: ScanRun) {
  const rows = run.summary.standings ?? []
  const header = ['rank', 'discord_username', 'discord_id', 'x_handle', 'points', 'points_before_scan', 'change', 'protected', 'x_status']
  const lines = rows.map((row, index) => [index + 1, row.discord_username, row.discord_user_id, row.twitter_handle, row.score, row.before, +(row.score - row.before).toFixed(2), row.special_role ? 'YES' : 'NO', row.x_status || 'ok'].map(csvCell).join(','))
  const blob = new Blob(['﻿' + [header.join(','), ...lines].join('
')], { type: 'text/csv;charset=utf-8' })
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

export default function ScansPage() {
  const [page, setPage] = useState(1)
  const [open, setOpen] = useState<string>()
  const { data, isLoading } = useSWR<Paginated<ScanRun>>(`/api/scan-runs?page=${page}&page_size=${PAGE_SIZE}`, api, { refreshInterval: 15000 })

  return <div className="page">
    <PageHeader eyebrow="Permanent record" title="Scan reports" copy="Every engagement scan ever run, with what it matched, what it cost, which X accounts could not be verified, and who gained points. Reports are never deleted, not even by a leaderboard reset." />
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
            <tr className={`scan-row ${expanded ? 'open' : ''}`} onClick={() => setOpen(expanded ? undefined : run.scan_id)}>
              <td className="chev">{expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</td>
              <td><Status state={run.status} /></td>
              <td className="mono">{run.period}</td>
              <td>{formatDate(run.started_at)}</td>
              <td>{duration(run)}</td>
              <td>{run.source === 'admin' ? 'Dashboard' : 'Discord'}<small className="block mono">{run.triggered_by}</small></td>
              <td>{String(s.source_posts ?? '—')}</td>
              <td>{String(s.discovered ?? '—')}</td>
              <td>{changes.length ? `${changes.length} members` : run.status === 'complete' ? '0' : '—'}</td>
              <td>{unavailable.length ? <span className="status failed"><i />{unavailable.length}</span> : run.status === 'complete' && s.x_checked ? '0' : '—'}</td>
              <td>{run.status === 'complete' ? `${cost.toLocaleString()} cr · $${(cost * CREDIT_USD).toFixed(2)}` : '—'}</td>
            </tr>
            {expanded && <tr className="scan-detail"><td colSpan={11}>
              {run.error && <p className="estimate-warning">Failed: {run.error}</p>}
              <div className="report-grid">
                <section>
                  <h4>Matched this scan</h4>
                  <dl>
                    <div><dt>Replies</dt><dd>{String(s.replies ?? 0)}</dd></div>
                    <div><dt>Quotes</dt><dd>{String(s.quotes ?? 0)}</dd></div>
                    <div><dt>Retweets</dt><dd>{String(s.retweets ?? 0)}</dd></div>
                    <div><dt>Mentions</dt><dd>{String(s.mentions ?? 0)}</dd></div>
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
                    <div><dt>Approx. credits</dt><dd>{cost.toLocaleString()} <small>(${(cost * CREDIT_USD).toFixed(3)})</small></dd></div>
                    <div><dt>Capped scopes</dt><dd>{String(s.incomplete_scopes ?? 0)}</dd></div>
                    <div><dt>X accounts checked</dt><dd>{String(s.x_checked ?? 0)}</dd></div>
                    <div><dt>Renamed on X</dt><dd>{renamed.length}</dd></div>
                  </dl>
                </section>
              </div>
              {unavailable.length > 0 && <section className="report-block"><h4>Could not verify these X accounts</h4><div className="table-wrap"><table><tbody>{unavailable.map((item) => <tr key={String(item.discord_user_id)}><td><span className="member-cell"><strong>{String(item.discord_username)}</strong><small className="mono">{String(item.discord_user_id)}</small></span></td><td><a href={`https://x.com/${String(item.twitter_handle)}`} target="_blank">@{String(item.twitter_handle)}</a></td><td><span className="status failed"><i />{String(item.status)}</span></td><td className="muted">{String(item.reason ?? '')}</td></tr>)}</tbody></table></div></section>}
              {renamed.length > 0 && <section className="report-block"><h4>Handles updated automatically</h4><div className="table-wrap"><table><tbody>{renamed.map((item) => <tr key={String(item.discord_user_id)}><td><span className="member-cell"><strong>{String(item.discord_username)}</strong><small className="mono">{String(item.discord_user_id)}</small></span></td><td className="muted">@{String(item.old_handle)} → @{String(item.new_handle)}</td></tr>)}</tbody></table></div></section>}
              {changes.length > 0 && <section className="report-block"><h4>Points moved by this scan</h4><div className="table-wrap"><table><thead><tr><th>Member</th><th>X</th><th>Before</th><th>After</th><th>Change</th></tr></thead><tbody>{changes.map((item) => { const delta = Number(item.after) - Number(item.before); return <tr key={String(item.discord_user_id)}><td><span className="member-cell"><strong>{String(item.discord_username)}</strong><small className="mono">{String(item.discord_user_id)}</small></span></td><td>{item.twitter_handle ? <a href={`https://x.com/${String(item.twitter_handle)}`} target="_blank">@{String(item.twitter_handle)}</a> : <span className="muted">—</span>}</td><td className="score">{formatScore(Number(item.before))}</td><td className="score">{formatScore(Number(item.after))}</td><td className={`score ${delta >= 0 ? 'gain' : 'loss'}`}>{delta >= 0 ? '+' : ''}{formatScore(delta)}</td></tr> })}</tbody></table></div>{Number(s.score_changes_total ?? changes.length) > changes.length && <p className="muted small">Showing the {changes.length} largest of {String(s.score_changes_total)} changes.</p>}</section>}
              {standings.length > 0 && <section className="report-block"><div className="report-head"><h4>Everyone's points after this scan ({standings.length} members)</h4><button className="button" onClick={(e) => { e.stopPropagation(); downloadStandings(run) }}><Download size={15} /> Download sheet (CSV)</button></div><div className="table-wrap standings-table"><table><thead><tr><th>#</th><th>Discord</th><th>X</th><th>Points</th><th>This scan</th><th>Protected</th></tr></thead><tbody>{standings.map((row, index) => { const delta = row.score - row.before; return <tr key={row.discord_user_id}><td className="mono">{index + 1}</td><td><span className="member-cell"><strong>{row.discord_username}</strong><small className="mono">{row.discord_user_id}</small></span></td><td>{row.twitter_handle ? <a href={`https://x.com/${row.twitter_handle}`} target="_blank">@{row.twitter_handle}</a> : <span className="muted">not linked</span>}</td><td className="score">{formatScore(row.score)}</td><td className={`score ${delta > 0 ? 'gain' : delta < 0 ? 'loss' : 'muted'}`}>{delta === 0 ? '—' : `${delta > 0 ? '+' : ''}${formatScore(delta)}`}</td><td>{row.special_role ? <span className="status complete"><i />Yes</span> : <span className="muted">—</span>}</td></tr> })}</tbody></table></div></section>}
              {warnings.length > 0 && <section className="report-block"><h4>Warnings</h4><ul className="warning-list">{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></section>}
            </td></tr>}
          </Fragment>
        })}
      </tbody></table></div>
      {!isLoading && !data?.items.length && <Empty title="No scans yet" copy="Run an engagement scan from the Overview page or with /check-engagement in Discord." />}
      <Pagination page={page} size={PAGE_SIZE} total={data?.total ?? 0} onChange={setPage} />
    </section>
    <p className="muted small page-note"><ScrollText size={13} /> Cost is approximate: 15 credits per item or request, 10 per X account check.</p>
  </div>
}
