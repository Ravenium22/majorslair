import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowLeft, ArrowLeftRight, BadgeCheck, ExternalLink, Link2, Pencil, Shield, ShieldCheck, Trash2, UserRoundCheck, UserRoundX } from 'lucide-react'
import useSWR from 'swr'
import { api, formatCount, formatDate, formatDay, formatScore, formatUsd, mutateApi } from '../api'
import { Empty, HelpLink, Loading, Pagination, Toast, ToolsMenu, useConfirm, useEscape } from '../components'
import type { Action, Adjustment, LinkedUser, MemberDetail, MemberScanResult, Paginated, Session } from '../types'

const HISTORY_PAGE = 50
const TYPE_LABEL: Record<string, string> = { reply: 'Replies', quote: 'Quotes', retweet: 'Retweets', mention: 'Mentions' }
const TYPE_ORDER = ['reply', 'quote', 'retweet', 'mention']
const daysAgo = (iso: string) => Math.floor((Date.now() - new Date(iso).getTime()) / 86400000)
const memberIdFromHash = () => new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('id') ?? ''
const toolFromHash = () => {
  const tool = new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('tool')
  return tool === 'edit' || tool === 'points' || tool === 'scan' ? tool : 'none'
}

/** One member on a page of their own, with a link that can be sent to a moderator. It leads
 *  with the sum behind their score, then every action that went into it. This used to be a
 *  popup with two nested scrollbars and no address. */
