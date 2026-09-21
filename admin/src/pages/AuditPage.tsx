import { useMemo, useState } from 'react'
import { Fingerprint, Search } from 'lucide-react'
import useSWR from 'swr'
import { api, formatDate } from '../api'
import { Empty, Loading, PageHeader, Pagination } from '../components'
import type { AuditEntry, Paginated } from '../types'

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
      {data?.items.map((event) => <article className="audit-event" key={event.event_id}><div className="audit-node"><i /></div><div className="audit-time"><strong>{formatDate(event.created_at)}</strong><span className="mono">{event.event_id.slice(0, 8)}</span></div><div className="audit-body"><span className="action-chip">{event.event_type.replaceAll('_', ' ')}</span><p>By <strong>{event.actor_name || event.actor_discord_id || 'system'}</strong>{event.actor_name && <small className="mono"> {event.actor_discord_id}</small>}{event.subject_discord_id ? <> · on <strong>{event.subject_name || event.subject_discord_id}</strong>{event.subject_name && <small className="mono"> {event.subject_discord_id}</small>}</> : null}</p>{(event.old_value || event.new_value) && <small>{event.old_value || '∅'} → {event.new_value || '∅'}</small>}<details><summary>Technical details</summary><pre>{JSON.stringify(event.details, null, 2)}</pre></details></div></article>)}
      {!isLoading && !data?.items.length && <Empty title="No audit events yet" copy="Administrator and bot actions will be recorded here." />}
      <Pagination page={page} size={35} total={data?.total ?? 0} onChange={setPage} />
    </section>
  </div>
}
