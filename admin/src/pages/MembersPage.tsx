import { useMemo, useState, type ChangeEvent, type FormEvent } from 'react'
import { BadgeCheck, FileUp, Plus, RefreshCw, Search, Shield, ShieldCheck, ShieldOff, UserRoundCheck, UserRoundX } from 'lucide-react'
import useSWR from 'swr'
import { api, formatDate, formatScore, mutateApi } from '../api'
import { Empty, PageHeader, Pagination, Toast } from '../components'
import { parseCsv, rowsFromSheet, type ImportRow } from '../csv'
import type { DiscordSyncResponse, ImportResponse, ImportStatus, LinkedUser, Paginated, Session, VerifyResponse } from '../types'

const STATUS_LABEL: Record<ImportStatus, string> = { linked: 'Linked', relinked: 'Handle updated', unchanged: 'Already linked', registered: 'Registered, no X', skipped: 'Skipped', conflict: 'Conflict', failed: 'Failed' }
const STATUS_TONE: Record<ImportStatus, string> = { linked: 'complete', relinked: 'complete', unchanged: 'active', registered: 'running', skipped: '', conflict: 'failed', failed: 'failed' }
const ATTENTION: ImportStatus[] = ['failed', 'conflict']
const SEGMENTS = [
  { id: 'everyone', label: 'Everyone', query: {} },
  { id: 'linked', label: 'Linked to X', query: { linked: 'true' } },
  { id: 'unlinked', label: 'No X yet', query: { linked: 'false' } },
  { id: 'protected', label: 'Protected', query: { protected: 'true' } },
  { id: 'xissues', label: 'X suspended', query: { x_ok: 'false' } },
] as const

