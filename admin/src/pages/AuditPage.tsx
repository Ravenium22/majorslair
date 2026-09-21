import { useMemo, useState } from 'react'
import { Fingerprint, Search } from 'lucide-react'
import useSWR from 'swr'
import { api, formatDate } from '../api'
import { Empty, Loading, PageHeader, Pagination } from '../components'
import type { AuditEntry, Paginated } from '../types'

const n = (value: unknown) => (typeof value === 'number' ? value : Number(value ?? 0))

/** What happened, as a sentence. The raw payload stays under "Technical details"; this is
 *  the line that makes a 35-row page readable without opening anything. */
function summarise(event: AuditEntry): string {
  const d = (event.details ?? {}) as Record<string, any>
  const who = event.subject_name || event.subject_discord_id || 'a member'
  switch (event.event_type) {
    case 'config_updated': {
      const changes = d.changes as Record<string, { from: unknown; to: unknown }> | undefined
      if (changes) {
        const names = Object.keys(changes)
        const first = names.slice(0, 3).map((k) => `${k.replaceAll('_', ' ')} ${changes[k].from} → ${changes[k].to}`).join(', ')
        return `Changed ${names.length} rule${names.length === 1 ? '' : 's'}: ${first}${names.length > 3 ? `, and ${names.length - 3} more` : ''}. ${n(d.rescored_actions)} action records rescored.`
      }
      return `Changed ${(d.keys ?? []).length} rules. ${n(d.rescored_actions)} action records rescored.`
    }
    case 'engagement_scan': return `Ran a ${d.period ?? ''} scan: ${n(d.discovered)} actions matched.`.replace('  ', ' ')
    case 'member_scan': return `Deep-scanned ${who}: ${n(d.matched)} actions matched, ${n(d.new_actions)} new.`
    case 'admin_bulk_role': return `${d.action === 'remove' ? 'Removed' : 'Gave'} a role for ${n(d.changed)} of ${n(d.matched)} members${n(d.skipped) ? `, leaving ${n(d.skipped)} alone` : ''}.`
    case 'admin_points_adjusted': return `Adjusted ${who} by ${n(d.points) >= 0 ? '+' : ''}${n(d.points)} points${d.reason ? `: ${d.reason}` : ''}.`
    case 'admin_points_transferred': return `Moved ${n(d.points)} points to ${who}${d.reason ? `: ${d.reason}` : ''}.`
    case 'admin_members_imported': return `Imported a spreadsheet: ${n(d.linked)} linked, ${n(d.registered)} registered, ${n(d.skipped)} skipped.`
    case 'admin_members_synced': return `Synced from Discord: ${n(d.added)} added, ${n(d.updated)} updated.`
    case 'x_accounts_verified': return `Checked X accounts: ${n(d.checked)} checked, ${n(d.changed)} changed.`
    case 'leaderboard_reset': return `Froze the standings and started a new cycle.`
    case 'admin_member_protection_changed': return `${event.new_value === 'true' ? 'Protected' : 'Removed protection from'} ${who}.`
    case 'admin_member_toggled': return `${event.new_value === 'true' ? 'Reactivated' : 'Deactivated'} ${who}.`
    case 'tracked_post_toggled': return `${event.new_value === 'true' ? 'Resumed' : 'Paused'} tracking of post ${event.subject_discord_id || ''}.`.trim()
    case 'admin_login': return 'Signed in to the dashboard.'
    case 'low_activity_report': return `Opened the low-activity report: ${n(d.items)} members at or below ${d.threshold ?? 'the threshold'}.`
    default: return event.event_type.replaceAll('_', ' ')
  }
}

export default function AuditPage() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [type, setType] = useState('')
  const query = useMemo(() => new URLSearchParams({ search, event_type: type, page: String(page), page_size: '35' }).toString(), [search, type, page])
  const { data, isLoading } = useSWR<Paginated<AuditEntry> & { event_types: string[] }>(`/api/audit?${query}`, api)
  return <div className="page">
    <PageHeader title="Audit trail" copy="Everything the bot and its admins have done: links, scans, rule changes, point adjustments, roles and resets. Nothing here is ever edited or deleted." actions={<div className="system-pill"><Fingerprint size={15} /> Database recorded</div>} />
    <section className="panel timeline-panel">
      <div className="toolbar"><label className="search"><Search size={17} /><input value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} placeholder="Search a member, an ID or a value" /></label><select value={type} onChange={(e) => { setType(e.target.value); setPage(1) }} aria-label="Event type"><option value="">Everything that happened</option>{data?.event_types?.map((value) => <option key={value} value={value}>{value.replaceAll('_', ' ')}</option>)}</select></div>
      {isLoading && <Loading />}
      {data?.items.map((event) => <article className="audit-event" key={event.event_id}><div className="audit-node"><i /></div><div className="audit-time"><strong>{formatDate(event.created_at)}</strong><span className="mono">{event.event_id.slice(0, 8)}</span></div><div className="audit-body"><span className="action-chip">{event.event_type.replaceAll('_', ' ')}</span><p className="audit-summary">{summarise(event)}</p><p>By <strong>{event.actor_name || event.actor_discord_id || 'system'}</strong>{event.subject_discord_id ? <> · on <strong>{event.subject_name || event.subject_discord_id}</strong></> : null}</p>{(event.old_value || event.new_value) && <small>{event.old_value || '∅'} → {event.new_value || '∅'}</small>}<details><summary>Technical details</summary><pre>{JSON.stringify({ actor_discord_id: event.actor_discord_id, subject_discord_id: event.subject_discord_id, ...event.details }, null, 2)}</pre></details></div></article>)}
      {!isLoading && !data?.items.length && <Empty title="No audit events yet" copy="Administrator and bot actions will be recorded here." />}
      <Pagination page={page} size={35} total={data?.total ?? 0} onChange={setPage} />
    </section>
  </div>
}