export default function MemberPage({ session }: { session: Session }) {
  const [memberId, setMemberId] = useState(memberIdFromHash)
  useEffect(() => {
    const onHash = () => { if (window.location.hash.startsWith('#member?')) setMemberId(memberIdFromHash()) }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const { data, error, mutate } = useSWR<MemberDetail>(memberId ? `/api/users/${memberId}` : null, api)
  const [historyPage, setHistoryPage] = useState(1)
  const { data: history, mutate: mutateHistory } = useSWR<Paginated<Action>>(memberId ? `/api/actions?discord_user_id=${memberId}&page=${historyPage}&page_size=${HISTORY_PAGE}` : null, api)
  const { data: adjustments, mutate: mutateAdjustments } = useSWR<Adjustment[]>(memberId ? `/api/users/${memberId}/adjustments` : null, api)
  const confirm = useConfirm()
  const [notice, setNotice] = useState<{ text: string; kind: 'success' | 'error' }>()
  const [tool, setTool] = useState<'none' | 'edit' | 'points' | 'scan'>(toolFromHash)
  const member = data?.member
  const breakdown = data?.breakdown

  const refresh = async () => { await Promise.all([mutate(), mutateHistory(), mutateAdjustments()]) }

  // ---- Edit -----------------------------------------------------------------------------
  const editForm = useRef<HTMLFormElement>(null)
  const [saving, setSaving] = useState(false)
  const editDirty = () => {
    const form = editForm.current
    if (!form || tool !== 'edit' || !member) return false
    const values = new FormData(form)
    return String(values.get('discord_username') ?? '').trim() !== member.discord_username
      || String(values.get('twitter_handle') ?? '').trim().replace(/^@/, '') !== (member.twitter_handle ?? '')
      || (values.get('special_role') === 'on') !== member.special_role_manual
      || String(values.get('special_role_names') ?? '').trim() !== (member.special_role_names ?? '')
  }
  const switchTool = async (next: typeof tool) => {
    if (tool === 'edit' && next !== 'edit' && editDirty() && !(await confirm({
      title: 'Discard your changes?',
      body: `You have edits to ${member?.discord_username} that have not been saved. Leaving the form throws them away.`,
      confirmLabel: 'Discard',
      cancelLabel: 'Keep editing',
      tone: 'danger',
    }))) return
    setTool(next)
  }

  const saveEdit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!member) return
    const form = new FormData(event.currentTarget)
    const discord_username = String(form.get('discord_username') ?? '').trim()
    const twitter_handle = String(form.get('twitter_handle') ?? '').trim().replace(/^@/, '')
    const special_role = form.get('special_role') === 'on'
    const special_role_names = String(form.get('special_role_names') ?? '').trim()
    const changes: string[] = []
    if (discord_username && discord_username !== member.discord_username) changes.push(`Discord handle becomes ${discord_username}`)
    if (twitter_handle && twitter_handle.toLowerCase() !== member.twitter_handle.toLowerCase()) changes.push(`X account becomes @${twitter_handle}, checked on X before it is saved`)
    // The box is protection set by hand; protection from a Discord role is managed by sync.
    const manual = Boolean(member.special_role_manual)
    if (special_role !== manual) changes.push(special_role ? 'protected by hand' : 'protection set by hand is removed')
    else if (special_role && special_role_names !== member.special_role_names) changes.push(`protection label becomes "${special_role_names}"`)
    if (!changes.length) { setTool('none'); setNotice({ text: 'Nothing changed.', kind: 'success' }); return }
    if (!(await confirm({
      title: `Save changes to ${member.discord_username}?`,
      body: <ul className="confirm-list">{changes.map((change) => <li key={change}>{change}</li>)}</ul>,
      confirmLabel: 'Save changes',
    }))) return
    setSaving(true)
    try {
      const body: Record<string, unknown> = {}
      if (discord_username && discord_username !== member.discord_username) body.discord_username = discord_username
      if (special_role !== manual || (special_role && special_role_names !== member.special_role_names)) { body.special_role = special_role; body.special_role_names = special_role ? special_role_names : '' }
      if (Object.keys(body).length) await mutateApi<LinkedUser>(`/api/users/${member.discord_user_id}`, session.csrf_token, 'PATCH', body)
      if (twitter_handle && twitter_handle.toLowerCase() !== member.twitter_handle.toLowerCase()) {
        await mutateApi('/api/users/link', session.csrf_token, 'POST', { discord_user_id: member.discord_user_id, discord_username: discord_username || member.discord_username, twitter_handle })
      }
      setTool('none')
      setNotice({ text: `${discord_username || member.discord_username} updated.`, kind: 'success' })
      await refresh()
    } catch (err) { setNotice({ text: err instanceof Error ? err.message : 'Update failed', kind: 'error' }) }
    finally { setSaving(false) }
  }

  // ---- Points ---------------------------------------------------------------------------
  const [adjustMode, setAdjustMode] = useState<'add' | 'transfer'>('add')
  const [adjusting, setAdjusting] = useState(false)
  const submitAdjust = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!member) return
    const formElement = event.currentTarget
    const form = new FormData(formElement)
    const points = Number(form.get('points'))
    const reason = String(form.get('reason') ?? '').trim()
    const transfer_to = adjustMode === 'transfer' ? String(form.get('transfer_to') ?? '').trim() : ''
    if (!points || Number.isNaN(points)) { setNotice({ text: 'Enter a non-zero amount', kind: 'error' }); return }
    if (adjustMode === 'transfer' && !transfer_to) { setNotice({ text: 'Enter who receives the points', kind: 'error' }); return }
    const reasonNote = reason ? ` Reason recorded: "${reason}".` : ''
    if (!(await confirm(adjustMode === 'transfer' ? {
      title: `Move ${formatScore(Math.abs(points))} points from ${member.discord_username} to ${transfer_to}?`,
      body: `Both members' totals change now and the leaderboard follows.${reasonNote} To undo it, transfer the points back.`,
      confirmLabel: 'Move points',
    } : {
      title: `${points > 0 ? 'Add' : 'Take away'} ${formatScore(Math.abs(points))} points ${points > 0 ? 'for' : 'from'} ${member.discord_username}?`,
      body: `Their total changes now and the leaderboard follows.${reasonNote} To undo it, make the opposite adjustment.`,
      confirmLabel: points > 0 ? 'Add points' : 'Take points away',
      tone: points > 0 ? 'default' : 'danger',
    }))) return
    setAdjusting(true)
    try {
      await mutateApi(`/api/users/${member.discord_user_id}/adjust`, session.csrf_token, 'POST', { points: adjustMode === 'transfer' ? Math.abs(points) : points, reason, transfer_to: transfer_to || null })
      setNotice({ text: adjustMode === 'transfer' ? `Moved ${formatScore(Math.abs(points))} points from ${member.discord_username} to ${transfer_to}.` : `${points > 0 ? '+' : ''}${formatScore(points)} points for ${member.discord_username}.`, kind: 'success' })
      formElement.reset()
      await refresh()
    } catch (err) { setNotice({ text: err instanceof Error ? err.message : 'Adjustment failed', kind: 'error' }) }
    finally { setAdjusting(false) }
  }

  // ---- Scan this member -----------------------------------------------------------------
  const [memberPeriod, setMemberPeriod] = useState('30d')
  const [memberDepthTweets, setMemberDepthTweets] = useState(500)
  const memberDepth = Math.min(250, Math.max(1, Math.ceil(memberDepthTweets / 20)))
  const [memberScanning, setMemberScanning] = useState(false)
  const [memberScan, setMemberScan] = useState<MemberScanResult>()
  const runMemberScan = async () => {
    if (!member) return
    if (!(await confirm({
      title: `Scan ${member.discord_username}'s timeline?`,
      body: `Reads up to ${formatCount(memberDepthTweets)} of their latest tweets for the chosen window. It costs at most ${formatUsd(memberDepth * 20 * 15)}, usually much less because it stops at the window start. Anything newly matched is scored straight away.`,
      confirmLabel: 'Scan this member',
      tone: 'cost',
    }))) return
    setMemberScanning(true)
    try {
      const result = await mutateApi<MemberScanResult>(`/api/users/${member.discord_user_id}/scan`, session.csrf_token, 'POST', { period: memberPeriod, max_pages: memberDepth })
      setMemberScan(result)
      setNotice({ text: `${result.discord_username}: ${result.matched} actions matched, ${result.new_actions} new, ${formatScore(result.points_before)} → ${formatScore(result.points_after)} pts.`, kind: 'success' })
      await refresh()
    } catch (err) { setNotice({ text: err instanceof Error ? err.message : 'Member scan failed', kind: 'error' }) }
    finally { setMemberScanning(false) }
  }

  // ---- Protection, active state, delete -------------------------------------------------
  const patch = async (body: Record<string, unknown>, success: string) => {
    if (!member) return
    try {
      await mutateApi(`/api/users/${member.discord_user_id}`, session.csrf_token, 'PATCH', body)
      setNotice({ text: success, kind: 'success' })
      await refresh()
    } catch (err) { setNotice({ text: err instanceof Error ? err.message : 'Update failed', kind: 'error' }) }
  }
  const toggleProtected = async () => {
    if (!member) return
    const protecting = !member.special_role
    if (!(await confirm(protecting ? {
      title: `Protect ${member.discord_username}?`,
      body: 'They will never appear on the low-activity report, and scans leave them out whenever "skip protected members" is ticked. Nothing changes in Discord.',
      confirmLabel: 'Protect',
    } : {
      title: `Remove protection from ${member.discord_username}?`,
      body: <>They can appear on the low-activity report again.{member.role_protected_names ? <> They still hold <strong>{member.role_protected_names}</strong> in Discord, so the next sync will protect them again unless that role is removed there.</> : null}</>,
      confirmLabel: 'Remove protection',
      tone: 'danger',
    }))) return
    await patch({ special_role: protecting }, `${member.discord_username} is ${protecting ? 'now protected from the low-activity report' : 'no longer protected'}.`)
  }
  const toggleActive = async () => {
    if (!member) return
    if (!(await confirm(member.active ? {
      title: `Deactivate ${member.discord_username}?`,
      body: 'They drop out of the leaderboard, the low-activity report and every scan. Their history and points are kept, and you can reactivate them at any time.',
      confirmLabel: 'Deactivate',
      tone: 'danger',
    } : {
      title: `Reactivate ${member.discord_username}?`,
      body: 'They go back on the leaderboard with the points and history they had, and the next scans include them again.',
      confirmLabel: 'Reactivate',
    }))) return
    await patch({ active: !member.active }, `${member.discord_username} ${member.active ? 'deactivated' : 'reactivated'}.`)
  }

  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleteText, setDeleteText] = useState('')
  const [deleteIgnore, setDeleteIgnore] = useState(true)
  const [deleting, setDeleting] = useState(false)
  useEscape(deleteOpen && !deleting, () => setDeleteOpen(false))
  const removeMember = async () => {
    if (!member) return
    setDeleting(true)
    try {
      const result = await mutateApi<{ actions: number; adjustments: number; ignored_in_sync: boolean }>(`/api/users/${member.discord_user_id}?ignore_in_sync=${deleteIgnore}`, session.csrf_token, 'DELETE')
      setDeleteOpen(false)
      sessionStorage.setItem('member-deleted', `${member.discord_username} deleted, along with ${result.actions} scored action${result.actions === 1 ? '' : 's'} and ${result.adjustments} adjustment${result.adjustments === 1 ? '' : 's'}.${result.ignored_in_sync ? ' Sync from Discord will not add them back.' : ''}`)
      window.location.hash = '#members'
    } catch (err) { setNotice({ text: err instanceof Error ? err.message : 'Delete failed', kind: 'error' }) }
    finally { setDeleting(false) }
  }

  const copyLink = async () => {
    try { await navigator.clipboard.writeText(window.location.href); setNotice({ text: 'Link copied. Anyone with dashboard access can open it.', kind: 'success' }) }
    catch { setNotice({ text: 'Could not copy; the link is in the address bar.', kind: 'error' }) }
  }

  if (!memberId) return <div className="page"><Empty title="No member chosen" copy="Open a member from the Members page." /></div>
  if (error) return <div className="page"><a className="back-link" href="#members"><ArrowLeft size={15} /> All members</a><Empty title="Member not found" copy="They may have been deleted, or the link is wrong." /></div>
  if (!member || !breakdown) return <div className="page"><Loading label="Loading member…" /></div>

  const typed = TYPE_ORDER.filter((type) => breakdown.by_type[type])
  const zeroCount = typed.reduce((sum, type) => sum + breakdown.by_type[type].zero, 0)
  const followLabel = member.follows_checked_at
    ? member.follows_primary === 'yes' && member.follows_secondary === 'yes' ? 'Follows both accounts' : `Follows ${[member.follows_primary === 'yes' ? 'the primary' : null, member.follows_secondary === 'yes' ? 'the secondary' : null].filter(Boolean).join(' and ') || 'neither account'}`
    : 'Not checked yet'

  return <div className="page member-page">
    <a className="back-link" href="#members"><ArrowLeft size={15} /> All members</a>
    <header className="member-head">
      <div>
        <h1>{member.discord_username}</h1>
        <p className="member-sub"><span className="mono">{member.discord_user_id}</span>
          {!member.active && <span className="status failed"><i />Inactive</span>}
          {member.special_role && <span className="status complete"><i />Protected</span>}
        </p>
      </div>
      <div className="header-actions">
        <button className="button" onClick={copyLink}><Link2 size={16} /> Copy link</button>
        <button className={`button ${tool === 'edit' ? 'primary' : ''}`} onClick={() => switchTool(tool === 'edit' ? 'none' : 'edit')}><Pencil size={16} /> Edit</button>
        <button className={`button ${tool === 'points' ? 'primary' : ''}`} onClick={() => switchTool(tool === 'points' ? 'none' : 'points')}><ArrowLeftRight size={16} /> Points</button>
        {member.twitter_user_id && <button className={`button ${tool === 'scan' ? 'primary' : ''}`} onClick={() => switchTool(tool === 'scan' ? 'none' : 'scan')}><BadgeCheck size={16} /> Scan</button>}
        <ToolsMenu label="More" items={[
          { label: member.special_role ? 'Remove protection' : 'Protect', hint: member.special_role ? 'They can appear on the low-activity report again' : 'Never on the low-activity report', icon: member.special_role ? <Shield size={16} /> : <ShieldCheck size={16} />, onSelect: toggleProtected },
          { label: member.active ? 'Deactivate' : 'Reactivate', hint: member.active ? 'Off the leaderboard and scans, history kept' : 'Back on the leaderboard', icon: member.active ? <UserRoundX size={16} /> : <UserRoundCheck size={16} />, onSelect: toggleActive },
          { label: 'Delete record', hint: 'Erase them and their scoring data', icon: <Trash2 size={16} />, onSelect: () => { setDeleteText(''); setDeleteIgnore(true); setDeleteOpen(true) } },
        ]} />
      </div>
    </header>
    {notice && <Toast message={notice.text} kind={notice.kind} />}

    <div className="member-layout">
      <section className="panel member-score">
        <div className="panel-head"><div><h2>Why {formatScore(member.score)} points</h2></div><HelpLink topic="points" /></div>
        <table className="breakdown">
          <tbody>
            {typed.map((type) => {
              const row = breakdown.by_type[type]
              return <tr key={type}>
                <th scope="row">{TYPE_LABEL[type] ?? type}</th>
                <td className="count">{formatCount(row.count)}{row.zero ? <small>{row.zero} scored 0</small> : null}</td>
                <td className="score">{formatScore(row.points)}</td>
              </tr>
            })}
            {breakdown.adjustments.count > 0 && <tr>
              <th scope="row">Manual adjustments</th>
              <td className="count">{formatCount(breakdown.adjustments.count)}</td>
              <td className={`score ${breakdown.adjustments.points < 0 ? 'loss' : ''}`}>{breakdown.adjustments.points > 0 ? '+' : ''}{formatScore(breakdown.adjustments.points)}</td>
            </tr>}
            {!typed.length && !breakdown.adjustments.count && <tr><td colSpan={3} className="muted">Nothing has been counted for them this cycle.</td></tr>}
          </tbody>
          <tfoot><tr><th scope="row">Total this cycle</th><td /><td className="score total">{formatScore(breakdown.total)}</td></tr></tfoot>
        </table>
        {(zeroCount > 0 || breakdown.no_longer_counted > 0) && <p className="breakdown-note">
          {zeroCount > 0 && <>{zeroCount} action{zeroCount === 1 ? '' : 's'} scored 0: over the daily limit, or too short to count. </>}
          {breakdown.no_longer_counted > 0 && <>{breakdown.no_longer_counted} earlier action{breakdown.no_longer_counted === 1 ? ' no longer counts' : 's no longer count'}, because the tweet was deleted or is no longer public.</>}
        </p>}
      </section>

      <section className="panel member-facts-panel">
        <dl className="fact-list">
          <div><dt>X account</dt><dd>{member.twitter_user_id ? <a href={`https://x.com/${member.twitter_handle}`} target="_blank" rel="noreferrer">@{member.twitter_handle}</a> : <span className="muted">Not linked, so nothing can score</span>}{(member.x_status === 'suspended' || member.x_status === 'unavailable') && <span className="status failed"><i />X {member.x_status}</span>}{member.handle_history && <small>previously {member.handle_history.split('|').map((h) => `@${h}`).join(', ')}</small>}</dd></div>
          <div><dt>Follows</dt><dd>{followLabel}{member.follows_checked_at && <small>checked {formatDate(member.follows_checked_at)}</small>}</dd></div>
          <div><dt>Protection</dt><dd>{member.special_role ? (member.role_protected_names ? `By the Discord role ${member.role_protected_names}${member.special_role_manual ? ', and by hand' : ''}` : `By hand${member.special_role_names ? `: ${member.special_role_names}` : ''}`) : 'Not protected'}<small><HelpLink topic="protection" label="How protection works" /></small></dd></div>
          <div><dt>In the server</dt><dd>{member.discord_joined_at ? <>{formatDay(member.discord_joined_at)}<small>{daysAgo(member.discord_joined_at)} days</small></> : <span className="muted">Unknown until the next sync</span>}</dd></div>
          <div><dt>Last counted action</dt><dd>{member.last_active_at ? formatDate(member.last_active_at) : 'None this cycle'}</dd></div>
        </dl>
      </section>
    </div>

    {tool === 'edit' && <section className="panel member-tool">
      <div className="panel-head"><div><h2>Edit {member.discord_username}</h2></div></div>
      <form className="edit-grid" ref={editForm} onSubmit={saveEdit}>
        <label>Discord handle<input name="discord_username" defaultValue={member.discord_username} required maxLength={120} /></label>
        <label>X handle<input name="twitter_handle" defaultValue={member.twitter_handle} placeholder="e.g. @handle, checked on X when saved" /></label>
        <label className="check-row"><input type="checkbox" name="special_role" defaultChecked={member.special_role_manual} /> Protected by hand (never on the low-activity report)</label>
        <label>Protection label<input name="special_role_names" defaultValue={member.special_role_names} placeholder="e.g. Builder, Friend" /></label>
        <div className="modal-actions"><button type="button" className="button ghost" onClick={() => switchTool('none')} disabled={saving}>Cancel</button><button className="button primary" disabled={saving}>{saving ? 'Saving…' : 'Save changes'}</button></div>
      </form>
    </section>}

    {tool === 'points' && <section className="panel member-tool">
      <div className="panel-head"><div><h2>Add, remove or transfer points</h2></div><HelpLink topic="points" /></div>
      <p className="tool-note">Adjustments are kept apart from scanned actions, so scans and rescoring never undo them. Every one is logged in the Audit trail with who did it and why.</p>
      <form className="adjust-form" onSubmit={submitAdjust}>
        <div className="segmented adjust-mode"><button type="button" className={adjustMode === 'add' ? 'active' : ''} onClick={() => setAdjustMode('add')}>Add or remove</button><button type="button" className={adjustMode === 'transfer' ? 'active' : ''} onClick={() => setAdjustMode('transfer')}>Transfer to someone</button></div>
        <div className={`adjust-fields ${adjustMode === 'transfer' ? 'transfer' : ''}`}>
          <label>{adjustMode === 'transfer' ? 'Amount to move' : 'Points'}<input name="points" type="number" step="0.5" placeholder={adjustMode === 'transfer' ? 'e.g. 10' : 'e.g. 10 or -5'} required disabled={adjusting} /></label>
          {adjustMode === 'transfer' && <label>Receiver<input name="transfer_to" placeholder="Discord handle, Discord ID or X handle" required disabled={adjusting} autoCapitalize="none" spellCheck={false} /></label>}
          <label className="grow">Reason<input name="reason" placeholder="Shown in the audit trail and their history" maxLength={300} disabled={adjusting} /></label>
        </div>
        <div className="modal-actions left"><button className="button primary" disabled={adjusting}>{adjusting ? 'Saving…' : adjustMode === 'transfer' ? 'Transfer points' : 'Apply points'}</button></div>
      </form>
    </section>}

    {tool === 'scan' && member.twitter_user_id && <section className="panel member-tool">
      <div className="panel-head"><div><h2>Scan {member.discord_username}'s own timeline</h2></div><HelpLink topic="member-scan" label="What this scan counts" /></div>
      <p className="tool-note">Reads their timeline, replies included, back to the start of the window or until the depth is reached. It catches replies X hides everywhere else. Costs up to {formatUsd(memberDepth * 20 * 15)}, usually far less because it stops at the window start.</p>
      <div className="member-scan-controls"><div className="depth-picker"><span className="muted small">Latest tweets to read:</span><div className="segmented">{[100, 300, 500, 1000, 2000].map((n) => <button type="button" key={n} className={memberDepthTweets === n ? 'active' : ''} onClick={() => setMemberDepthTweets(n)} disabled={memberScanning}>{formatCount(n)}</button>)}</div><label className="depth-custom">custom<input type="number" min={20} max={5000} step={20} value={memberDepthTweets} onChange={(e) => setMemberDepthTweets(Math.min(5000, Math.max(20, Number(e.target.value) || 20)))} disabled={memberScanning} /></label></div><select value={memberPeriod} onChange={(e) => setMemberPeriod(e.target.value)} disabled={memberScanning} aria-label="Window"><option value="7d">Last 7 days</option><option value="30d">Last 30 days</option><option value="60d">Last 60 days</option><option value="90d">Last 90 days</option><option value="180d">Last 6 months</option><option value="365d">Last 12 months</option></select><button className="button primary" onClick={runMemberScan} disabled={memberScanning}>{memberScanning ? 'Scanning…' : 'Scan this member'}</button></div>
      {memberScan && <p className="import-summary">Read {memberScan.tweets_read} tweets · matched {memberScan.matched} ({memberScan.replies} replies, {memberScan.quotes} quotes, {memberScan.mentions} mentions) · {memberScan.new_actions} new · points {formatScore(memberScan.points_before)} → <strong>{formatScore(memberScan.points_after)}</strong>{memberScan.complete ? '' : ' · depth reached, older tweets skipped'} · saved under <a href="#scans">Scan reports</a></p>}
    </section>}

    <section className="panel member-history">
      <div className="panel-head"><div><h2>Every action this cycle</h2></div><span>{history ? `${formatCount(history.total)} action${history.total === 1 ? '' : 's'}` : ''}</span></div>
      {history?.items.length ? <div className="table-wrap"><table className="history-table"><thead><tr><th>Type</th><th>On</th><th>What they posted, and how it scored</th><th className="score">Points</th><th>When</th><th aria-label="Open on X" /></tr></thead><tbody>
        {history.items.map((item) => <tr key={item.action_key} className={item.active ? '' : 'muted-row'}><td><span className={`action-chip ${item.action_type}`}>{item.action_type}</span></td><td>@{item.target_handle}</td><td className="decision"><strong>{item.text || 'Native retweet'}</strong><small>{item.reason}{!item.active ? ' · no longer counts: deleted or not public' : ''}</small></td><td className={`score ${item.points > 0 ? 'gain' : ''}`}>{formatScore(item.points)}</td><td className="nowrap">{formatDate(item.occurred_at)}</td><td>{item.action_url && <a className="icon-button" href={item.action_url} target="_blank" rel="noreferrer" title="Open on X" aria-label="Open on X"><ExternalLink size={15} /></a>}</td></tr>)}
      </tbody></table></div> : history ? <Empty title="Nothing matched this cycle" copy="If they did interact, check that the X account above is the one they used, then run a scan that covers the date, or Scan this member." /> : <Loading label="Loading their actions…" />}
      {history && history.total > HISTORY_PAGE && <Pagination page={historyPage} size={HISTORY_PAGE} total={history.total} onChange={setHistoryPage} />}
    </section>

    {adjustments && adjustments.length > 0 && <section className="panel member-history">
      <div className="panel-head"><div><h2>Manual adjustments</h2></div><span>{adjustments.length}</span></div>
      <div className="table-wrap"><table><thead><tr><th>When</th><th className="score">Points</th><th>Reason</th><th>By</th><th>Other member</th></tr></thead><tbody>{adjustments.map((a) => <tr key={a.adjustment_id}><td className="nowrap">{formatDate(a.created_at)}</td><td className={`score ${a.points >= 0 ? 'gain' : 'loss'}`}>{a.points >= 0 ? '+' : ''}{formatScore(a.points)}</td><td>{a.reason || <span className="muted">No reason given</span>}</td><td className="mono">{a.actor_discord_id}</td><td className="mono">{a.counterpart_discord_id || '—'}</td></tr>)}</tbody></table></div>
    </section>}

    {deleteOpen && <div className="modal-backdrop" onMouseDown={() => { if (!deleting) setDeleteOpen(false) }}><div className="modal" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal-icon danger-icon"><Trash2 /></div><h2>Delete {member.discord_username} for good?</h2><HelpLink topic="members" label="Deactivating and deleting" />
      <p>This erases the record itself, not just their standing. Gone for good: <strong>{formatScore(member.score)} points</strong>, every scored action behind them, and every manual adjustment. Frozen leaderboards from closed cycles keep their copy, and so does the Audit trail, so past cycles still add up. Nothing happens to their Discord account.</p>
      <p className="muted small">To park somebody instead, keeping their history and points so you can bring them back, close this and use Deactivate.</p>
      <label className="check-row"><input type="checkbox" checked={deleteIgnore} onChange={(e) => setDeleteIgnore(e.target.checked)} disabled={deleting} /> Never let Sync from Discord add them back{member.active ? ' (they are still in the server, so leave this on)' : ''}</label>
      <label>Type DELETE to confirm<input autoFocus value={deleteText} onChange={(e) => setDeleteText(e.target.value)} placeholder="DELETE" disabled={deleting} /></label>
      <div className="modal-actions"><button className="button ghost" onClick={() => setDeleteOpen(false)} disabled={deleting}>Cancel</button><button className="button danger" disabled={deleting || deleteText.trim().toUpperCase() !== 'DELETE'} onClick={() => void removeMember()}>{deleting ? 'Deleting…' : 'Delete this record'}</button></div>
    </div></div>}
  </div>
}
