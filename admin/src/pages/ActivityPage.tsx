import { useMemo, useState, type FormEvent } from 'react'
import { ExternalLink, Search, Stethoscope } from 'lucide-react'
import useSWR from 'swr'
import { api, formatDate, formatScore, mutateApi } from '../api'
import { Empty, PageHeader, Pagination, Toast } from '../components'
import type { Action, Diagnosis, Paginated, Session } from '../types'

export default function ActivityPage({ session }: { session: Session }) {
  const [search, setSearch] = useState('')
  const [type, setType] = useState('')
  const [page, setPage] = useState(1)
  const [showDiagnose, setShowDiagnose] = useState(false)
  const [diagnosing, setDiagnosing] = useState(false)
  const [diagnosis, setDiagnosis] = useState<Diagnosis>()
  const [notice, setNotice] = useState<{ text: string; kind: 'success' | 'error' }>()
  const query = useMemo(() => new URLSearchParams({ search, action_type: type, page: String(page), page_size: '35' }).toString(), [search, type, page])
  const { data, isLoading } = useSWR<Paginated<Action>>(`/api/actions?${query}`, api)

  const diagnose = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const url = String(new FormData(event.currentTarget).get('url') ?? '').trim()
    if (!url) return
    setDiagnosing(true)
    setDiagnosis(undefined)
    try {
      setDiagnosis(await mutateApi<Diagnosis>('/api/diagnose', session.csrf_token, 'POST', { url }))
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Diagnosis failed', kind: 'error' }) }
    finally { setDiagnosing(false) }
  }

  return <div className="page">
    <PageHeader eyebrow="Scoring evidence" title="Activity log" copy="Inspect every discovered action, its point award, and the exact scoring explanation." actions={<button className="button" onClick={() => setShowDiagnose(true)}><Stethoscope size={17} /> Why isn't this counted?</button>} />
    {notice && <Toast message={notice.text} kind={notice.kind} />}
    <section className="panel">
      <div className="toolbar"><label className="search"><Search size={17} /><input value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} placeholder="Search handle, text, or reason" /></label><select value={type} onChange={(e) => { setType(e.target.value); setPage(1) }}><option value="">All action types</option><option value="reply">Replies</option><option value="quote">Quotes</option><option value="retweet">Retweets</option><option value="mention">Mentions</option></select></div>
      <div className="table-wrap"><table className="activity-table"><thead><tr><th>Member</th><th>Signal</th><th>Target</th><th>Content / decision</th><th>Points</th><th>Occurred</th><th /></tr></thead><tbody>{data?.items.map((item) => <tr className={item.active ? '' : 'muted-row'} key={item.action_key}><td><strong>@{item.twitter_handle}</strong><small className="mono block">{item.discord_user_id}</small></td><td><span className={`action-chip ${item.action_type}`}>{item.action_type}</span></td><td>@{item.target_handle}</td><td className="decision"><strong>{item.text || 'Native retweet'}</strong><small>{item.reason}</small></td><td className="score">{formatScore(item.points)}</td><td>{formatDate(item.occurred_at)}</td><td>{item.action_url && <a className="icon-button" href={item.action_url} target="_blank" title="Open on X"><ExternalLink size={16} /></a>}</td></tr>)}</tbody></table></div>
      {!isLoading && !data?.items.length && <Empty title="No matching activity" copy="Run a scan or adjust your search filters." />}
      <Pagination page={page} size={35} total={data?.total ?? 0} onChange={setPage} />
    </section>

    {showDiagnose && <div className="modal-backdrop" onMouseDown={() => { if (!diagnosing) { setShowDiagnose(false); setDiagnosis(undefined) } }}><div className="modal modal-wide" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal-icon"><Stethoscope /></div><p className="eyebrow">Diagnosis</p><h2>Why isn't this tweet counted?</h2>
      <p>Paste the URL of a reply, quote or post. The bot checks whether the author is linked, whether the tweet is already in the log, what it would score today, and whether X actually shows it in the parent's reply list and in search. Costs a few credits.</p>
      <form onSubmit={diagnose} className="diagnose-form">
        <input name="url" placeholder="https://x.com/user/status/123456789" required autoFocus disabled={diagnosing} />
        <button className="button primary" disabled={diagnosing}>{diagnosing ? 'Checking…' : 'Check'}</button>
      </form>
      {diagnosis && <div className="diagnosis">
        <ol className="findings">{diagnosis.findings.map((line, i) => <li key={i}>{line}</li>)}</ol>
        {diagnosis.tweet && <dl className="estimate-grid">
          <div><dt>Tweet</dt><dd><a href={diagnosis.tweet.url} target="_blank">@{diagnosis.tweet.author_handle}</a><small>{formatDate(diagnosis.tweet.created_at)} · {diagnosis.tweet.is_reply ? 'reply' : diagnosis.tweet.quoted_tweet_id ? 'quote' : 'post'}</small><small className="quote">“{diagnosis.tweet.text}”</small></dd></div>
          {diagnosis.parent && <div><dt>Parent post</dt><dd><a href={diagnosis.parent.url} target="_blank">@{diagnosis.parent.author_handle || '?'}</a><small>{diagnosis.parent.tracked ? 'tracked by scans' : 'never fetched by a scan'}{diagnosis.parent.reply_count != null ? ` · X shows ${diagnosis.parent.reply_count} replies` : ''}</small></dd></div>}
          {diagnosis.reply_endpoint && <div><dt>Parent's reply list</dt><dd><span className={`status ${diagnosis.reply_endpoint.found ? 'complete' : 'failed'}`}><i />{diagnosis.reply_endpoint.found ? 'includes it' : 'does not include it'}</span><small>{diagnosis.reply_endpoint.returned} replies returned{diagnosis.reply_endpoint.complete ? '' : ' (capped)'}</small></dd></div>}
          {diagnosis.sweep && <div><dt>Search sweep (to:@account)</dt><dd><span className={`status ${diagnosis.sweep.found ? 'complete' : 'failed'}`}><i />{diagnosis.sweep.found ? 'finds it' : 'does not find it'}</span><small>{diagnosis.sweep.returned} replies returned</small></dd></div>}
          {diagnosis.timeline && <div><dt>Author's own timeline</dt><dd><span className={`status ${diagnosis.timeline.found ? 'complete' : 'failed'}`}><i />{diagnosis.timeline.found ? 'shows it' : 'does not show it'}</span><small>{diagnosis.timeline.enabled ? 'timeline path is on' : 'timeline path is off (member_timeline_pages = 0)'}</small></dd></div>}
          {diagnosis.score_preview && <div><dt>Would score today</dt><dd>{formatScore(diagnosis.score_preview.points)} pts<small>{diagnosis.score_preview.reason}</small></dd></div>}
          <div><dt>In the actions log</dt><dd>{diagnosis.actions.filter((a) => a.action_tweet_id === diagnosis.tweet_id).length ? 'yes' : 'no'}<small>{diagnosis.member ? `author linked to ${diagnosis.member.discord_username}` : 'author not linked'}</small></dd></div>
        </dl>}
      </div>}
      <div className="modal-actions"><button className="button ghost" onClick={() => { setShowDiagnose(false); setDiagnosis(undefined) }} disabled={diagnosing}>Close</button></div>
    </div></div>}
  </div>
}
