import { useEffect, useMemo, useState } from 'react'
import { ArrowLeft, Check, Copy, Download, Minus, UserRoundSearch, X } from 'lucide-react'
import useSWR from 'swr'
import { api, formatCount, formatUsd, mutateApi } from '../api'
import { Empty, HelpLink, PageHeader, Toast, useConfirm } from '../components'
import type { ConfigEntry, Session } from '../types'

type Row = { handle: string; results: Record<string, boolean | null>; follows_all: boolean; error: string }
type Outcome = { checked: number; follows_all: number; errors: number; accounts: string[]; calls: number; credits: number; rows: Row[] }

const CREDITS_PER_CHECK = 100
const HANDLE = /^[A-Za-z0-9_]{1,15}$/

/** Mirrors the server's clean_handles: @ and x.com links stripped, duplicates dropped. */
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

/** Check whether any X handles (raffle winners, say) follow the accounts you name. People do
 *  not have to be in the server. It only reads: no member, setting or stored follow result
 *  changes, which is why it is a tool of its own rather than a change to the tracked accounts. */
export default function FollowCheckerPage({ session }: { session: Session }) {
  const { data: config } = useSWR<ConfigEntry[]>('/api/config', api)
  const confirm = useConfirm()
  const [text, setText] = useState('')
  const [accounts, setAccounts] = useState<string[]>(['', '', ''])
  const [prefilled, setPrefilled] = useState(false)
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<Outcome>()
  const [onlyMissing, setOnlyMissing] = useState(false)
  const [notice, setNotice] = useState<{ text: string; kind: 'success' | 'error' }>()

  // Start from the two tracked accounts; either can be replaced for this check only.
  useEffect(() => {
    if (prefilled || !config) return
    const value = (key: string) => config.find((entry) => entry.key === key)?.value ?? ''
    setAccounts([value('primary_handle'), value('secondary_handle'), ''])
    setPrefilled(true)
  }, [config, prefilled])

  const { handles, invalid } = useMemo(() => parse(text), [text])
  const targets = useMemo(() => parse(accounts.join(' ')).handles, [accounts])
  const checks = handles.length * targets.length
  const tooMany = handles.length > 200

  const run = async () => {
    if (!handles.length || !targets.length || tooMany) return
    if (!(await confirm({
      title: `Check ${handles.length} handle${handles.length === 1 ? '' : 's'}?`,
      body: `Each handle is checked against ${targets.map((t) => `@${t}`).join(', ')}: ${formatCount(checks)} lookup${checks === 1 ? '' : 's'} on X, about ${formatUsd(checks * CREDITS_PER_CHECK)}. It only reads; nothing in the bot changes.`,
      confirmLabel: 'Run the check',
      tone: 'cost',
    }))) return
    setBusy(true)
    setOutcome(undefined)
    try {
      setOutcome(await mutateApi<Outcome>('/api/tools/follow-check', session.csrf_token, 'POST', { handles, accounts: targets }))
    } catch (err) { setNotice({ text: err instanceof Error ? err.message : 'The check failed', kind: 'error' }) }
    finally { setBusy(false) }
  }

  const shown = outcome ? outcome.rows.filter((row) => !onlyMissing || !row.follows_all) : []
  const missing = outcome ? outcome.rows.filter((row) => !row.follows_all && !row.error).map((row) => `@${row.handle}`) : []

  const copyMissing = async () => {
    try { await navigator.clipboard.writeText(missing.join('\n')); setNotice({ text: `Copied ${missing.length} handle${missing.length === 1 ? '' : 's'}.`, kind: 'success' }) }
    catch { setNotice({ text: 'Could not copy to the clipboard.', kind: 'error' }) }
  }

  const download = () => {
    if (!outcome) return
    const cell = (value: unknown) => { const t = String(value ?? ''); return /[",\n\r]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t }
    const header = ['handle', ...outcome.accounts.map((a) => `follows_${a}`), 'follows_all', 'error']
    const lines = outcome.rows.map((row) => [row.handle, ...outcome.accounts.map((a) => row.results[a] === null ? 'unknown' : row.results[a] ? 'yes' : 'no'), row.follows_all ? 'yes' : 'no', row.error].map(cell).join(','))
    const blob = new Blob(['﻿' + [header.join(','), ...lines].join('\r\n')], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `majors-lair-follow-check-${new Date().toISOString().slice(0, 10)}.csv`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  return <div className="page">
    <a className="back-link" href="#members"><ArrowLeft size={15} /> Members</a>
    <PageHeader title="Check who follows" copy="Paste any X handles, raffle winners for example, and see who follows the accounts you name. They do not need to be in the server. It only reads: members, settings and stored follow results are left alone." />
    {notice && <Toast message={notice.text} kind={notice.kind} />}

    <section className="panel checker-form">
      <label className="checker-handles">Handles to check
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={7} placeholder={'One per line, or separated by commas.\n@winner_one\nhttps://x.com/winner_two'} disabled={busy} spellCheck={false} />
        <small className="field-hint">{handles.length ? `${handles.length} handle${handles.length === 1 ? '' : 's'} found` : 'Handles, @handles and x.com links all work.'}{invalid.length ? ` · ignored ${invalid.length} that are not X handles: ${invalid.slice(0, 5).join(', ')}${invalid.length > 5 ? '…' : ''}` : ''}{tooMany ? ' · at most 200 per check' : ''}</small>
      </label>
      <fieldset className="checker-accounts" disabled={busy}>
        <legend>Must follow</legend>
        {accounts.map((value, index) => <label key={index}><span className="visually-hidden">Account {index + 1}</span><input value={value} onChange={(e) => setAccounts(accounts.map((a, i) => (i === index ? e.target.value : a)))} placeholder={index === 2 ? 'Optional third account' : '@account'} spellCheck={false} autoCapitalize="none" /></label>)}
        <small className="field-hint">Starts with the two tracked accounts. Changing them here only affects this check.</small>
      </fieldset>
      <div className="checker-run">
        <p className="muted">{checks ? <>{formatCount(checks)} lookup{checks === 1 ? '' : 's'} · about <strong>{formatUsd(checks * CREDITS_PER_CHECK)}</strong></> : 'Add handles and at least one account.'} <HelpLink topic="follows" /></p>
        <button className="button primary" onClick={run} disabled={busy || !handles.length || !targets.length || tooMany}><UserRoundSearch size={16} /> {busy ? 'Checking…' : 'Check who follows'}</button>
      </div>
    </section>

    {outcome && <section className="panel">
      <div className="panel-head"><div><h2>{outcome.follows_all} of {outcome.checked} follow {outcome.accounts.length === 1 ? `@${outcome.accounts[0]}` : outcome.accounts.length === 2 ? 'both' : 'all of them'}</h2></div><span>{formatUsd(outcome.credits)} spent</span></div>
      <div className="toolbar">
        <label className="check-row inline"><input type="checkbox" checked={onlyMissing} onChange={(e) => setOnlyMissing(e.target.checked)} /> Only show who is missing a follow</label>
        <button className="button" onClick={copyMissing} disabled={!missing.length}><Copy size={15} /> Copy who is missing ({missing.length})</button>
        <button className="button" onClick={download}><Download size={15} /> Download CSV</button>
      </div>
      {outcome.errors > 0 && <p className="estimate-warning">{outcome.errors} handle{outcome.errors === 1 ? ' could' : 's could'} not be checked, usually because the account does not exist or is suspended. They are marked below and not counted either way.</p>}
      {shown.length ? <div className="table-wrap"><table className="checker-table"><thead><tr><th>Handle</th>{outcome.accounts.map((a) => <th key={a}>Follows @{a}</th>)}<th>All</th></tr></thead><tbody>
        {shown.map((row) => <tr key={row.handle}>
          <td><a href={`https://x.com/${row.handle}`} target="_blank" rel="noreferrer">@{row.handle}</a>{row.error && <small className="block muted">{row.error}</small>}</td>
          {outcome.accounts.map((a) => <td key={a}>{row.results[a] === null ? <span className="muted"><Minus size={14} /> unknown</span> : row.results[a] ? <span className="follow-yes"><Check size={14} /> Yes</span> : <span className="follow-no"><X size={14} /> No</span>}</td>)}
          <td>{row.error ? <span className="muted">—</span> : row.follows_all ? <span className="status complete"><i />Passes</span> : <span className="status failed"><i />Missing</span>}</td>
        </tr>)}
      </tbody></table></div> : <Empty title="Everyone follows" copy="Nobody in this list is missing a follow." />}
    </section>}
  </div>
}
