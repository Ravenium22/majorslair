import { useEffect, useMemo, useState } from 'react'
import { Check, Copy, Dices, Download, Minus, Plus, Ticket, Trophy, X } from 'lucide-react'
import useSWR from 'swr'
import { api, formatCount, formatDate, formatUsd, mutateApi } from '../api'
import { Empty, HelpLink, PageHeader, Toast, useConfirm, useEscape } from '../components'
import type { ConfigEntry, Session } from '../types'

type Post = { tweet_id: string; author: string; text: string; created_at: string; reply_count: number; retweet_count: number; url: string }
type Participant = { handle: string; reply: string; replied_at: string; reply_url: string }
type Participants = { participants: Participant[]; found_only_by_search: number; complete: boolean; credits: number; reply_count?: number; breakdown?: { from_list: number; only_by_search: number; second_replies: number; left_out: number; nested: number }; sources?: SourceRow[] }
type SourceRow = { source: string; items: number; new: number; pages: number; complete: boolean; error: string }
type RetweetPost = { tweet_id: string; url: string; author: string; retweet_count: number; retweeters_found: number; whole_list: boolean }
type Row = { handle: string; results: Record<string, boolean | null>; follows_all: boolean; retweeted: boolean | null; retweets?: Record<string, boolean | null>; verdict: 'passes' | 'missing' | 'check'; error: string; byHand?: boolean }
type Outcome = { checked: number; passes: number; to_check: number; errors: number; accounts: string[]; retweet: RetweetPost | null; retweet_posts?: RetweetPost[]; credits: number; rows: Row[] }

