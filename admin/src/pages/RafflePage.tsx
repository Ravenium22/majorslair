import { useEffect, useMemo, useState } from 'react'
import { Check, Copy, Download, Minus, Plus, Ticket, X } from 'lucide-react'
import useSWR from 'swr'
import { api, formatCount, formatDate, formatUsd, mutateApi } from '../api'
import { Empty, HelpLink, PageHeader, Toast, useConfirm } from '../components'
import type { ConfigEntry, Session } from '../types'

type Post = { tweet_id: string; author: string; text: string; created_at: string; reply_count: number; retweet_count: number; url: string }
type Participant = { handle: string; reply: string; replied_at: string; reply_url: string }
type Participants = { participants: Participant[]; found_only_by_search: number; complete: boolean; credits: number }
type Row = { handle: string; results: Record<string, boolean | null>; follows_all: boolean; retweeted: boolean | null; verdict: 'passes' | 'missing' | 'check'; error: string }
type Outcome = { checked: number; passes: number; to_check: number; errors: number; accounts: string[]; retweet: { retweet_count: number; retweeters_found: number; whole_list: boolean } | null; credits: number; rows: Row[] }

const FOLLOW_CHECK_CREDITS = 100
const ITEM_CREDITS = 15
const HANDLE = /^[A-Za-z0-9_]{1,15}$/
const MAX_ACCOUNTS = 5

/** Handles from pasted text: @ and x.com links stripped, duplicates and junk dropped. */
function parse(text: string): { handles: string[]; invalid: string[] } {
  const seen = new Set<string>()
  const handles: string[] = []
  const invalid: string[] = []
  for (const raw of text.split(/[\s,;]+/)) {
    if (!raw) continue
    const link = raw.match(/(?:x|twitter)\.com\/([A-Za-z0-9_]{1,15})/)
    const value = (link ? link[1] : raw).replace(/^@/, '')
    if (!HANDLE.test(value)) { invalid.push(raw); continue }
    if (seen.has(value.toLowerCase())) continue
    seen.add(value.toLowerCase())
    handles.push(value)
  }
  return { handles, invalid }
}

const VERDICT_LABEL = { passes: 'Passes', missing: 'Missing something', check: 'Check by hand' } as const

/** A raffle on X: who entered (everyone who replied, or a pasted list), what they had to do
 *  (follow some accounts, retweet a post), and who did it. It only reads; nothing in the
 *  bot changes, and entrants do not need to be in the server. */
