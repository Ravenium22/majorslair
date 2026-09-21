import { useMemo, useState, type ChangeEvent, type FormEvent } from 'react'
import { ArrowLeftRight, BadgeCheck, Download, ExternalLink, FileUp, Pencil, Plus, RefreshCw, Search, Shield, ShieldCheck, ShieldOff, Tags, UserRoundCheck, UserRoundX, X } from 'lucide-react'
import useSWR from 'swr'
import { api, formatDate, formatScore, mutateApi } from '../api'
import { Empty, Loading, PageHeader, Pagination, Toast, useEscape } from '../components'
import { parseCsv, rowsFromSheet, type ImportRow } from '../csv'
import type { Action, Adjustment, DiscordRole, DiscordSyncResponse, ImportResponse, ImportStatus, LinkedUser, MemberScanResult, Paginated, RoleBulkResult, Session, VerifyResponse } from '../types'

const STATUS_LABEL: Record<ImportStatus, string> = { linked: 'Linked', relinked: 'Handle updated', unchanged: 'Already linked', registered: 'Registered, no X', skipped: 'Skipped', conflict: 'Conflict', failed: 'Failed' }
const STATUS_TONE: Record<ImportStatus, string> = { linked: 'complete', relinked: 'complete', unchanged: 'active', registered: 'running', skipped: '', conflict: 'failed', failed: 'failed' }
const ATTENTION: ImportStatus[] = ['failed', 'conflict']
const SEGMENTS = [
  { id: 'everyone', label: 'Everyone', query: {} },
  { id: 'linked', label: 'Linked to X', query: { linked: 'true' } },
  { id: 'unlinked', label: 'No X yet', query: { linked: 'false' } },
  { id: 'xissues', label: 'X suspended', query: { x_ok: 'false' } },
] as const
const PROTECTION = [
  { id: 'any', label: 'Any role', query: {} },
  { id: 'protected', label: 'Protected', query: { protected: 'true' } },
  { id: 'regular', label: 'Not protected', query: { protected: 'false' } },
] as const
const POINTS = [
  { id: 'any', label: 'Any points' },
  { id: 'positive', label: 'Has points' },
  { id: 'zero', label: '0 points' },
  { id: 'low', label: 'Low activity' },
] as const
const JOINED = [
  { id: 'any', label: 'Any join date' },
  { id: 'new', label: 'Newcomers' },
  { id: 'established', label: 'Established' },
] as const
const daysAgo = (iso: string) => Math.floor((Date.now() - new Date(iso).getTime()) / 86400000)
const SORTS = [
  { id: 'score_desc', label: 'Points: high to low' },
  { id: 'score_asc', label: 'Points: low to high' },
  { id: 'name', label: 'Discord handle A→Z' },
  { id: 'last_signal', label: 'Most recent activity' },
  { id: 'linked_at', label: 'Recently added' },
  { id: 'joined', label: 'Joined Discord: newest' },
] as const