const FOLLOW_CHECK_CREDITS = 100
const ITEM_CREDITS = 15
const HANDLE = /^[A-Za-z0-9_]{1,15}$/
const MAX_ACCOUNTS = 5
const MAX_RETWEETS = 3
const SOURCE_LABEL: Record<string, string> = { thread_latest: 'Thread, newest first', thread_top: "Thread, X's order", reply_list: 'Reply list', conversation: 'Conversation search', to_author: 'Replies-to search' }

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
    // Replies are read from five sources (two thread views, the reply list, two searches),
    // each returning many of the same replies, so the estimate allows for all of them.
    const estimate = Math.max(1, post.reply_count) * ITEM_CREDITS * 5
    if (!(await confirm({
      title: `Fetch everyone who replied to @${post.author}?`,
      body: <>The post shows {formatCount(post.reply_count)} repl{post.reply_count === 1 ? 'y' : 'ies'}. The bot reads the thread two ways, the reply list, and two searches, because each misses replies the others return. About {formatUsd(estimate)}. {handles.length ? 'This replaces the handles already in the list.' : ''}</>,
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
      if (!retweetUrls.some((url) => url.trim())) setRetweetUrls([post.url])
    } catch (err) { setNotice({ text: err instanceof Error ? err.message : 'Could not fetch the replies', kind: 'error' }) }
    finally { setFetching(false) }
  }

  // ---- 2. What they had to do ---------------------------------------------------------------
  const [accounts, setAccounts] = useState<string[]>(['', ''])
  const [prefilled, setPrefilled] = useState(false)
  const [retweetUrls, setRetweetUrls] = useState<string[]>([''])
  const retweetList = retweetUrls.map((url) => url.trim()).filter(Boolean)
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
  const estimate = lookups * FOLLOW_CHECK_CREDITS + retweetList.length * Math.max(100, post?.retweet_count ?? 100) * ITEM_CREDITS
  const retweetWords = retweetList.length === 1 ? 'the post' : `all ${retweetList.length} posts`
  const tooMany = handles.length > 500
  const ready = handles.length > 0 && targets.length > 0 && !tooMany

  const run = async () => {
    if (!ready) return
    if (!(await confirm({
      title: `Check ${handles.length} entr${handles.length === 1 ? 'y' : 'ies'}?`,
      body: <>Each entry is checked for following {targets.map((t) => `@${t}`).join(', ')}{retweetList.length ? ` and for retweeting ${retweetWords}` : ''}: {formatCount(lookups)} follow lookup{lookups === 1 ? '' : 's'}{retweetList.length ? ` plus ${retweetList.length === 1 ? 'one read of the retweeter list' : `a read of each post's retweeter list`}` : ''}, about {formatUsd(estimate)}. It only reads; nothing in the bot changes.</>,
      confirmLabel: 'Run the check',
      tone: 'cost',
    }))) return
    setBusy(true)
    setOutcome(undefined)
    try {
      setPassedByHand(new Set())
      setOutcome(await mutateApi<Outcome>('/api/tools/follow-check', session.csrf_token, 'POST', { handles, accounts: targets, retweet_urls: retweetList }))
      setShow('all')
    } catch (err) { setNotice({ text: err instanceof Error ? err.message : 'The check failed', kind: 'error' }) }
    finally { setBusy(false) }
  }

  const [passedByHand, setPassedByHand] = useState<Set<string>>(new Set())
  const effectiveRows: Row[] = useMemo(() => (outcome ? outcome.rows.map((row) => (passedByHand.has(row.handle) ? { ...row, verdict: 'passes' as const, byHand: true } : row)) : []), [outcome, passedByHand])
  const count = (verdict: Row['verdict']) => effectiveRows.filter((row) => row.verdict === verdict).length
  const rtPosts: RetweetPost[] = outcome ? (outcome.retweet_posts ?? (outcome.retweet ? [outcome.retweet] : [])) : []
  const rtLabel = (p: RetweetPost) => `post ${rtPosts.indexOf(p) + 1}${p.author ? ` (@${p.author})` : ''}`
  const togglePass = (handle: string) => setPassedByHand((current) => { const next = new Set(current); if (next.has(handle)) next.delete(handle); else next.add(handle); return next })
  const visible = effectiveRows.filter((row) => show === 'all' || row.verdict === show)
  const handlesWith = (verdict: Row['verdict']) => effectiveRows.filter((row) => row.verdict === verdict).map((row) => `@${row.handle}`)
  const copy = async (verdict: Row['verdict']) => {
    const list = handlesWith(verdict)
    try { await navigator.clipboard.writeText(list.join('\n')); setNotice({ text: `Copied ${list.length} handle${list.length === 1 ? '' : 's'}.`, kind: 'success' }) }
    catch { setNotice({ text: 'Could not copy to the clipboard.', kind: 'error' }) }
  }
  const download = () => {
    if (!outcome) return
    const cell = (value: unknown) => { const t = String(value ?? ''); return /[",\n\r]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t }
    const yn = (value: boolean | null) => (value === null ? 'unknown' : value ? 'yes' : 'no')
    const header = ['handle', 'result', 'passed_by_hand', ...outcome.accounts.map((a) => `follows_${a}`), ...rtPosts.map((p, i) => (rtPosts.length === 1 ? 'retweeted' : `retweeted_post_${i + 1}`)), 'reply', 'error']
    const lines = effectiveRows.map((row) => [row.handle, VERDICT_LABEL[row.verdict], row.byHand ? 'yes' : 'no', ...outcome.accounts.map((a) => yn(row.results[a])), ...rtPosts.map((p) => yn(row.retweets ? row.retweets[p.tweet_id] ?? null : row.retweeted)), replies[row.handle.toLowerCase()]?.reply ?? '', row.error].map(cell).join(','))
    const blob = new Blob(['﻿' + [header.join(','), ...lines].join('\r\n')], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `majors-lair-raffle-${new Date().toISOString().slice(0, 10)}.csv`
    anchor.click()
    URL.revokeObjectURL(url)
  }
  // ---- 4. Pick winners -----------------------------------------------------------------------
  // The draw happens on the server and every draw is logged, redraws included, so a result
  // can be shown to anyone and never quietly replaced.
  const [pickerOpen, setPickerOpen] = useState(false)
  const [howMany, setHowMany] = useState('1')
  const [drawing, setDrawing] = useState(false)
  const [draw, setDraw] = useState<{ winners: string[]; pool_size: number; drawn_at: string }>()
  const eligible = effectiveRows.filter((row) => row.verdict === 'passes').map((row) => row.handle)
  const wanted = Number(howMany)
  const wantedValid = Number.isInteger(wanted) && wanted >= 1 && wanted <= eligible.length
  useEscape(pickerOpen && !drawing, () => setPickerOpen(false))
  const openPicker = () => { setDraw(undefined); setHowMany('1'); setPickerOpen(true) }
  const runDraw = async (again: boolean) => {
    if (!wantedValid) return
    if (again && !(await confirm({
      title: 'Draw again?',
      body: 'The first draw stays in the Audit trail with its winners, so anyone can see there were two draws.',
      confirmLabel: 'Draw again',
      tone: 'danger',
    }))) return
    setDrawing(true)
    try {
      setDraw(await mutateApi<{ winners: string[]; pool_size: number; drawn_at: string }>('/api/tools/raffle/draw', session.csrf_token, 'POST', { pool: eligible, passed_by_hand: [...passedByHand], count: wanted, post_url: post?.url ?? '' }))
    } catch (err) { setNotice({ text: err instanceof Error ? err.message : 'The draw failed', kind: 'error' }) }
    finally { setDrawing(false) }
  }
  const copyWinners = async () => {
    if (!draw) return
    try { await navigator.clipboard.writeText(draw.winners.map((w) => `@${w}`).join('\n')); setNotice({ text: `Copied ${draw.winners.length} winner${draw.winners.length === 1 ? '' : 's'}.`, kind: 'success' }) }
    catch { setNotice({ text: 'Could not copy to the clipboard.', kind: 'error' }) }
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
          {fetched && <div className="fetch-report">
            <p><strong>{formatCount(fetched.participants.length)} people</strong> found{fetched.breakdown ? <>: {fetched.breakdown.from_list} from the post's reply list, {fetched.breakdown.only_by_search} more only through search (X hid their reply from the list)</> : null}.</p>
            {fetched.breakdown && (fetched.breakdown.second_replies + fetched.breakdown.left_out + fetched.breakdown.nested) > 0 && <p className="field-hint">Not counted: {[fetched.breakdown.second_replies ? `${fetched.breakdown.second_replies} second replies by someone already in` : null, fetched.breakdown.left_out ? `${fetched.breakdown.left_out} by the author or the accounts below` : null, fetched.breakdown.nested ? `${fetched.breakdown.nested} replies to other replies` : null].filter(Boolean).join(', ')}.</p>}
            {fetched.sources && <details className="source-report"><summary>Where they came from</summary><ul>{fetched.sources.map((s) => <li key={s.source}><span>{SOURCE_LABEL[s.source] ?? s.source}</span>{s.error ? <span className="failed-text">failed: {s.error}</span> : <span>{formatCount(s.items)} replies read, {formatCount(s.new)} new{s.complete ? '' : ', stopped early'}</span>}</li>)}</ul></details>}
            {fetched.reply_count !== undefined && fetched.reply_count > fetched.participants.length + (fetched.breakdown ? fetched.breakdown.second_replies + fetched.breakdown.left_out + fetched.breakdown.nested : 0) && <p className="estimate-warning">The post shows {formatCount(fetched.reply_count)} replies, so some are still not visible to the bot. Replies X marks as probable spam are not available to any outside tool. Open "Show probable spam" under the post on X and paste those handles into the list.</p>}
          </div>}
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
        <fieldset className="checker-accounts raffle-retweet" disabled={busy}>
          <legend>Retweet</legend>
          {retweetUrls.map((value, index) => <span className="inline-field" key={index}><input value={value} onChange={(e) => setRetweetUrls(retweetUrls.map((u, i) => (i === index ? e.target.value : u)))} placeholder={index === 0 ? 'Optional. e.g. https://x.com/m_m3l/status/…' : 'Another post they had to retweet'} spellCheck={false} aria-label={`Post to retweet ${index + 1}`} />{retweetUrls.length > 1 && <button type="button" className="icon-button" onClick={() => setRetweetUrls(retweetUrls.filter((_, i) => i !== index))} aria-label={`Remove post ${index + 1}`}><X size={15} /></button>}</span>)}
          <span className="retweet-actions">
            {retweetUrls.length < MAX_RETWEETS && <button type="button" className="link-button add-account" onClick={() => setRetweetUrls([...retweetUrls, ''])}><Plus size={14} /> Add another post</button>}
            {post && !retweetUrls.some((url) => url.trim() === post.url) && <button type="button" className="link-button" onClick={() => setRetweetUrls(retweetUrls.some((url) => url.trim()) ? [...retweetUrls.filter((url) => url.trim()), post.url].slice(0, MAX_RETWEETS) : [post.url])}>Use the raffle post</button>}
          </span>
          <small className="field-hint">Everyone has to have retweeted every post listed. Likes cannot be checked: X does not show who liked a post. If X returns fewer retweeters than a post has, anyone it did not return is marked "Not found" for you to check, never "No".</small>
        </fieldset>
      </div>
      <div className="checker-run">
        <p className="muted">{ready ? <>{formatCount(handles.length)} entr{handles.length === 1 ? 'y' : 'ies'} · {formatCount(lookups)} follow lookups{retweetList.length ? ` + retweeters of ${retweetList.length} post${retweetList.length === 1 ? '' : 's'}` : ''} · about <strong>{formatUsd(estimate)}</strong></> : 'Add entrants and at least one account.'}</p>
        <button className="button primary" onClick={run} disabled={busy || !ready}><Ticket size={16} /> {busy ? 'Checking…' : '3. Check who did it'}</button>
      </div>
    </section>

    {outcome && <section className="panel">
      <div className="panel-head"><div><h2>{count('passes')} of {outcome.checked} pass{passedByHand.size ? <small className="by-hand-note">{passedByHand.size} passed by hand</small> : null}</h2></div><span>{formatUsd(outcome.credits)} spent</span></div>
      <div className="toolbar">
        <div className="segmented">{(['all', 'passes', 'missing', 'check'] as const).map((key) => <button key={key} className={show === key ? 'active' : ''} aria-pressed={show === key} onClick={() => setShow(key)}>{key === 'all' ? `All ${outcome.checked}` : key === 'passes' ? `Passes ${count('passes')}` : key === 'missing' ? `Missing something ${count('missing')}` : `Check by hand ${count('check')}`}</button>)}</div>
        <button className="button primary" onClick={openPicker} disabled={!count('passes')} title={count('passes') ? undefined : 'Nobody passed, so there is nobody to draw from'}><Dices size={15} /> Pick winners</button>
        <button className="button" onClick={() => copy('passes')} disabled={!count('passes')}><Copy size={15} /> Copy who passes</button>
        <button className="button" onClick={download}><Download size={15} /> Download CSV</button>
      </div>
      {rtPosts.filter((p) => !p.whole_list).map((p) => <p key={p.tweet_id} className="estimate-warning">X returned {formatCount(p.retweeters_found)} of {rtLabel(p)}'s {formatCount(p.retweet_count)} retweeters, so anyone not among them is marked "Not found" and sorted into Check by hand rather than failed.</p>)}
      {visible.length ? <div className="table-wrap"><table className="checker-table"><thead><tr><th>Entrant</th><th>Result</th>{outcome.accounts.map((a) => <th key={a}>Follows @{a}</th>)}{rtPosts.map((p) => <th key={p.tweet_id}><a href={p.url} target="_blank" rel="noreferrer">{rtPosts.length === 1 ? 'Retweeted' : `Retweeted ${rtLabel(p)}`}</a></th>)}<th>Their reply</th></tr></thead><tbody>
        {visible.map((row) => {
          const reply = replies[row.handle.toLowerCase()]
          return <tr key={row.handle}>
            <td><a href={`https://x.com/${row.handle}`} target="_blank" rel="noreferrer">@{row.handle}</a>{row.error && <small className="block muted">{row.error}</small>}</td>
            <td className="verdict-cell"><span className={`status ${row.verdict === 'passes' ? 'complete' : row.verdict === 'missing' ? 'failed' : ''}`}><i />{row.byHand ? 'Passed by hand' : VERDICT_LABEL[row.verdict]}</span>
              {row.byHand
                ? <button type="button" className="link-button" onClick={() => togglePass(row.handle)}>Undo</button>
                : row.verdict !== 'passes' && <button type="button" className="button small-button" onClick={() => togglePass(row.handle)} title="You checked this one on X yourself and they did everything">Pass</button>}
            </td>
            {outcome.accounts.map((a) => <td key={a}>{mark(row.results[a])}</td>)}
            {rtPosts.map((p) => <td key={p.tweet_id}>{mark(row.retweets ? row.retweets[p.tweet_id] ?? null : row.retweeted)}</td>)}
            <td className="reply-cell">{reply ? <a href={reply.reply_url} target="_blank" rel="noreferrer">{reply.reply || 'Open reply'}</a> : <span className="muted">—</span>}</td>
          </tr>
        })}
      </tbody></table></div> : <Empty title="Nobody here" copy="Nobody in the list falls into this group." />}
    </section>}

    {pickerOpen && outcome && <div className="modal-backdrop" onMouseDown={() => { if (!drawing) setPickerOpen(false) }}><div className="modal raffle-picker" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal-icon">{draw ? <Trophy /> : <Dices />}</div>
      {!draw ? <>
        <h2>Pick winners</h2>
        <p>Drawn at random from the <strong>{eligible.length}</strong> entrant{eligible.length === 1 ? '' : 's'} who pass.{count('check') ? <> The {count('check')} still marked Check by hand are not in the draw: check them on X and press Pass if they did everything.</> : null}{passedByHand.size ? <> Includes {passedByHand.size} you passed by hand.</> : null} Every draw is saved in the Audit trail.</p>
        <label>How many winners?<input autoFocus type="text" inputMode="numeric" pattern="[0-9]*" value={howMany} onChange={(e) => setHowMany(e.target.value.replace(/[^0-9]/g, '').slice(0, 3))} onKeyDown={(e) => { if (e.key === 'Enter' && wantedValid) void runDraw(false) }} disabled={drawing} /></label>
        {howMany !== '' && !wantedValid && <p className="field-hint error-hint">{wanted < 1 ? 'Pick at least one winner.' : `There are only ${eligible.length} entrant${eligible.length === 1 ? '' : 's'} who pass.`}</p>}
        <div className="modal-actions"><button className="button ghost" onClick={() => setPickerOpen(false)} disabled={drawing}>Cancel</button><button className="button primary" onClick={() => void runDraw(false)} disabled={drawing || !wantedValid}><Dices size={16} /> {drawing ? 'Drawing…' : 'Raffle'}</button></div>
      </> : <>
        <h2>{draw.winners.length === 1 ? 'The winner' : `The ${draw.winners.length} winners`}</h2>
        <ol className="winner-list">{draw.winners.map((handle) => <li key={handle}><a href={`https://x.com/${handle}`} target="_blank" rel="noreferrer">@{handle}</a>{replies[handle.toLowerCase()]?.reply && <small>{replies[handle.toLowerCase()].reply}</small>}</li>)}</ol>
        <p className="field-hint">Drawn from {draw.pool_size} eligible entrant{draw.pool_size === 1 ? '' : 's'} on {formatDate(draw.drawn_at)}. Saved in the Audit trail.</p>
        <div className="modal-actions"><button className="button ghost" onClick={() => void runDraw(true)} disabled={drawing}>{drawing ? 'Drawing…' : 'Draw again'}</button><button className="button" onClick={copyWinners}><Copy size={15} /> Copy winners</button><button className="button primary" onClick={() => setPickerOpen(false)}>Done</button></div>
      </>}
    </div></div>}
  </div>
}