export default function MembersPage({ session }: { session: Session }) {
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('active')
  const [segment, setSegment] = useState<(typeof SEGMENTS)[number]['id']>('everyone')
  const [page, setPage] = useState(1)
  const [showLink, setShowLink] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const [importRows, setImportRows] = useState<ImportRow[]>([])
  const [importName, setImportName] = useState('')
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState<ImportResponse>()
  const [syncing, setSyncing] = useState(false)
  const [syncResult, setSyncResult] = useState<DiscordSyncResponse>()
  const [verifying, setVerifying] = useState(false)
  const [verifyResult, setVerifyResult] = useState<VerifyResponse>()
  const [verifyPlan, setVerifyPlan] = useState<{ linked: number; protectedLinked: number }>()
  const [skipProtected, setSkipProtected] = useState(false)
  const [notice, setNotice] = useState<{ text: string; kind: 'success' | 'error' }>()
  const query = useMemo(() => new URLSearchParams({
    search, page: String(page), page_size: '25',
    ...(filter !== 'all' ? { active: String(filter === 'active') } : {}),
    ...SEGMENTS.find((item) => item.id === segment)?.query,
  }).toString(), [search, filter, segment, page])
  const { data, mutate, isLoading } = useSWR<Paginated<LinkedUser>>(`/api/users?${query}`, api)

  const patch = async (user: LinkedUser, body: Record<string, unknown>, success: string) => {
    try {
      await mutateApi(`/api/users/${user.discord_user_id}`, session.csrf_token, 'PATCH', body)
      setNotice({ text: success, kind: 'success' })
      await mutate()
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Update failed', kind: 'error' }) }
  }
  const toggleActive = (user: LinkedUser) => patch(user, { active: !user.active }, `${user.discord_username} ${user.active ? 'deactivated' : 'reactivated'}.`)
  const toggleProtected = (user: LinkedUser) => patch(user, { special_role: !user.special_role }, `${user.discord_username} is ${user.special_role ? 'no longer protected' : 'now protected from the low-activity report'}.`)

  const link = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const values = Object.fromEntries(new FormData(event.currentTarget))
    try {
      await mutateApi('/api/users/link', session.csrf_token, 'POST', values)
      setNotice({ text: 'Member linked and X profile verified.', kind: 'success' })
      setShowLink(false)
      await mutate()
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Link failed', kind: 'error' }) }
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
      setNotice({ text: `Discord sync: ${response.added.length} new members registered, ${response.already_registered} already present, ${response.bots_skipped} bots skipped.`, kind: 'success' })
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

  return <div className="page">
    <PageHeader eyebrow="Community registry" title="Linked members" copy="Everyone in the community, with or without an X account. Protected members never appear in the low-activity report." actions={<>
      <button className="button" onClick={openVerify} disabled={verifying} title="Check linked X accounts for suspensions, deletions, and renames (about 10 credits each)"><BadgeCheck size={17} className={verifying ? 'spin' : ''} /> {verifying ? 'Checking X…' : 'Verify X accounts'}</button>
      <button className="button" onClick={runSync} disabled={syncing} title="Register every human member of the Discord server who is missing here"><RefreshCw size={17} className={syncing ? 'spin' : ''} /> {syncing ? 'Syncing…' : 'Sync from Discord'}</button>
      <button className="button" onClick={() => setShowImport(true)}><FileUp size={17} /> Import CSV</button>
      <button className="button primary" onClick={() => setShowLink(true)}><Plus size={17} /> Link member</button>
    </>} />
    {notice && <Toast message={notice.text} kind={notice.kind} />}
    <section className="panel">
      <div className="toolbar">
        <label className="search"><Search size={17} /><input value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} placeholder="Search Discord, X, ID, or role" /></label>
        <div className="segmented">{SEGMENTS.map((item) => <button className={segment === item.id ? 'active' : ''} onClick={() => { setSegment(item.id); setPage(1) }} key={item.id}>{item.label}</button>)}</div>
        <div className="segmented">{['active', 'inactive', 'all'].map((value) => <button className={filter === value ? 'active' : ''} onClick={() => { setFilter(value); setPage(1) }} key={value}>{value}</button>)}</div>
      </div>
      <div className="table-wrap"><table><thead><tr><th>Member</th><th>X identity</th><th>Special role</th><th>Score</th><th>Last signal</th><th>Status</th><th aria-label="Actions" /></tr></thead><tbody>
        {data?.items.map((user) => <tr key={user.discord_user_id}>
          <td><span className="member-cell"><strong>{user.discord_username}</strong><small className="mono">{user.discord_user_id}</small></span></td>
          <td>{user.twitter_user_id ? <span className="protected-cell"><a href={`https://x.com/${user.twitter_handle}`} target="_blank">@{user.twitter_handle}</a>{(user.x_status === 'suspended' || user.x_status === 'unavailable') && <span className="status failed"><i />X {user.x_status}</span>}</span> : <span className="muted">Not linked</span>}</td>
          <td>{user.special_role ? <span className="protected-cell"><span className="status complete"><i />Protected</span>{user.special_role_names && <small>{user.special_role_names}</small>}</span> : <span className="muted">—</span>}</td>
          <td className="score">{formatScore(user.score)}</td>
          <td>{formatDate(user.last_active_at)}</td>
          <td><span className={`status ${user.active ? 'complete' : 'failed'}`}><i />{user.active ? 'Active' : 'Inactive'}</span></td>
          <td className="row-actions">
            <button className="icon-button" title={user.special_role ? 'Remove protection' : 'Protect from low-activity report'} onClick={() => toggleProtected(user)}>{user.special_role ? <ShieldOff size={18} /> : <Shield size={18} />}</button>
            <button className="icon-button" title={user.active ? 'Deactivate' : 'Reactivate'} onClick={() => toggleActive(user)}>{user.active ? <UserRoundX size={18} /> : <UserRoundCheck size={18} />}</button>
          </td>
        </tr>)}
      </tbody></table></div>
      {!isLoading && !data?.items.length && <Empty title="No matching members" copy="Change the filters, sync from Discord, or import the community sheet." />}
      <Pagination page={page} size={25} total={data?.total ?? 0} onChange={setPage} />
    </section>

    {showLink && <div className="modal-backdrop" onMouseDown={() => setShowLink(false)}><form className="modal" onSubmit={link} onMouseDown={(e) => e.stopPropagation()}><div className="modal-icon"><ShieldCheck /></div><p className="eyebrow">Verified identity</p><h2>Link a member</h2><p>The X handle is resolved through twitterapi.io and its stable account ID is stored.</p><label>Discord user ID<input required name="discord_user_id" pattern="\d+" placeholder="123456789012345678" /></label><label>Discord display name<input required name="discord_username" placeholder="Major" /></label><label>X handle<input required name="twitter_handle" placeholder="@handle" /></label><div className="modal-actions"><button type="button" className="button ghost" onClick={() => setShowLink(false)}>Cancel</button><button className="button primary">Verify & link</button></div></form></div>}

    {showImport && <div className="modal-backdrop" onMouseDown={closeImport}><div className="modal modal-wide" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal-icon"><FileUp /></div><p className="eyebrow">Bulk registry</p><h2>Import members from a spreadsheet</h2>
      {!importResult && <>
        <p>Export the sheet as CSV. The first row must contain a <code>discord_id</code> column. Optional columns: <code>discord_username</code>, <code>x_handle</code>, <code>SPECIAL ROLE</code> (YES/NO) and <code>SPECIAL ROLE NAMES</code>. Everyone in the sheet is registered; handles are verified through twitterapi.io (about 18 credits each) and members already linked to the same handle are left untouched. Re-importing is safe.</p>
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
      <p className="import-summary">Will check <strong>{verifyPlan.linked - (skipProtected ? verifyPlan.protectedLinked : 0)}</strong> accounts · about {(verifyPlan.linked - (skipProtected ? verifyPlan.protectedLinked : 0)) * 10} credits.</p>
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
      <p className="import-summary">{syncResult.discord_members} humans in the server · {syncResult.added.length} newly registered · {syncResult.already_registered} already present · {syncResult.bots_skipped} bots skipped · {syncResult.left_server.length} registered members no longer in the server.</p>
      {syncResult.added.length > 0 && <><h3 className="sub-heading">Newly registered (no X yet)</h3><div className="table-wrap import-results"><table><tbody>{syncResult.added.map((row) => <tr key={row.discord_user_id}><td><span className="member-cell"><strong>{row.discord_username}</strong><small className="mono">{row.discord_user_id}</small></span></td></tr>)}</tbody></table></div></>}
      {syncResult.left_server.length > 0 && <><h3 className="sub-heading">In the registry but not in the server</h3><div className="table-wrap import-results"><table><tbody>{syncResult.left_server.map((row) => <tr key={row.discord_user_id}><td><span className="member-cell"><strong>{row.discord_username}</strong><small className="mono">{row.discord_user_id}</small></span></td></tr>)}</tbody></table></div></>}
      <div className="modal-actions"><button className="button primary" onClick={() => setSyncResult(undefined)}>Done</button></div>
    </div></div>}
  </div>
}