export default function MembersPage({ session }: { session: Session }) {
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('active')
  const [segment, setSegment] = useState<(typeof SEGMENTS)[number]['id']>('everyone')
  const [protection, setProtection] = useState<(typeof PROTECTION)[number]['id']>('any')
  const [points, setPoints] = useState<(typeof POINTS)[number]['id']>('any')
  const [joined, setJoined] = useState<(typeof JOINED)[number]['id']>('any')
  const [sort, setSort] = useState<(typeof SORTS)[number]['id']>('score_desc')
  const [page, setPage] = useState(1)
  const [showLink, setShowLink] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const [importRows, setImportRows] = useState<ImportRow[]>([])
  const [importName, setImportName] = useState('')
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState<ImportResponse>()
  const [syncing, setSyncing] = useState(false)
  const [syncResult, setSyncResult] = useState<DiscordSyncResponse>()
  const [selected, setSelected] = useState<LinkedUser>()
  const [memberPeriod, setMemberPeriod] = useState('30d')
  const [memberDepth, setMemberDepth] = useState(25)
  const [memberScanning, setMemberScanning] = useState(false)
  const [memberScan, setMemberScan] = useState<MemberScanResult>()
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const { data: history, mutate: mutateHistory } = useSWR<Paginated<Action>>(selected ? `/api/actions?discord_user_id=${selected.discord_user_id}&page_size=100` : null, api)
  const { data: adjustments, mutate: mutateAdjustments } = useSWR<Adjustment[]>(selected ? `/api/users/${selected.discord_user_id}/adjustments` : null, api)
  const [adjustMode, setAdjustMode] = useState<'add' | 'transfer'>('add')
  const [adjusting, setAdjusting] = useState(false)
  const [verifying, setVerifying] = useState(false)
  const [verifyResult, setVerifyResult] = useState<VerifyResponse>()
  const [verifyPlan, setVerifyPlan] = useState<{ linked: number; protectedLinked: number }>()
  const [skipProtected, setSkipProtected] = useState(false)
  const [notice, setNotice] = useState<{ text: string; kind: 'success' | 'error' }>()
  const [showRoles, setShowRoles] = useState(false)
  const [roles, setRoles] = useState<{ roles: DiscordRole[]; bot_can_manage_roles: boolean }>()
  const [roleId, setRoleId] = useState('')
  const [roleAction, setRoleAction] = useState<'add' | 'remove'>('add')
  const [roleMin, setRoleMin] = useState('')
  const [roleMax, setRoleMax] = useState('')
  const [rolePreview, setRolePreview] = useState<RoleBulkResult>()
  const [roleResult, setRoleResult] = useState<RoleBulkResult>()
  const [roleBusy, setRoleBusy] = useState(false)
  const filterQuery = useMemo(() => new URLSearchParams({
    search,
    ...(filter !== 'all' ? { active: String(filter === 'active') } : {}),
    ...SEGMENTS.find((item) => item.id === segment)?.query,
    ...PROTECTION.find((item) => item.id === protection)?.query,
    ...(points !== 'any' ? { points } : {}),
    ...(joined !== 'any' ? { joined } : {}),
    ...(sort !== 'score_desc' ? { sort } : {}),
  }).toString(), [search, filter, segment, protection, points, joined, sort])
  const query = `${filterQuery}&page=${page}&page_size=25`
  const { data, mutate, isLoading } = useSWR<Paginated<LinkedUser>>(`/api/users?${query}`, api)
  const resetPage = <T,>(setter: (value: T) => void) => (value: T) => { setter(value); setPage(1) }
  const hasFilters = Boolean(search) || filter !== 'active' || segment !== 'everyone' || protection !== 'any' || points !== 'any' || joined !== 'any'
  const clearFilters = () => { setSearch(''); setFilter('active'); setSegment('everyone'); setProtection('any'); setPoints('any'); setJoined('any'); setSort('score_desc'); setPage(1) }

  const patch = async (user: LinkedUser, body: Record<string, unknown>, success: string) => {
    try {
      await mutateApi(`/api/users/${user.discord_user_id}`, session.csrf_token, 'PATCH', body)
      setNotice({ text: success, kind: 'success' })
      await mutate()
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Update failed', kind: 'error' }) }
  }
  const toggleActive = (user: LinkedUser) => patch(user, { active: !user.active }, `${user.discord_username} ${user.active ? 'deactivated' : 'reactivated'}.`)
  const toggleProtected = (user: LinkedUser) => patch(user, { special_role: !user.special_role }, `${user.discord_username} is ${user.special_role ? 'no longer protected' : 'now protected from the low-activity report'}.`)

  const [linking, setLinking] = useState(false)
  const link = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const values = Object.fromEntries(new FormData(event.currentTarget))
    setLinking(true)
    try {
      await mutateApi('/api/users/link', session.csrf_token, 'POST', values)
      setNotice({ text: 'Member linked and X profile verified.', kind: 'success' })
      setShowLink(false)
      await mutate()
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Link failed', kind: 'error' }) }
    finally { setLinking(false) }
  }

  const closeImport = () => { if (importing) return; setShowImport(false); setImportRows([]); setImportName(''); setImportResult(undefined) }

  const pickFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    try {
      setImportRows(rowsFromSheet(parseCsv(await file.text())))
      setImportName(file.name)
      setImportResult(undefined)
    } catch (error) {
      setImportRows([])
      setNotice({ text: error instanceof Error ? error.message : 'Could not read the file', kind: 'error' })
    }
  }

  const runImport = async () => {
    if (!importRows.length) return
    setImporting(true)
    try {
      const response = await mutateApi<ImportResponse>('/api/users/import', session.csrf_token, 'POST', { rows: importRows })
      setImportResult(response)
      const linked = (response.summary.linked ?? 0) + (response.summary.relinked ?? 0)
      const attention = ATTENTION.reduce((sum, key) => sum + (response.summary[key] ?? 0), 0)
      setNotice({ text: `Import finished: ${linked} linked, ${response.summary.registered ?? 0} registered without X, ${attention} need attention.`, kind: 'success' })
      await mutate()
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Import failed', kind: 'error' }) }
    finally { setImporting(false) }
  }

  const runSync = async () => {
    setSyncing(true)
    try {
      const response = await mutateApi<DiscordSyncResponse>('/api/users/sync-discord', session.csrf_token, 'POST')
      setSyncResult(response)
      setNotice({ text: `Discord sync: ${response.added.length} new members registered · ${response.already_registered_active} already present + ${response.already_registered_inactive} inactive · ${response.bots_skipped} bots skipped. Registry now ${response.registry_active} active + ${response.registry_inactive} inactive.`, kind: 'success' })
      await mutate()
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Discord sync failed', kind: 'error' }) }
    finally { setSyncing(false) }
  }

  const openVerify = async () => {
    try {
      const [all, prot] = await Promise.all([
        api<Paginated<LinkedUser>>('/api/users?active=true&linked=true&page_size=1'),
        api<Paginated<LinkedUser>>('/api/users?active=true&linked=true&protected=true&page_size=1'),
      ])
      setVerifyPlan({ linked: all.total, protectedLinked: prot.total })
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Could not count members', kind: 'error' }) }
  }

  const runVerify = async () => {
    setVerifying(true)
    try {
      const response = await mutateApi<VerifyResponse>('/api/users/verify-x', session.csrf_token, 'POST', { skip_protected: skipProtected })
      setVerifyPlan(undefined)
      setVerifyResult(response)
      setNotice({ text: `Checked ${response.checked} X accounts: ${response.unavailable.length} suspended or gone, ${response.renamed.length} renamed and updated.`, kind: 'success' })
      await mutate()
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Verification failed', kind: 'error' }) }
    finally { setVerifying(false) }
  }

  const withHandles = importRows.filter((row) => row.twitter_handle).length
  const withSpecial = importRows.filter((row) => row.special_role).length

  const openMember = (user: LinkedUser) => { setSelected(user); setEditing(false); setMemberScan(undefined) }

  const roleFilters = () => ({
    search,
    active: filter === 'all' ? null : filter === 'active',
    ...(segment === 'linked' ? { linked: true } : segment === 'unlinked' ? { linked: false } : {}),
    ...(segment === 'xissues' ? { x_ok: false } : {}),
    ...(protection !== 'any' ? { protected: protection === 'protected' } : {}),
    points,
    joined,
    min_score: roleMin === '' ? null : Number(roleMin),
    max_score: roleMax === '' ? null : Number(roleMax),
  })

  const openRoles = async () => {
    setShowRoles(true)
    setRoleResult(undefined)
    setRolePreview(undefined)
    try { setRoles(await api('/api/discord/roles')) } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Could not load roles', kind: 'error' }) }
  }

  const previewRoles = async () => {
    setRoleBusy(true)
    try { setRolePreview(await mutateApi<RoleBulkResult>('/api/users/roles', session.csrf_token, 'POST', { role_id: roleId || '000000', action: roleAction, filters: roleFilters(), dry_run: true })) }
    catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Preview failed', kind: 'error' }) }
    finally { setRoleBusy(false) }
  }

  const applyRoles = async () => {
    if (!roleId) { setNotice({ text: 'Pick a role first', kind: 'error' }); return }
    setRoleBusy(true)
    try {
      const result = await mutateApi<RoleBulkResult>('/api/users/roles', session.csrf_token, 'POST', { role_id: roleId, action: roleAction, filters: roleFilters(), dry_run: false })
      setRoleResult(result)
      setNotice({ text: `${roleAction === 'add' ? 'Gave' : 'Removed'} the role for ${result.changed?.length ?? 0} of ${result.matched} members${result.failed?.length ? `, ${result.failed.length} failed` : ''}.`, kind: result.failed?.length ? 'error' : 'success' })
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Role update failed', kind: 'error' }) }
    finally { setRoleBusy(false) }
  }

  const submitAdjust = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!selected) return
    const form = new FormData(event.currentTarget)
    const points = Number(form.get('points'))
    const reason = String(form.get('reason') ?? '').trim()
    const transfer_to = adjustMode === 'transfer' ? String(form.get('transfer_to') ?? '').trim() : ''
    if (!points || Number.isNaN(points)) { setNotice({ text: 'Enter a non-zero amount', kind: 'error' }); return }
    if (adjustMode === 'transfer' && !transfer_to) { setNotice({ text: 'Enter who receives the points', kind: 'error' }); return }
    setAdjusting(true)
    try {
      const result = await mutateApi<{ member: LinkedUser | null }>(`/api/users/${selected.discord_user_id}/adjust`, session.csrf_token, 'POST', { points: adjustMode === 'transfer' ? Math.abs(points) : points, reason, transfer_to: transfer_to || null })
      if (result.member) setSelected(result.member)
      setNotice({ text: adjustMode === 'transfer' ? `Moved ${formatScore(Math.abs(points))} points from ${selected.discord_username} to ${transfer_to}.` : `${points > 0 ? '+' : ''}${formatScore(points)} points for ${selected.discord_username}.`, kind: 'success' })
      ;(event.target as HTMLFormElement).reset()
      await Promise.all([mutate(), mutateAdjustments()])
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Adjustment failed', kind: 'error' }) }
    finally { setAdjusting(false) }
  }

  const runMemberScan = async () => {
    if (!selected) return
    setMemberScanning(true)
    try {
      const result = await mutateApi<MemberScanResult>(`/api/users/${selected.discord_user_id}/scan`, session.csrf_token, 'POST', { period: memberPeriod, max_pages: memberDepth })
      setMemberScan(result)
      setSelected({ ...selected, score: result.points_after })
      setNotice({ text: `${result.discord_username}: ${result.matched} actions matched, ${result.new_actions} new, ${formatScore(result.points_before)} → ${formatScore(result.points_after)} pts.`, kind: 'success' })
      await Promise.all([mutate(), mutateHistory()])
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Member scan failed', kind: 'error' }) }
    finally { setMemberScanning(false) }
  }
  const closeMember = () => { if (saving) return; setSelected(undefined); setEditing(false) }

  const saveEdit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!selected) return
    const form = new FormData(event.currentTarget)
    const discord_username = String(form.get('discord_username') ?? '').trim()
    const twitter_handle = String(form.get('twitter_handle') ?? '').trim().replace(/^@/, '')
    const special_role = form.get('special_role') === 'on'
    const special_role_names = String(form.get('special_role_names') ?? '').trim()
    setSaving(true)
    try {
      const body: Record<string, unknown> = {}
      if (discord_username && discord_username !== selected.discord_username) body.discord_username = discord_username
      if (special_role !== selected.special_role || special_role_names !== selected.special_role_names) { body.special_role = special_role; body.special_role_names = special_role ? special_role_names : '' }
      let updated = selected
      if (Object.keys(body).length) updated = await mutateApi<LinkedUser>(`/api/users/${selected.discord_user_id}`, session.csrf_token, 'PATCH', body)
      if (twitter_handle && twitter_handle.toLowerCase() !== selected.twitter_handle.toLowerCase()) {
        await mutateApi('/api/users/link', session.csrf_token, 'POST', { discord_user_id: selected.discord_user_id, discord_username: discord_username || selected.discord_username, twitter_handle })
        const fresh = await api<Paginated<LinkedUser>>(`/api/users?search=${encodeURIComponent(selected.discord_user_id)}&page_size=1`)
        updated = fresh.items[0] ?? updated
      }
      setSelected(updated)
      setEditing(false)
      setNotice({ text: `${updated.discord_username} updated.`, kind: 'success' })
      await Promise.all([mutate(), mutateHistory()])
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Update failed', kind: 'error' }) }
    finally { setSaving(false) }
  }

  const anyModal = Boolean(showLink || showImport || showRoles || selected || syncResult || verifyResult || verifyPlan)
  const anyBusy = importing || saving || roleBusy || verifying || memberScanning || syncing || adjusting
  useEscape(anyModal && !anyBusy, () => { setShowLink(false); setShowImport(false); setShowRoles(false); setSelected(undefined); setEditing(false); setSyncResult(undefined); setVerifyResult(undefined); setVerifyPlan(undefined) })

  return <div className="page">
    <PageHeader eyebrow="Community registry" title="Linked members" copy="Everyone in the community, with or without an X account. Protected members never appear in the low-activity report." actions={<button className="button primary" onClick={() => setShowLink(true)}><Plus size={17} /> Link member</button>} toolbar={<>
      <button className="button" onClick={openVerify} disabled={verifying} title="Check linked X accounts for suspensions, deletions, and renames (about 10 credits each)"><BadgeCheck size={17} className={verifying ? 'spin' : ''} /> {verifying ? 'Checking X…' : 'Verify X accounts'}</button>
      <button className="button" onClick={runSync} disabled={syncing} title="Register every human member of the Discord server who is missing here"><RefreshCw size={17} className={syncing ? 'spin' : ''} /> {syncing ? 'Syncing…' : 'Sync from Discord'}</button>
      <button className="button" onClick={openRoles}><Tags size={17} /> Give role</button>
      <button className="button" onClick={() => setShowImport(true)}><FileUp size={17} /> Import CSV</button>
      <a className="button" href={`/api/users/export?${filterQuery}`} title="Download the list exactly as filtered below"><Download size={17} /> Export CSV</a>
    </>} />
    {notice && <Toast message={notice.text} kind={notice.kind} />}
    <section className="panel">
      <div className="toolbar filters">
        <label className="search"><Search size={17} /><input value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} placeholder="Search Discord handle, X handle, ID, or role" /></label>
        <select value={sort} onChange={(e) => resetPage(setSort)(e.target.value as typeof sort)} aria-label="Sort">{SORTS.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select>
      </div>
      <div className="toolbar filters wrap">
        <div className="segmented">{SEGMENTS.map((item) => <button className={segment === item.id ? 'active' : ''} onClick={() => resetPage(setSegment)(item.id)} key={item.id}>{item.label}</button>)}</div>
        <div className="segmented">{PROTECTION.map((item) => <button className={protection === item.id ? 'active' : ''} onClick={() => resetPage(setProtection)(item.id)} key={item.id}>{item.label}</button>)}</div>
        <div className="segmented">{POINTS.map((item) => <button className={points === item.id ? 'active' : ''} onClick={() => resetPage(setPoints)(item.id)} key={item.id} title={item.id === 'low' ? 'At or below the low-activity threshold from Scoring rules' : undefined}>{item.label}</button>)}</div>
        <div className="segmented">{JOINED.map((item) => <button className={joined === item.id ? 'active' : ''} onClick={() => resetPage(setJoined)(item.id)} key={item.id} title={item.id === 'new' ? 'Joined Discord within the grace period (newcomer_grace_days in Scoring rules); never in the low-activity report' : item.id === 'established' ? 'Joined before the grace period, or join date unknown' : undefined}>{item.label}</button>)}</div>
        <div className="segmented">{['active', 'inactive', 'all'].map((value) => <button className={filter === value ? 'active' : ''} onClick={() => resetPage(setFilter)(value)} key={value}>{value}</button>)}</div>
        <span className="filter-count">{data ? `${data.total.toLocaleString()} member${data.total === 1 ? '' : 's'}` : ''}{hasFilters && <button className="link-button" onClick={clearFilters}>Clear filters</button>}</span>
      </div>
      <div className="table-wrap"><table><thead><tr><th>Discord</th><th>X identity</th><th>Special role</th><th>Score</th><th>Last signal</th><th>Joined Discord</th><th>Status</th><th aria-label="Actions" /></tr></thead><tbody>
        {data?.items.map((user) => <tr key={user.discord_user_id} className="member-row" onClick={() => openMember(user)}>
          <td><span className="member-cell"><strong>{user.discord_username}</strong><small className="mono">{user.discord_user_id}</small></span></td>
          <td>{user.twitter_user_id ? <span className="protected-cell"><a href={`https://x.com/${user.twitter_handle}`} target="_blank">@{user.twitter_handle}</a>{(user.x_status === 'suspended' || user.x_status === 'unavailable') && <span className="status failed"><i />X {user.x_status}</span>}</span> : <span className="muted">Not linked</span>}</td>
          <td>{user.special_role ? <span className="protected-cell"><span className="status complete"><i />Protected</span>{user.special_role_names && <small>{user.special_role_names}</small>}</span> : <span className="muted">—</span>}</td>
          <td className="score">{formatScore(user.score)}</td>
          <td>{formatDate(user.last_active_at)}</td>
          <td>{user.discord_joined_at ? <span className="protected-cell">{formatDate(user.discord_joined_at)}<small>{daysAgo(user.discord_joined_at)} days ago</small></span> : <span className="muted" title="Run Sync from Discord to fill join dates">—</span>}</td>
          <td><span className={`status ${user.active ? 'complete' : 'failed'}`}><i />{user.active ? 'Active' : 'Inactive'}</span></td>
          <td className="row-actions" onClick={(e) => e.stopPropagation()}>
            <button className="icon-button" title="Open member: history and edit" onClick={() => { openMember(user); setEditing(true) }}><Pencil size={17} /></button>
            <button className="icon-button" title={user.special_role ? 'Remove protection' : 'Protect from low-activity report'} onClick={() => toggleProtected(user)}>{user.special_role ? <ShieldOff size={18} /> : <Shield size={18} />}</button>
            <button className="icon-button" title={user.active ? 'Deactivate' : 'Reactivate'} onClick={() => toggleActive(user)}>{user.active ? <UserRoundX size={18} /> : <UserRoundCheck size={18} />}</button>
          </td>
        </tr>)}
      </tbody></table></div>
      {isLoading && !data && <Loading label="Loading members…" />}
      {!isLoading && !data?.items.length && <Empty title="No matching members" copy="Change the filters, sync from Discord, or import the community sheet." />}
      <Pagination page={page} size={25} total={data?.total ?? 0} onChange={setPage} />
    </section>

    {selected && <div className="modal-backdrop" onMouseDown={closeMember}><div className="modal modal-wide member-drawer" onMouseDown={(e) => e.stopPropagation()}>
      <button className="icon-button drawer-close" onClick={closeMember} aria-label="Close"><X size={18} /></button>
      <p className="eyebrow">Member</p>
      <h2>{selected.discord_username} <small className="mono">{selected.discord_user_id}</small></h2>
      <div className="member-facts">
        <span>{selected.twitter_user_id ? <a href={`https://x.com/${selected.twitter_handle}`} target="_blank">@{selected.twitter_handle}</a> : <em className="muted">no X linked</em>}</span>
        <span className="score">{formatScore(selected.score)} pts this cycle</span>
        {selected.special_role && <span className="status complete"><i />Protected{selected.special_role_names ? ` · ${selected.special_role_names}` : ''}</span>}
        {(selected.x_status === 'suspended' || selected.x_status === 'unavailable') && <span className="status failed"><i />X {selected.x_status}</span>}
        <span className={`status ${selected.active ? 'complete' : 'failed'}`}><i />{selected.active ? 'Active' : 'Inactive'}</span>
        {selected.discord_joined_at && <span className="muted">joined Discord {formatDate(selected.discord_joined_at)} ({daysAgo(selected.discord_joined_at)} days ago)</span>}
        {selected.handle_history && <span className="muted">previous X: {selected.handle_history.split('|').map((h) => `@${h}`).join(', ')}</span>}
      </div>
      {!editing ? <div className="modal-actions left"><button className="button" onClick={() => setEditing(true)}><Pencil size={15} /> Edit member</button></div> : <form className="edit-grid" onSubmit={saveEdit}>
        <label>Discord handle<input name="discord_username" defaultValue={selected.discord_username} required maxLength={120} /></label>
        <label>X handle<input name="twitter_handle" defaultValue={selected.twitter_handle} placeholder="handle (verified on save)" /></label>
        <label className="check-row"><input type="checkbox" name="special_role" defaultChecked={selected.special_role} /> Protected (never in the low-activity report)</label>
        <label>Special role names<input name="special_role_names" defaultValue={selected.special_role_names} placeholder="Builder, Friend" /></label>
        <div className="modal-actions"><button type="button" className="button ghost" onClick={() => setEditing(false)} disabled={saving}>Cancel</button><button className="button primary" disabled={saving}>{saving ? 'Saving…' : 'Save changes'}</button></div>
      </form>}
      <div className="member-scan adjust-panel">
        <div><h3 className="sub-heading"><ArrowLeftRight size={13} /> Points: add, remove or transfer</h3><p className="muted small">Manual adjustments are kept separately from scanned actions, so scans and rescoring never undo them. Every one is logged in the Audit trail with who did it and why.</p></div>
        <form className="adjust-form" onSubmit={submitAdjust}>
          <div className="segmented"><button type="button" className={adjustMode === 'add' ? 'active' : ''} onClick={() => setAdjustMode('add')}>Add / remove</button><button type="button" className={adjustMode === 'transfer' ? 'active' : ''} onClick={() => setAdjustMode('transfer')}>Transfer to someone</button></div>
          <input name="points" type="number" step="0.5" placeholder={adjustMode === 'transfer' ? 'Amount to move' : 'Points, e.g. 10 or -5'} required disabled={adjusting} />
          {adjustMode === 'transfer' && <input name="transfer_to" placeholder="Receiver: Discord handle, Discord ID or X handle" required disabled={adjusting} />}
          <input name="reason" placeholder="Reason (shown in the audit trail)" maxLength={300} disabled={adjusting} />
          <button className="button primary" disabled={adjusting}>{adjusting ? 'Saving…' : adjustMode === 'transfer' ? 'Transfer points' : 'Apply'}</button>
        </form>
        {adjustments && adjustments.length > 0 && <div className="table-wrap"><table><thead><tr><th>When</th><th>Points</th><th>Reason</th><th>By</th><th>Counterpart</th></tr></thead><tbody>{adjustments.map((a) => <tr key={a.adjustment_id}><td>{formatDate(a.created_at)}</td><td className={`score ${a.points >= 0 ? 'gain' : 'loss'}`}>{a.points >= 0 ? '+' : ''}{formatScore(a.points)}</td><td className="muted">{a.reason || '—'}</td><td className="mono">{a.actor_discord_id}</td><td className="mono">{a.counterpart_discord_id || '—'}</td></tr>)}</tbody></table></div>}
      </div>
      {selected.twitter_user_id && <div className="member-scan">
        <div><h3 className="sub-heading">Scan this member only</h3><p className="muted small">Reads their own timeline (replies included) back to the start of the period or until the depth is reached, whichever comes first. Costs up to {(memberDepth * 20 * 15).toLocaleString()} credits (${((memberDepth * 20 * 15) / 100000).toFixed(2)}), usually far less because it stops at the period start. Catches replies X hides everywhere else. Pick a bigger depth for long periods on active posters.</p></div>
        <div className="member-scan-controls"><select value={memberDepth} onChange={(e) => setMemberDepth(Number(e.target.value))} disabled={memberScanning} title="How far back into their timeline to read, at most"><option value={5}>Depth: latest 100 tweets</option><option value={25}>Depth: latest 500 tweets</option><option value={50}>Depth: latest 1,000 tweets</option><option value={100}>Depth: latest 2,000 tweets</option></select><select value={memberPeriod} onChange={(e) => setMemberPeriod(e.target.value)} disabled={memberScanning}><option value="7d">Last 7 days</option><option value="30d">Last 30 days</option><option value="60d">Last 60 days</option><option value="90d">Last 90 days</option><option value="180d">Last 6 months</option><option value="365d">Last 12 months</option></select><button className="button primary" onClick={runMemberScan} disabled={memberScanning}>{memberScanning ? 'Scanning…' : 'Scan this member'}</button></div>
        {memberScan && <p className="import-summary">Read {memberScan.tweets_read} tweets · matched {memberScan.matched} ({memberScan.replies} replies, {memberScan.quotes} quotes, {memberScan.mentions} mentions) · {memberScan.new_actions} new · points {formatScore(memberScan.points_before)} → <strong>{formatScore(memberScan.points_after)}</strong>{memberScan.complete ? '' : ' · timeline page cap reached, older tweets skipped'} · ≈ {(Math.max(memberScan.items_returned, memberScan.api_requests) * 15).toLocaleString()} credits</p>}
      </div>}
      <h3 className="sub-heading">Engagement this cycle · {history ? `${history.total} action${history.total === 1 ? '' : 's'}${history.total > history.items.length ? ` · showing the latest ${history.items.length}` : ''}` : '…'}</h3>
      <p className="muted small">Every reply, quote, retweet and mention the scans matched to this member, with the points decision. Zero-point rows show why.</p>
      {history?.items.length ? <div className="table-wrap standings-table"><table><thead><tr><th>Type</th><th>Target</th><th>Content / decision</th><th>Points</th><th>When</th><th /></tr></thead><tbody>
        {history.items.map((item) => <tr key={item.action_key} className={item.active ? '' : 'muted-row'}><td><span className={`action-chip ${item.action_type}`}>{item.action_type}</span></td><td>@{item.target_handle}</td><td className="decision"><strong>{item.text || 'Native retweet'}</strong><small>{item.reason}{!item.active ? ' · no longer public' : ''}</small></td><td className={`score ${item.points > 0 ? 'gain' : ''}`}>{formatScore(item.points)}</td><td>{formatDate(item.occurred_at)}</td><td>{item.action_url && <a className="icon-button" href={item.action_url} target="_blank" title="Open on X"><ExternalLink size={15} /></a>}</td></tr>)}
      </tbody></table></div> : history ? <p className="muted small">No matched actions in this cycle. If they did interact, check the X handle above is the account they used, then run a scan that covers the date.</p> : null}
    </div></div>}

    {showRoles && <div className="modal-backdrop" onMouseDown={() => { if (!roleBusy) setShowRoles(false) }}><div className="modal modal-wide" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal-icon"><Tags /></div><p className="eyebrow">Discord roles</p><h2>Give or remove a role in bulk</h2>
      <p>Applies to the members matching the <strong>filters currently set on this page</strong> (search, X state, protection, points, join date, active), optionally narrowed by a points range below. Preview first, then apply. Every run is logged in the Audit trail.</p>
      {roles && !roles.bot_can_manage_roles && <p className="estimate-warning">The bot has no <strong>Manage Roles</strong> permission in Discord. Server Settings → Roles → the bot's role → enable Manage Roles, and drag the bot's role above the roles you want it to give.</p>}
      <div className="role-grid">
        <label>Role<select value={roleId} onChange={(e) => setRoleId(e.target.value)} disabled={roleBusy}><option value="">Pick a role…</option>{roles?.roles.map((r) => <option key={r.id} value={r.id} disabled={!r.assignable}>{r.name}{r.assignable ? '' : r.managed ? ' (managed by an integration)' : ' (above the bot, cannot assign)'}</option>)}</select></label>
        <label>Action<select value={roleAction} onChange={(e) => setRoleAction(e.target.value as 'add' | 'remove')} disabled={roleBusy}><option value="add">Give the role</option><option value="remove">Remove the role</option></select></label>
        <label>Min points<input type="number" step="1" value={roleMin} onChange={(e) => setRoleMin(e.target.value)} placeholder="any" disabled={roleBusy} /></label>
        <label>Max points<input type="number" step="1" value={roleMax} onChange={(e) => setRoleMax(e.target.value)} placeholder="any" disabled={roleBusy} /></label>
      </div>
      <p className="muted small">Current page filters: {[segment !== 'everyone' ? SEGMENTS.find((s) => s.id === segment)?.label : null, protection !== 'any' ? PROTECTION.find((s) => s.id === protection)?.label : null, points !== 'any' ? POINTS.find((s) => s.id === points)?.label : null, joined !== 'any' ? JOINED.find((s) => s.id === joined)?.label : null, filter, search ? `search "${search}"` : null].filter(Boolean).join(' · ') || 'none'}</p>
      {rolePreview && !roleResult && <><p className="import-summary"><strong>{rolePreview.matched}</strong> members match. {rolePreview.matched > 500 ? 'Showing the first 500.' : ''}</p><div className="table-wrap import-results"><table><tbody>{rolePreview.members?.map((m) => <tr key={m.discord_user_id}><td><span className="member-cell"><strong>{m.discord_username}</strong><small className="mono">{m.discord_user_id}</small></span></td><td className="score">{formatScore(m.score)}</td><td>{m.special_role ? <span className="status complete"><i />Protected</span> : ''}</td></tr>)}</tbody></table></div></>}
      {roleResult && <><p className="import-summary">{roleAction === 'add' ? 'Gave' : 'Removed'} the role for <strong>{roleResult.changed?.length ?? 0}</strong> of {roleResult.matched} members{roleResult.failed?.length ? ` · ${roleResult.failed.length} failed` : ''}.</p>{roleResult.failed && roleResult.failed.length > 0 && <div className="table-wrap import-results"><table><tbody>{roleResult.failed.map((f) => <tr key={f.discord_user_id}><td><span className="member-cell"><strong>{f.discord_username}</strong><small className="mono">{f.discord_user_id}</small></span></td><td className="muted">{f.error}</td></tr>)}</tbody></table></div>}</>}
      <div className="modal-actions"><button type="button" className="button ghost" onClick={() => setShowRoles(false)} disabled={roleBusy}>{roleResult ? 'Done' : 'Cancel'}</button>{!roleResult && <button type="button" className="button" onClick={previewRoles} disabled={roleBusy}>{roleBusy ? 'Working…' : 'Preview who matches'}</button>}{!roleResult && <button type="button" className="button primary" onClick={applyRoles} disabled={roleBusy || !roleId || !rolePreview} title={!rolePreview ? 'Preview first' : undefined}>{roleBusy ? 'Working…' : !rolePreview ? 'Preview first' : roleAction === 'add' ? `Give role to ${rolePreview.matched} members` : `Remove role from ${rolePreview.matched} members`}</button>}</div>
    </div></div>}

    {showLink && <div className="modal-backdrop" onMouseDown={() => setShowLink(false)}><form className="modal" onSubmit={link} onMouseDown={(e) => e.stopPropagation()}><div className="modal-icon"><ShieldCheck /></div><p className="eyebrow">Verified identity</p><h2>Link a member</h2><p>Use the Discord username (the handle shown in the profile, not the nickname). The X handle is resolved through twitterapi.io and its stable account ID is stored.</p><label>Discord user ID<input required name="discord_user_id" pattern="\d+" inputMode="numeric" placeholder="123456789012345678" disabled={linking} /><small className="field-hint">Discord → User Settings → Advanced → Developer Mode on, then right-click the member → Copy User ID.</small></label><label>Discord handle<input required name="discord_username" placeholder="luna.luna12" autoCapitalize="none" spellCheck={false} disabled={linking} /></label><label>X handle<input required name="twitter_handle" placeholder="@handle" autoCapitalize="none" spellCheck={false} disabled={linking} /></label><div className="modal-actions"><button type="button" className="button ghost" onClick={() => setShowLink(false)} disabled={linking}>Cancel</button><button className="button primary" disabled={linking}>{linking ? 'Verifying on X…' : 'Verify & link'}</button></div></form></div>}

    {showImport && <div className="modal-backdrop" onMouseDown={closeImport}><div className="modal modal-wide" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal-icon"><FileUp /></div><p className="eyebrow">Bulk registry</p><h2>Import members from a spreadsheet</h2>
      {!importResult && <>
        <p>Export the sheet as CSV. The first row must contain a <code>discord_id</code> column. Optional columns: <code>discord_username</code> (the Discord handle), <code>x_handle</code>, <code>SPECIAL ROLE</code> (YES/NO) and <code>SPECIAL ROLE NAMES</code>. Everyone in the sheet is registered; handles are verified through twitterapi.io (about 18 credits each) and members already linked to the same handle are left untouched. Re-importing is safe.</p>
        <label>CSV file<input type="file" accept=".csv,text/csv" onChange={pickFile} disabled={importing} /></label>
        {importRows.length > 0 && <p className="import-summary"><strong>{importName}</strong>: {importRows.length} members found · {withHandles} with an X handle · {importRows.length - withHandles} without one (registered anyway) · {withSpecial} marked as special role.</p>}
        <div className="modal-actions"><button type="button" className="button ghost" onClick={closeImport} disabled={importing}>Cancel</button><button className="button primary" onClick={runImport} disabled={!importRows.length || importing}>{importing ? 'Verifying handles…' : `Import ${importRows.length || ''}`}</button></div>
      </>}
      {importResult && <>
        <p className="import-summary">{(['linked', 'relinked', 'unchanged', 'registered', 'skipped', 'conflict', 'failed'] as ImportStatus[]).filter((key) => importResult.summary[key]).map((key) => `${importResult.summary[key]} ${STATUS_LABEL[key].toLowerCase()}`).join(' · ')}</p>
        <div className="table-wrap import-results"><table><thead><tr><th>Member</th><th>X handle</th><th>Result</th><th>Note</th></tr></thead><tbody>
          {[...importResult.results].sort((a, b) => Number(ATTENTION.includes(b.status)) - Number(ATTENTION.includes(a.status))).map((row) => <tr key={row.discord_user_id}><td><span className="member-cell"><strong>{row.discord_username}</strong><small className="mono">{row.discord_user_id}</small></span></td><td>{row.twitter_handle ? <a href={`https://x.com/${row.twitter_handle}`} target="_blank">@{row.twitter_handle}</a> : <span className="muted">none</span>}</td><td><span className={`status ${STATUS_TONE[row.status]}`}><i />{STATUS_LABEL[row.status]}</span></td><td className="muted">{row.message}</td></tr>)}
        </tbody></table></div>
        <div className="modal-actions"><button className="button primary" onClick={closeImport}>Done</button></div>
      </>}
    </div></div>}

    {verifyPlan && <div className="modal-backdrop" onMouseDown={() => { if (!verifying) setVerifyPlan(undefined) }}><div className="modal" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal-icon"><BadgeCheck /></div><p className="eyebrow">X verification</p><h2>Check linked X accounts</h2>
      <p>Each account is looked up on X by its stable ID. Suspended or deleted accounts get flagged, renamed accounts get their handle updated. About 10 twitterapi.io credits per account.</p>
      <label className="check-row"><input type="checkbox" checked={skipProtected} onChange={(e) => setSkipProtected(e.target.checked)} disabled={verifying} /> Skip protected members ({verifyPlan.protectedLinked} linked)</label>
      <p className="import-summary">Will check <strong>{verifyPlan.linked - (skipProtected ? verifyPlan.protectedLinked : 0)}</strong> accounts · about {((verifyPlan.linked - (skipProtected ? verifyPlan.protectedLinked : 0)) * 10).toLocaleString()} credits (${(((verifyPlan.linked - (skipProtected ? verifyPlan.protectedLinked : 0)) * 10) / 100000).toFixed(2)}).</p>
      <div className="modal-actions"><button type="button" className="button ghost" onClick={() => setVerifyPlan(undefined)} disabled={verifying}>Cancel</button><button className="button primary" onClick={runVerify} disabled={verifying || verifyPlan.linked - (skipProtected ? verifyPlan.protectedLinked : 0) === 0}>{verifying ? 'Checking X…' : 'Start check'}</button></div>
    </div></div>}

    {verifyResult && <div className="modal-backdrop" onMouseDown={() => setVerifyResult(undefined)}><div className="modal modal-wide" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal-icon"><BadgeCheck /></div><p className="eyebrow">X verification</p><h2>Linked X accounts checked</h2>
      <p className="import-summary">{verifyResult.checked} accounts checked{verifyResult.include_protected ? '' : ' (protected members skipped)'} · {verifyResult.unavailable.length} suspended or gone · {verifyResult.renamed.length} renamed and updated automatically.</p>
      {verifyResult.unavailable.length > 0 && <><h3 className="sub-heading">Could not verify</h3><div className="table-wrap import-results"><table><tbody>{verifyResult.unavailable.map((row) => <tr key={row.discord_user_id}><td><span className="member-cell"><strong>{row.discord_username}</strong><small className="mono">{row.discord_user_id}</small></span></td><td><a href={`https://x.com/${row.twitter_handle}`} target="_blank">@{row.twitter_handle}</a></td><td><span className="status failed"><i />{row.status}</span></td><td className="muted">{row.reason}</td></tr>)}</tbody></table></div></>}
      {verifyResult.renamed.length > 0 && <><h3 className="sub-heading">Renamed on X</h3><div className="table-wrap import-results"><table><tbody>{verifyResult.renamed.map((row) => <tr key={row.discord_user_id}><td><span className="member-cell"><strong>{row.discord_username}</strong><small className="mono">{row.discord_user_id}</small></span></td><td className="muted">@{row.old_handle} → <a href={`https://x.com/${row.new_handle}`} target="_blank">@{row.new_handle}</a></td></tr>)}</tbody></table></div></>}
      {!verifyResult.unavailable.length && !verifyResult.renamed.length && <p className="import-summary">Every linked account is alive and still uses the handle on file.</p>}
      <div className="modal-actions"><button className="button primary" onClick={() => setVerifyResult(undefined)}>Done</button></div>
    </div></div>}

    {syncResult && <div className="modal-backdrop" onMouseDown={() => setSyncResult(undefined)}><div className="modal modal-wide" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal-icon"><RefreshCw /></div><p className="eyebrow">Discord sync</p><h2>Server members compared with the registry</h2>
      <p className="import-summary">{syncResult.discord_members} humans in the server · {syncResult.added.length} newly registered · {syncResult.already_registered_active} already present + {syncResult.already_registered_inactive} inactive · {syncResult.bots_skipped} bots skipped · {syncResult.left_server.length} registered members no longer in the server.</p>
      <p className="import-summary">Registry now holds <strong>{syncResult.registry_active} active + {syncResult.registry_inactive} inactive</strong> members.</p>
      {syncResult.protected_roles_configured.length === 0 && <p className="estimate-warning">No protected role names are configured. Set <code>protected_role_names</code> in Scoring rules (for example: Active Supporter, Builder, Friend, Collaborator, Team) and sync again to protect members by their Discord roles.</p>}
      {syncResult.protected_by_role.length > 0 && <><h3 className="sub-heading">Newly protected because of their Discord roles</h3><div className="table-wrap import-results"><table><tbody>{syncResult.protected_by_role.map((row) => <tr key={row.discord_user_id}><td><span className="member-cell"><strong>{row.discord_username}</strong><small className="mono">{row.discord_user_id}</small></span></td><td className="muted">{row.roles}</td></tr>)}</tbody></table></div></>}
      {syncResult.renamed.length > 0 && <><h3 className="sub-heading">Discord handles updated</h3><div className="table-wrap import-results"><table><tbody>{syncResult.renamed.map((row) => <tr key={row.discord_user_id}><td className="muted">{row.old} →</td><td><span className="member-cell"><strong>{row.discord_username}</strong><small className="mono">{row.discord_user_id}</small></span></td></tr>)}</tbody></table></div></>}
      {syncResult.added.length > 0 && <><h3 className="sub-heading">Newly registered (no X yet)</h3><div className="table-wrap import-results"><table><tbody>{syncResult.added.map((row) => <tr key={row.discord_user_id}><td><span className="member-cell"><strong>{row.discord_username}</strong><small className="mono">{row.discord_user_id}</small></span></td></tr>)}</tbody></table></div></>}
      {syncResult.left_server.length > 0 && <><h3 className="sub-heading">In the registry but not in the server</h3><div className="table-wrap import-results"><table><tbody>{syncResult.left_server.map((row) => <tr key={row.discord_user_id}><td><span className="member-cell"><strong>{row.discord_username}</strong><small className="mono">{row.discord_user_id}</small></span></td></tr>)}</tbody></table></div></>}
      <div className="modal-actions"><button className="button primary" onClick={() => setSyncResult(undefined)}>Done</button></div>
    </div></div>}
  </div>
}