export default function RafflePage({ session }: { session: Session }) {
  const { data: config } = useSWR<ConfigEntry[]>('/api/config', api)
  const confirm = useConfirm()
  const [notice, setNotice] = useState<{ text: string; kind: 'success' | 'error' }>()

  // ---- 1. Who entered ---------------------------------------------------------------------
  const [postUrl, setPostUrl] = useState('')
  const [post, setPost] = useState<Post>()
  const [loadingPost, setLoadingPost] = useState(false)
  const [fetching, setFetching] = useState(false)
  const [fetched, setFetched] = useState<Participants>()
  const [replies, setReplies] = useState<Record<string, Participant>>({})
  const [text, setText] = useState('')
  const { handles, invalid } = useMemo(() => parse(text), [text])

  const loadPost = async () => {
    setLoadingPost(true)
    setPost(undefined)
    try { setPost(await api<Post>(`/api/tools/raffle/tweet?url=${encodeURIComponent(postUrl.trim())}`)) }
    catch (err) { setNotice({ text: err instanceof Error ? err.message : 'Could not read that post', kind: 'error' }) }
    finally { setLoadingPost(false) }
  }

  const fetchRepliers = async () => {
    if (!post) return
    // Replies are read twice (the post's list, and a search for the ones X hides), so the
    // estimate allows for both.
    const estimate = Math.max(1, post.reply_count) * ITEM_CREDITS * 2
    if (!(await confirm({
      title: `Fetch everyone who replied to @${post.author}?`,
      body: <>The post shows {formatCount(post.reply_count)} repl{post.reply_count === 1 ? 'y' : 'ies'}. The bot reads the reply list and also searches the conversation, because X hides low-effort replies such as "done @friend" from the list. About {formatUsd(estimate)}. {handles.length ? 'This replaces the handles already in the list.' : ''}</>,
      confirmLabel: 'Fetch repliers',
      tone: 'cost',
    }))) return
    setFetching(true)
    try {
      const result = await mutateApi<Participants>('/api/tools/raffle/participants', session.csrf_token, 'POST', { url: post.url, exclude: parse(accounts.join(' ')).handles })
      setFetched(result)
      setReplies(Object.fromEntries(result.participants.map((p) => [p.handle.toLowerCase(), p])))
      setText(result.participants.map((p) => `@${p.handle}`).join('\n'))
      setOutcome(undefined)
      if (!retweetUrl) setRetweetUrl(post.url)
    } catch (err) { setNotice({ text: err instanceof Error ? err.message : 'Could not fetch the replies', kind: 'error' }) }
    finally { setFetching(false) }
  }

  // ---- 2. What they had to do ---------------------------------------------------------------
  const [accounts, setAccounts] = useState<string[]>(['', ''])
  const [prefilled, setPrefilled] = useState(false)
  const [retweetUrl, setRetweetUrl] = useState('')
  useEffect(() => {
    if (prefilled || !config) return
    const value = (key: string) => config.find((entry) => entry.key === key)?.value ?? ''
    setAccounts([value('primary_handle'), value('secondary_handle')])
    setPrefilled(true)
  }, [config, prefilled])
  const targets = useMemo(() => parse(accounts.join(' ')).handles, [accounts])

  // ---- 3. Run -------------------------------------------------------------------------------
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<Outcome>()
  const [show, setShow] = useState<'all' | 'passes' | 'missing' | 'check'>('all')
  const lookups = handles.length * targets.length
  const estimate = lookups * FOLLOW_CHECK_CREDITS + (retweetUrl.trim() ? Math.max(100, post?.retweet_count ?? 100) * ITEM_CREDITS : 0)
  const tooMany = handles.length > 500
  const ready = handles.length > 0 && targets.length > 0 && !tooMany

  const run = async () => {
    if (!ready) return
    if (!(await confirm({
      title: `Check ${handles.length} entr${handles.length === 1 ? 'y' : 'ies'}?`,
      body: <>Each entry is checked for following {targets.map((t) => `@${t}`).join(', ')}{retweetUrl.trim() ? ' and for retweeting the post' : ''}: {formatCount(lookups)} follow lookup{lookups === 1 ? '' : 's'}{retweetUrl.trim() ? ' plus one read of the retweeter list' : ''}, about {formatUsd(estimate)}. It only reads; nothing in the bot changes.</>,
      confirmLabel: 'Run the check',
      tone: 'cost',
    }))) return
    setBusy(true)
    setOutcome(undefined)
    try {
      setOutcome(await mutateApi<Outcome>('/api/tools/follow-check', session.csrf_token, 'POST', { handles, accounts: targets, retweet_url: retweetUrl.trim() }))
      setShow('all')
    } catch (err) { setNotice({ text: err instanceof Error ? err.message : 'The check failed', kind: 'error' }) }
    finally { setBusy(false) }
  }

  const visible = outcome ? outcome.rows.filter((row) => show === 'all' || row.verdict === show) : []
  const handlesWith = (verdict: Row['verdict']) => (outcome ? outcome.rows.filter((row) => row.verdict === verdict).map((row) => `@${row.handle}`) : [])
  const copy = async (verdict: Row['verdict']) => {
    const list = handlesWith(verdict)
    try { await navigator.clipboard.writeText(list.join('\n')); setNotice({ text: `Copied ${list.length} handle${list.length === 1 ? '' : 's'}.`, kind: 'success' }) }
    catch { setNotice({ text: 'Could not copy to the clipboard.', kind: 'error' }) }
  }
  const download = () => {
    if (!outcome) return
    const cell = (value: unknown) => { const t = String(value ?? ''); return /[",\n\r]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t }
    const yn = (value: boolean | null) => (value === null ? 'unknown' : value ? 'yes' : 'no')
    const header = ['handle', 'result', ...outcome.accounts.map((a) => `follows_${a}`), ...(outcome.retweet ? ['retweeted'] : []), 'reply', 'error']
    const lines = outcome.rows.map((row) => [row.handle, VERDICT_LABEL[row.verdict], ...outcome.accounts.map((a) => yn(row.results[a])), ...(outcome.retweet ? [yn(row.retweeted)] : []), replies[row.handle.toLowerCase()]?.reply ?? '', row.error].map(cell).join(','))
    const blob = new Blob(['﻿' + [header.join(','), ...lines].join('\r\n')], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `majors-lair-raffle-${new Date().toISOString().slice(0, 10)}.csv`
    anchor.click()
    URL.revokeObjectURL(url)
  }
  const mark = (value: boolean | null) => value === null ? <span className="muted"><Minus size={14} /> Not found</span> : value ? <span className="follow-yes"><Check size={14} /> Yes</span> : <span className="follow-no"><X size={14} /> No</span>

  return <div className="page">
    <PageHeader title="X raffle checker" copy="Pull in everyone who replied to a raffle post, or paste a list, then check who follows the accounts you name and who retweeted. Entrants do not need to be in the server, and nothing in the bot changes." />
    {notice && <Toast message={notice.text} kind={notice.kind} />}

    <section className="panel raffle-step">
      <div className="panel-head"><div><h2>1. Who entered</h2></div></div>
      <div className="raffle-body">
        <div className="raffle-post">
          <label>Raffle post<span className="inline-field"><input value={postUrl} onChange={(e) => { setPostUrl(e.target.value); setPost(undefined) }} placeholder="e.g. https://x.com/m_m3l/status/…" spellCheck={false} /><button className="button" onClick={loadPost} disabled={!postUrl.trim() || loadingPost}>{loadingPost ? 'Reading…' : 'Read post'}</button></span></label>
          {post && <div className="post-preview">
            <p className="post-account">@{post.author} · {formatDate(post.created_at)}</p>
            <p className="post-text">{post.text}</p>
            <p className="muted small">{formatCount(post.reply_count)} replies · {formatCount(post.retweet_count)} retweets</p>
            <button className="button primary" onClick={fetchRepliers} disabled={fetching}><Ticket size={16} /> {fetching ? 'Fetching…' : 'Fetch everyone who replied'}</button>
          </div>}
          {fetched && <p className="field-hint">{formatCount(fetched.participants.length)} people replied{fetched.found_only_by_search ? `, ${fetched.found_only_by_search} of them only found by search because X hid their reply` : ''}. The author and the accounts below are left out, and someone who replied twice counts once.{fetched.complete ? '' : ' The post has more replies than one fetch reads, so some may be missing.'}</p>}
        </div>
        <label className="raffle-list">Entrants{handles.length ? ` (${handles.length})` : ''}
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={9} placeholder={'Filled in from the post, or paste your own.\nOne per line or separated by commas.\n@winner_one\nhttps://x.com/winner_two'} spellCheck={false} disabled={busy} />
          <small className="field-hint">{invalid.length ? `Ignored ${invalid.length} that are not X handles: ${invalid.slice(0, 5).join(', ')}${invalid.length > 5 ? '…' : ''}. ` : ''}{tooMany ? 'At most 500 per check. ' : ''}You can edit the list before checking.</small>
        </label>
      </div>
    </section>

    <section className="panel raffle-step">
      <div className="panel-head"><div><h2>2. What they had to do</h2></div><HelpLink topic="raffle" /></div>
      <div className="raffle-body">
        <fieldset className="checker-accounts" disabled={busy}>
          <legend>Follow</legend>
          {accounts.map((value, index) => <span className="inline-field" key={index}><input value={value} onChange={(e) => setAccounts(accounts.map((a, i) => (i === index ? e.target.value : a)))} placeholder="@account" spellCheck={false} autoCapitalize="none" aria-label={`Account ${index + 1} to follow`} />{accounts.length > 1 && <button type="button" className="icon-button" onClick={() => setAccounts(accounts.filter((_, i) => i !== index))} aria-label={`Remove account ${index + 1}`}><X size={15} /></button>}</span>)}
          {accounts.length < MAX_ACCOUNTS && <button type="button" className="link-button add-account" onClick={() => setAccounts([...accounts, ''])}><Plus size={14} /> Add an account</button>}
          <small className="field-hint">Starts with the two tracked accounts; change them freely, it only affects this check.</small>
        </fieldset>
        <div className="raffle-retweet">
          <label>Retweet this post<input value={retweetUrl} onChange={(e) => setRetweetUrl(e.target.value)} placeholder="Optional. e.g. https://x.com/m_m3l/status/…" spellCheck={false} disabled={busy} /></label>
          {post && retweetUrl !== post.url && <button type="button" className="link-button" onClick={() => setRetweetUrl(post.url)}>Use the raffle post</button>}
          <p className="field-hint">Likes cannot be checked: X does not show who liked a post. If X returns fewer retweeters than the post has, anyone it did not return is marked "Not found" for you to check, never "No".</p>
        </div>
      </div>
      <div className="checker-run">
        <p className="muted">{ready ? <>{formatCount(handles.length)} entr{handles.length === 1 ? 'y' : 'ies'} · {formatCount(lookups)} follow lookups{retweetUrl.trim() ? ' + retweeters' : ''} · about <strong>{formatUsd(estimate)}</strong></> : 'Add entrants and at least one account.'}</p>
        <button className="button primary" onClick={run} disabled={busy || !ready}><Ticket size={16} /> {busy ? 'Checking…' : '3. Check who did it'}</button>
      </div>
    </section>

    {outcome && <section className="panel">
      <div className="panel-head"><div><h2>{outcome.passes} of {outcome.checked} pass</h2></div><span>{formatUsd(outcome.credits)} spent</span></div>
      <div className="toolbar">
        <div className="segmented">{(['all', 'passes', 'missing', 'check'] as const).map((key) => <button key={key} className={show === key ? 'active' : ''} aria-pressed={show === key} onClick={() => setShow(key)}>{key === 'all' ? `All ${outcome.checked}` : key === 'passes' ? `Passes ${outcome.passes}` : key === 'missing' ? `Missing something ${handlesWith('missing').length}` : `Check by hand ${outcome.to_check}`}</button>)}</div>
        <button className="button" onClick={() => copy('passes')} disabled={!outcome.passes}><Copy size={15} /> Copy who passes</button>
        <button className="button" onClick={download}><Download size={15} /> Download CSV</button>
      </div>
      {outcome.retweet && !outcome.retweet.whole_list && <p className="estimate-warning">X returned {formatCount(outcome.retweet.retweeters_found)} of the post's {formatCount(outcome.retweet.retweet_count)} retweeters, so anyone not among them is marked "Not found" and sorted into Check by hand rather than failed.</p>}
      {visible.length ? <div className="table-wrap"><table className="checker-table"><thead><tr><th>Entrant</th><th>Result</th>{outcome.accounts.map((a) => <th key={a}>Follows @{a}</th>)}{outcome.retweet && <th>Retweeted</th>}<th>Their reply</th></tr></thead><tbody>
        {visible.map((row) => {
          const reply = replies[row.handle.toLowerCase()]
          return <tr key={row.handle}>
            <td><a href={`https://x.com/${row.handle}`} target="_blank" rel="noreferrer">@{row.handle}</a>{row.error && <small className="block muted">{row.error}</small>}</td>
            <td><span className={`status ${row.verdict === 'passes' ? 'complete' : row.verdict === 'missing' ? 'failed' : ''}`}><i />{VERDICT_LABEL[row.verdict]}</span></td>
            {outcome.accounts.map((a) => <td key={a}>{mark(row.results[a])}</td>)}
            {outcome.retweet && <td>{mark(row.retweeted)}</td>}
            <td className="reply-cell">{reply ? <a href={reply.reply_url} target="_blank" rel="noreferrer">{reply.reply || 'Open reply'}</a> : <span className="muted">—</span>}</td>
          </tr>
        })}
      </tbody></table></div> : <Empty title="Nobody here" copy="Nobody in the list falls into this group." />}
    </section>}
  </div>
}
