import { useMemo, useState } from 'react'
import { ExternalLink, Search } from 'lucide-react'
import useSWR from 'swr'
import { api, formatDate, formatScore } from '../api'
import { Empty, PageHeader, Pagination } from '../components'
import type { Action, Paginated } from '../types'

export default function ActivityPage() {
  const [search, setSearch] = useState('')
  const [type, setType] = useState('')
  const [page, setPage] = useState(1)
  const query = useMemo(() => new URLSearchParams({ search, action_type: type, page: String(page), page_size: '35' }).toString(), [search, type, page])
  const { data, isLoading } = useSWR<Paginated<Action>>(`/api/actions?${query}`, api)

  return <div className="page">
    <PageHeader eyebrow="Scoring evidence" title="Activity log" copy="Inspect every discovered action, its point award, and the exact scoring explanation." />
    <section className="panel">
      <div className="toolbar"><label className="search"><Search size={17} /><input value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} placeholder="Search handle, text, or reason" /></label><select value={type} onChange={(e) => { setType(e.target.value); setPage(1) }}><option value="">All action types</option><option value="reply">Replies</option><option value="quote">Quotes</option><option value="retweet">Retweets</option><option value="mention">Mentions</option></select></div>
      <div className="table-wrap"><table className="activity-table"><thead><tr><th>Member</th><th>Signal</th><th>Target</th><th>Content / decision</th><th>Points</th><th>Occurred</th><th /></tr></thead><tbody>{data?.items.map((item) => <tr className={item.active ? '' : 'muted-row'} key={item.action_key}><td><strong>@{item.twitter_handle}</strong><small className="mono block">{item.discord_user_id}</small></td><td><span className={`action-chip ${item.action_type}`}>{item.action_type}</span></td><td>@{item.target_handle}</td><td className="decision"><strong>{item.text || 'Native retweet'}</strong><small>{item.reason}</small></td><td className="score">{formatScore(item.points)}</td><td>{formatDate(item.occurred_at)}</td><td>{item.action_url && <a className="icon-button" href={item.action_url} target="_blank" title="Open on X"><ExternalLink size={16} /></a>}</td></tr>)}</tbody></table></div>
      {!isLoading && !data?.items.length && <Empty title="No matching activity" copy="Run a scan or adjust your search filters." />}
      <Pagination page={page} size={35} total={data?.total ?? 0} onChange={setPage} />
    </section>
  </div>
}
