import { useState } from 'react'
import { Download, ExternalLink, FileSearch } from 'lucide-react'
import useSWR from 'swr'
import { api, formatDate, formatScore } from '../api'
import { Empty, Loading, PageHeader } from '../components'
import type { LowActivityReport } from '../types'

const csvCell = (value: unknown) => { const text = String(value ?? ''); return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text }
const daysAgo = (iso: string) => Math.floor((Date.now() - new Date(iso).getTime()) / 86400000)

function download(report: LowActivityReport) {
  const header = ['discord_username', 'discord_id', 'x_handle', 'points', 'last_signal', 'joined_discord', 'days_in_server', 'x_status']
  const lines = report.items.map((m) => [
    m.discord_username, m.discord_user_id, m.twitter_handle, m.score,
    m.last_active_at || 'never', m.discord_joined_at || 'unknown',
    m.discord_joined_at ? daysAgo(m.discord_joined_at) : '',
    m.twitter_user_id ? (m.x_status || 'ok') : 'not linked',
  ].map(csvCell).join(','))
  const blob = new Blob(['﻿' + [header.join(','), ...lines].join('\r\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `majors-lair-low-activity-${new Date().toISOString().slice(0, 10)}.csv`
  anchor.click()
  URL.revokeObjectURL(url)
}

/** Every row opens that member's drawer on the Members page: this is the one screen whose
 *  output gets defended to a real person, so the proof has to be one click away. */
function MemberTable({ rows }: { rows: LowActivityReport['items'] }) {
  return <div className="table-wrap"><table>
    <thead><tr><th>Discord</th><th>X identity</th><th>Points</th><th>Last signal</th><th>In the server</th><th /></tr></thead>
    <tbody>{rows.map((m) => <tr key={m.discord_user_id}>
      <td><a className="member-cell linked-cell" href={`#members?search=${m.discord_user_id}&open=${m.discord_user_id}`} title="Open their points, history and scan tools"><strong>{m.discord_username}</strong><small className="mono">{m.discord_user_id}</small></a></td>
      <td>{m.twitter_user_id
        ? <span className="protected-cell"><a href={`https://x.com/${m.twitter_handle}`} target="_blank" rel="noreferrer">@{m.twitter_handle}</a>{(m.x_status === 'suspended' || m.x_status === 'unavailable') && <span className="status failed"><i />X {m.x_status}</span>}</span>
        : <span className="muted">No X account linked</span>}</td>
      <td className="score">{formatScore(m.score)}</td>
      <td>{formatDate(m.last_active_at)}</td>
      <td>{m.discord_joined_at ? <span className="protected-cell">{formatDate(m.discord_joined_at)}<small>{daysAgo(m.discord_joined_at)} days</small></span> : <span className="muted" title="Run Sync from Discord to fill join dates">unknown</span>}</td>
      <td><a className="icon-button" href={`#members?search=${m.discord_user_id}&open=${m.discord_user_id}`} title="See why they scored this"><FileSearch size={15} /></a>{m.twitter_user_id && <a className="icon-button" href={`https://x.com/${m.twitter_handle}`} target="_blank" rel="noreferrer" title="Open their X profile"><ExternalLink size={15} /></a>}</td>
    </tr>)}</tbody>
  </table></div>
}

export default function LowActivityPage() {
  const [threshold, setThreshold] = useState('')
  const query = threshold === '' ? '' : `?threshold=${threshold}`
  const { data, isLoading } = useSWR<LowActivityReport>(`/api/low-activity${query}`, api)
  const quiet = data?.items.filter((m) => m.twitter_user_id) ?? []
  const unlinked = data?.items.filter((m) => !m.twitter_user_id) ?? []

  return <div className="page">
    <PageHeader
      title="Low-activity report"
      copy="Who to consider removing this cycle. Protected members and recent joiners are left out automatically. Open anyone to read the evidence before you decide. Removing people happens in Discord, not here."
      actions={<button className="button" onClick={() => data && download(data)} disabled={!data?.items.length}><Download size={17} /> Download CSV</button>}
    />

    {data && <section className="panel rules-banner">
      <p>
        Showing active members with <strong>{formatScore(data.threshold)} points or fewer</strong>.
        {' '}Left out: <strong>{data.excluded_protected}</strong> protected {data.excluded_protected === 1 ? 'member' : 'members'}
        {data.newcomer_grace_days > 0 && <> and <strong>{data.excluded_newcomers}</strong> who joined in the last {data.newcomer_grace_days} days</>}.
      </p>
      <label className="threshold-box">Points at or below<input type="number" min={0} step={1} inputMode="numeric" value={threshold} onChange={(e) => setThreshold(e.target.value.replace(/[^0-9.]/g, ''))} placeholder={String(data.threshold)} /><span className="muted small">{threshold === '' ? 'from Scoring rules' : 'this view only'}</span></label>
    </section>}

    <section className="panel report-groups">
      {isLoading && !data && <Loading label="Building the report…" />}
      {data && quiet.length > 0 && <>
        <h2 className="sub-heading">Linked, but quiet <span className="group-count">{quiet.length}</span></h2>
        <p className="group-note">These members linked an X account, so the bot could see their engagement. It scored {formatScore(data.threshold)} or less.</p>
        <MemberTable rows={quiet} />
      </>}
      {data && unlinked.length > 0 && <>
        <h2 className="sub-heading">Never linked an X account <span className="group-count">{unlinked.length}</span></h2>
        <p className="group-note">The bot has never been able to score these members, so their points say nothing about whether they were active. Ask them to link an account before you judge them on this report.</p>
        <MemberTable rows={unlinked} />
      </>}
      {data && data.items.length === 0 && <Empty title="Nobody is below the threshold" copy="Every active member outside the protected and newcomer groups is above it. Lower the number above to widen the list." />}
      {data && data.items.length > 0 && <p className="muted small page-note">{data.items.length} {data.items.length === 1 ? 'member' : 'members'} in total · checked against the latest scan.</p>}
    </section>
  </div>
}
