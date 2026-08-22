import { useState } from 'react'
import { Fingerprint } from 'lucide-react'
import useSWR from 'swr'
import { api, formatDate } from '../api'
import { Empty, PageHeader, Pagination } from '../components'
import type { AuditEntry, Paginated } from '../types'

export default function AuditPage() {
  const [page, setPage] = useState(1)
  const { data, isLoading } = useSWR<Paginated<AuditEntry>>(`/api/audit?page=${page}&page_size=35`, api)
  return <div className="page">
    <PageHeader eyebrow="Accountability ledger" title="Audit trail" copy="An immutable operational record of admin access, identity changes, scans, rule edits, and resets." actions={<div className="system-pill"><Fingerprint size={15} /> Database recorded</div>} />
    <section className="panel timeline-panel">
      {data?.items.map((event) => <article className="audit-event" key={event.event_id}><div className="audit-node"><i /></div><div className="audit-time"><strong>{formatDate(event.created_at)}</strong><span className="mono">{event.event_id.slice(0, 8)}</span></div><div className="audit-body"><span className="action-chip">{event.event_type.replaceAll('_', ' ')}</span><p>Actor <strong>{event.actor_discord_id || 'system'}</strong>{event.subject_discord_id ? <> · Subject <strong>{event.subject_discord_id}</strong></> : null}</p>{(event.old_value || event.new_value) && <small>{event.old_value || '∅'} → {event.new_value || '∅'}</small>}<details><summary>Technical details</summary><pre>{JSON.stringify(event.details, null, 2)}</pre></details></div></article>)}
      {!isLoading && !data?.items.length && <Empty title="No audit events yet" copy="Administrator and bot actions will be recorded here." />}
      <Pagination page={page} size={35} total={data?.total ?? 0} onChange={setPage} />
    </section>
  </div>
}
