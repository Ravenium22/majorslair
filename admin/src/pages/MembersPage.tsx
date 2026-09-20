import { useMemo, useState, type ChangeEvent, type FormEvent } from 'react'
import { FileUp, Plus, Search, ShieldCheck, UserRoundCheck, UserRoundX } from 'lucide-react'
import useSWR from 'swr'
import { api, formatDate, formatScore, mutateApi } from '../api'
import { Empty, PageHeader, Pagination, Toast } from '../components'
import { parseCsv, rowsFromSheet, type ImportRow } from '../csv'
import type { ImportResponse, ImportStatus, LinkedUser, Paginated, Session } from '../types'

const STATUS_LABEL: Record<ImportStatus, string> = { linked: 'Linked', relinked: 'Handle updated', unchanged: 'Already linked', skipped: 'Skipped', conflict: 'Conflict', failed: 'Failed' }
const STATUS_TONE: Record<ImportStatus, string> = { linked: 'complete', relinked: 'complete', unchanged: 'active', skipped: '', conflict: 'failed', failed: 'failed' }

export default function MembersPage({ session }: { session: Session }) {
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('active')
  const [page, setPage] = useState(1)
  const [showLink, setShowLink] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const [importRows, setImportRows] = useState<ImportRow[]>([])
  const [importName, setImportName] = useState('')
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState<ImportResponse>()
  const [notice, setNotice] = useState<{ text: string; kind: 'success' | 'error' }>()
  const query = useMemo(() => new URLSearchParams({ search, page: String(page), page_size: '25', ...(filter !== 'all' ? { active: String(filter === 'active') } : {}) }).toString(), [search, filter, page])
  const { data, mutate, isLoading } = useSWR<Paginated<LinkedUser>>(`/api/users?${query}`, api)

  const toggle = async (user: LinkedUser) => {
    try {
      await mutateApi(`/api/users/${user.discord_user_id}`, session.csrf_token, 'PATCH', { active: !user.active })
      setNotice({ text: `${user.discord_username} ${user.active ? 'deactivated' : 'reactivated'}.`, kind: 'success' })
      await mutate()
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Update failed', kind: 'error' }) }
  }

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
      setNotice({ text: `Import finished: ${linked} linked, ${response.summary.unchanged ?? 0} already linked, ${(response.summary.failed ?? 0) + (response.summary.conflict ?? 0)} need attention.`, kind: 'success' })
      await mutate()
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Import failed', kind: 'error' }) }
    finally { setImporting(false) }
  }

  const withHandles = importRows.filter((row) => row.twitter_handle).length

  return <div className="page">
    <PageHeader eyebrow="Community registry" title="Linked members" copy="Control who participates in scoring and verify every Discord-to-X identity link." actions={<><button className="button" onClick={() => setShowImport(true)}><FileUp size={17} /> Import CSV</button><button className="button primary" onClick={() => setShowLink(true)}><Plus size={17} /> Link member</button></>} />
    {notice && <Toast message={notice.text} kind={notice.kind} />}
    <section className="panel">
      <div className="toolbar"><label className="search"><Search size={17} /><input value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} placeholder="Search Discord, X, or ID" /></label><div className="segmented">{['active', 'inactive', 'all'].map((value) => <button className={filter === value ? 'active' : ''} onClick={() => { setFilter(value); setPage(1) }} key={value}>{value}</button>)}</div></div>
      <div className="table-wrap"><table><thead><tr><th>Member</th><th>X identity</th><th>Score</th><th>Last signal</th><th>Status</th><th aria-label="Actions" /></tr></thead><tbody>
        {data?.items.map((user) => <tr key={user.discord_user_id}><td><span className="member-cell"><strong>{user.discord_username}</strong><small className="mono">{user.discord_user_id}</small></span></td><td><a href={`https://x.com/${user.twitter_handle}`} target="_blank">@{user.twitter_handle}</a></td><td className="score">{formatScore(user.score)}</td><td>{formatDate(user.last_active_at)}</td><td><span className={`status ${user.active ? 'complete' : 'failed'}`}><i />{user.active ? 'Active' : 'Inactive'}</span></td><td><button className="icon-button" title={user.active ? 'Deactivate' : 'Reactivate'} onClick={() => toggle(user)}>{user.active ? <UserRoundX size={18} /> : <UserRoundCheck size={18} />}</button></td></tr>)}
      </tbody></table></div>
      {!isLoading && !data?.items.length && <Empty title="No matching members" copy="Change the filters or link the first Discord member." />}
      <Pagination page={page} size={25} total={data?.total ?? 0} onChange={setPage} />
    </section>
    {showLink && <div className="modal-backdrop" onMouseDown={() => setShowLink(false)}><form className="modal" onSubmit={link} onMouseDown={(e) => e.stopPropagation()}><div className="modal-icon"><ShieldCheck /></div><p className="eyebrow">Verified identity</p><h2>Link a member</h2><p>The X handle is resolved through twitterapi.io and its stable account ID is stored.</p><label>Discord user ID<input required name="discord_user_id" pattern="\d+" placeholder="123456789012345678" /></label><label>Discord display name<input required name="discord_username" placeholder="Major" /></label><label>X handle<input required name="twitter_handle" placeholder="@handle" /></label><div className="modal-actions"><button type="button" className="button ghost" onClick={() => setShowLink(false)}>Cancel</button><button className="button primary">Verify & link</button></div></form></div>}
    {showImport && <div className="modal-backdrop" onMouseDown={closeImport}><div className="modal modal-wide" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal-icon"><FileUp /></div><p className="eyebrow">Bulk registry</p><h2>Import members from a spreadsheet</h2>
      {!importResult && <>
        <p>Export the sheet as CSV. The first row must contain <code>discord_id</code> and <code>x_handle</code> columns; <code>discord_username</code> is optional. Every handle is verified through twitterapi.io (about 18 credits each). Members already linked to the same handle are left untouched.</p>
        <label>CSV file<input type="file" accept=".csv,text/csv" onChange={pickFile} disabled={importing} /></label>
        {importRows.length > 0 && <p className="import-summary"><strong>{importName}</strong>: {importRows.length} members found, {withHandles} with an X handle{importRows.length - withHandles > 0 ? `, ${importRows.length - withHandles} without one (they will be skipped)` : ''}.</p>}
        <div className="modal-actions"><button type="button" className="button ghost" onClick={closeImport} disabled={importing}>Cancel</button><button className="button primary" onClick={runImport} disabled={!importRows.length || importing}>{importing ? 'Verifying handles…' : `Verify & import ${withHandles || ''}`}</button></div>
      </>}
      {importResult && <>
        <p className="import-summary">{(['linked', 'relinked', 'unchanged', 'skipped', 'conflict', 'failed'] as ImportStatus[]).filter((key) => importResult.summary[key]).map((key) => `${importResult.summary[key]} ${STATUS_LABEL[key].toLowerCase()}`).join(' · ')}</p>
        <div className="table-wrap import-results"><table><thead><tr><th>Member</th><th>X handle</th><th>Result</th><th>Note</th></tr></thead><tbody>
          {[...importResult.results].sort((a, b) => Number(['failed', 'conflict'].includes(b.status)) - Number(['failed', 'conflict'].includes(a.status))).map((row) => <tr key={row.discord_user_id}><td><span className="member-cell"><strong>{row.discord_username}</strong><small className="mono">{row.discord_user_id}</small></span></td><td>{row.twitter_handle ? <a href={`https://x.com/${row.twitter_handle}`} target="_blank">@{row.twitter_handle}</a> : <span className="muted">none</span>}</td><td><span className={`status ${STATUS_TONE[row.status]}`}><i />{STATUS_LABEL[row.status]}</span></td><td className="muted">{row.message}</td></tr>)}
        </tbody></table></div>
        <div className="modal-actions"><button className="button primary" onClick={closeImport}>Done</button></div>
      </>}
    </div></div>}
  </div>
}
