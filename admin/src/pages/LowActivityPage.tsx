import { useEffect, useState } from 'react'
import { Download, ExternalLink, FileSearch, UserRoundMinus } from 'lucide-react'
import useSWR from 'swr'
import { api, formatDate, formatScore, mutateApi } from '../api'
import { Empty, HelpLink, Loading, PageHeader, RoleExclusionPicker, Toast, useConfirm, useEscape } from '../components'
import type { DiscordRole, LinkedUser, LowActivityReport, RoleBulkResult, Session } from '../types'

const csvCell = (value: unknown) => { const text = String(value ?? ''); return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text }
const daysAgo = (iso: string) => Math.floor((Date.now() - new Date(iso).getTime()) / 86400000)

/** The follow state as one short phrase. "Not checked" is kept apart from "does not follow":
 *  a check that could not read a whole follower list proves nothing either way. */
function followState(member: LinkedUser): { label: string; tone: 'complete' | 'failed' | ''; detail: string } {
  const primary = member.follows_primary ?? ''
  const secondary = member.follows_secondary ?? ''
  if (primary === 'yes' && secondary === 'yes') return { label: 'Both', tone: 'complete', detail: 'Follows both tracked accounts' }
  if (primary === 'no' && secondary === 'no') return { label: 'Neither', tone: 'failed', detail: 'Follows neither tracked account' }
  if (primary === 'no' || secondary === 'no') return { label: 'Missing one', tone: 'failed', detail: primary === 'no' ? 'Does not follow the primary account' : 'Does not follow the secondary account' }
  return { label: 'Not checked', tone: '', detail: 'Run Check follows on the Members page' }
}

function download(report: LowActivityReport, ticked: (member: LinkedUser) => boolean) {
  const header = ['discord_username', 'discord_id', 'x_handle', 'points', 'last_signal', 'joined_discord', 'days_in_server', 'x_status', 'follows_primary', 'follows_secondary', 'ticked_for_purge']
  const lines = report.items.map((m) => [
    m.discord_username, m.discord_user_id, m.twitter_handle, m.score,
    m.last_active_at || 'never', m.discord_joined_at || 'unknown',
    m.discord_joined_at ? daysAgo(m.discord_joined_at) : '',
    m.twitter_user_id ? (m.x_status || 'ok') : 'not linked',
    m.follows_primary || 'not checked', m.follows_secondary || 'not checked',
    ticked(m) ? 'yes' : 'no',
  ].map(csvCell).join(','))
  const blob = new Blob(['﻿' + [header.join(','), ...lines].join('\r\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `majors-lair-low-activity-${new Date().toISOString().slice(0, 10)}.csv`
  anchor.click()
  URL.revokeObjectURL(url)
}

/** Every row opens that member's drawer on the Members page: this is the one screen whose
 *  output gets defended to a real person, so the proof has to be one click away. */
function MemberTable({ rows, isTicked, onToggle, onToggleAll }: {
  rows: LinkedUser[]
  isTicked: (member: LinkedUser) => boolean
  onToggle: (member: LinkedUser) => void
  onToggleAll: (tick: boolean) => void
}) {
  const allTicked = rows.length > 0 && rows.every(isTicked)
  return <div className="table-wrap"><table>
    <thead><tr>
      <th className="pick-cell"><label className="pick-box"><input type="checkbox" checked={allTicked} onChange={(e) => onToggleAll(e.target.checked)} aria-label="Tick everyone in this group for the purge" /></label></th>
      <th>Discord</th><th>X identity</th><th>Points</th><th>Follows</th><th>Last signal</th><th>In the server</th><th />
    </tr></thead>
    <tbody>{rows.map((m) => {
      const follow = followState(m)
      return <tr key={m.discord_user_id} className={isTicked(m) ? 'picked' : ''}>
        <td className="pick-cell"><label className="pick-box"><input type="checkbox" checked={isTicked(m)} onChange={() => onToggle(m)} aria-label={`Tick ${m.discord_username} for the purge`} /></label></td>
        <td><a className="member-cell linked-cell" href={`#members?search=${m.discord_user_id}&open=${m.discord_user_id}`} title="Open their points, history and scan tools"><strong>{m.discord_username}</strong><small className="mono">{m.discord_user_id}</small></a></td>
        <td>{m.twitter_user_id
          ? <span className="protected-cell"><a href={`https://x.com/${m.twitter_handle}`} target="_blank" rel="noreferrer">@{m.twitter_handle}</a>{(m.x_status === 'suspended' || m.x_status === 'unavailable') && <span className="status failed"><i />X {m.x_status}</span>}</span>
          : <span className="muted">No X account linked</span>}</td>
        <td className="score">{formatScore(m.score)}</td>
        <td>{m.twitter_user_id ? <span className={`status ${follow.tone}`} title={follow.detail}>{follow.tone && <i />}{follow.label}</span> : <span className="muted">—</span>}</td>
        <td>{formatDate(m.last_active_at)}</td>
        <td>{m.discord_joined_at ? <span className="protected-cell">{formatDate(m.discord_joined_at)}<small>{daysAgo(m.discord_joined_at)} days</small></span> : <span className="muted" title="Run Sync from Discord to fill join dates">unknown</span>}</td>
        <td className="row-actions"><a className="icon-button" href={`#members?search=${m.discord_user_id}&open=${m.discord_user_id}`} title="See why they scored this"><FileSearch size={15} /></a>{m.twitter_user_id && <a className="icon-button" href={`https://x.com/${m.twitter_handle}`} target="_blank" rel="noreferrer" title="Open their X profile"><ExternalLink size={15} /></a>}</td>
      </tr>
    })}</tbody>
  </table></div>
}

export default function LowActivityPage({ session }: { session: Session }) {
  const [threshold, setThreshold] = useState('')
  const query = threshold === '' ? '' : `?threshold=${threshold}`
  const { data, isLoading, mutate } = useSWR<LowActivityReport>(`/api/low-activity${query}`, api)
  const confirm = useConfirm()
  const [notice, setNotice] = useState<{ text: string; kind: 'success' | 'error' }>()

  // Ticks are stored as exceptions, so a background refresh of the report never throws away
  // the people you spared. Quiet members start ticked; members who never linked an X account
  // start unticked, because the report itself says their 0 is not evidence of anything.
  const [spared, setSpared] = useState<Set<string>>(new Set())
  const [addedUnlinked, setAddedUnlinked] = useState<Set<string>>(new Set())
  const isTicked = (m: LinkedUser) => (m.twitter_user_id ? !spared.has(m.discord_user_id) : addedUnlinked.has(m.discord_user_id))
  const toggle = (m: LinkedUser) => {
    const flip = (set: Set<string>) => { const next = new Set(set); if (next.has(m.discord_user_id)) next.delete(m.discord_user_id); else next.add(m.discord_user_id); return next }
    if (m.twitter_user_id) setSpared(flip); else setAddedUnlinked(flip)
  }
  const quiet = data?.items.filter((m) => m.twitter_user_id) ?? []
  const unlinked = data?.items.filter((m) => !m.twitter_user_id) ?? []
  const tickedMembers = (data?.items ?? []).filter(isTicked)

  // The purge dialog. The bot has no permission to kick, and should not have one: this only
  // gives a role, and the removing happens in Discord by a person.
  const [purgeOpen, setPurgeOpen] = useState(false)
  const [roles, setRoles] = useState<{ roles: DiscordRole[]; bot_can_manage_roles: boolean }>()
  const [roleId, setRoleId] = useState('')
  const [excludeRoleIds, setExcludeRoleIds] = useState<string[]>([])
  const [preview, setPreview] = useState<RoleBulkResult>()
  const [result, setResult] = useState<RoleBulkResult>()
  const [busy, setBusy] = useState(false)
  const tickedIds = tickedMembers.map((m) => m.discord_user_id)
  const tickedKey = tickedIds.join(',')
  useEffect(() => { setPreview(undefined) }, [tickedKey, roleId, excludeRoleIds])
  useEscape(purgeOpen && !busy, () => setPurgeOpen(false))

  const openPurge = async () => {
    setPurgeOpen(true)
    setPreview(undefined)
    setResult(undefined)
    try {
      const loaded = roles ?? await api<{ roles: DiscordRole[]; bot_can_manage_roles: boolean }>('/api/discord/roles')
      setRoles(loaded)
      if (!roleId) setRoleId(loaded.roles.find((r) => r.assignable && /purge/i.test(r.name))?.id ?? '')
      setExcludeRoleIds(loaded.roles.filter((r) => r.booster).map((r) => r.id))
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Could not load the server roles', kind: 'error' }) }
  }

  const request = (dryRun: boolean) => mutateApi<RoleBulkResult>('/api/users/roles', session.csrf_token, 'POST', {
    role_id: roleId || '000000', action: 'add', discord_user_ids: tickedIds, dry_run: dryRun, exclude_role_ids: excludeRoleIds,
  })

  const runPreview = async () => {
    if (!tickedIds.length) return
    setBusy(true)
    try { setPreview(await request(true)) }
    catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Preview failed', kind: 'error' }) }
    finally { setBusy(false) }
  }

  const roleName = roles?.roles.find((r) => r.id === roleId)?.name ?? 'the role'
  const apply = async () => {
    if (!preview || !roleId) return
    if (!(await confirm({
      title: `Give ${roleName} to ${preview.matched} member${preview.matched === 1 ? '' : 's'}?`,
      body: <>Their roles change in Discord straight away. <strong>Nobody is removed from the server</strong>: the bot cannot kick, so this only marks them, and removing them is done in Discord. Anyone holding a role you chose to leave alone is checked again now and skipped.</>,
      confirmLabel: `Give ${roleName}`,
      tone: 'danger',
    }))) return
    setBusy(true)
    try {
      const outcome = await request(false)
      setResult(outcome)
      setNotice({ text: `Gave ${roleName} to ${outcome.changed?.length ?? 0} of ${outcome.matched} members${outcome.failed?.length ? `, ${outcome.failed.length} failed` : ''}.`, kind: outcome.failed?.length ? 'error' : 'success' })
      await mutate()
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Role update failed', kind: 'error' }) }
    finally { setBusy(false) }
  }

  return <div className="page">
    <PageHeader
      title="Low-activity report"
      copy="Who to consider removing this cycle. Protected members and recent joiners are left out automatically. Open anyone to read the evidence before you decide. The bot never removes anyone: the purge below only gives a Discord role."
      actions={<>
        <button className="button" onClick={() => data && download(data, isTicked)} disabled={!data?.items.length}><Download size={17} /> Download CSV</button>
        <button className="button primary" onClick={openPurge} disabled={!tickedMembers.length} title={tickedMembers.length ? undefined : 'Tick at least one member first'}><UserRoundMinus size={17} /> Purge: give a role to {tickedMembers.length}</button>
      </>}
    />
    {notice && <Toast message={notice.text} kind={notice.kind} />}

    {data && <section className="panel rules-banner">
      <p>
        Showing active members with <strong>{formatScore(data.threshold)} points or fewer</strong>.
        {' '}Left out: <strong>{data.excluded_protected}</strong> protected {data.excluded_protected === 1 ? 'member' : 'members'}
        {data.newcomer_grace_days > 0 && <> and <strong>{data.excluded_newcomers}</strong> who joined in the last {data.newcomer_grace_days} days</>}.
        {' '}<HelpLink topic="low-activity" />
      </p>
      <label className="threshold-box">Points at or below<input type="number" min={0} step={1} inputMode="numeric" value={threshold} onChange={(e) => setThreshold(e.target.value.replace(/[^0-9.]/g, ''))} placeholder={String(data.threshold)} /><span className="muted small">{threshold === '' ? 'from Scoring rules' : 'this view only'}</span></label>
    </section>}

    <section className="panel report-groups">
      {isLoading && !data && <Loading label="Building the report…" />}
      {data && quiet.length > 0 && <>
        <h2 className="sub-heading">Linked, but quiet <span className="group-count">{quiet.length}</span></h2>
        <p className="group-note">These members linked an X account, so the bot could see their engagement. It scored {formatScore(data.threshold)} or less. All of them start ticked for the purge; untick anyone you want to spare.</p>
        <MemberTable rows={quiet} isTicked={isTicked} onToggle={toggle} onToggleAll={(tick) => setSpared((current) => { const next = new Set(current); quiet.forEach((m) => { if (tick) next.delete(m.discord_user_id); else next.add(m.discord_user_id) }); return next })} />
      </>}
      {data && unlinked.length > 0 && <>
        <h2 className="sub-heading">Never linked an X account <span className="group-count">{unlinked.length}</span></h2>
        <p className="group-note">The bot has never been able to score these members, so their points say nothing about whether they were active. They start unticked; tick them only if not linking is itself your reason.</p>
        <MemberTable rows={unlinked} isTicked={isTicked} onToggle={toggle} onToggleAll={(tick) => setAddedUnlinked((current) => { const next = new Set(current); unlinked.forEach((m) => { if (tick) next.add(m.discord_user_id); else next.delete(m.discord_user_id) }); return next })} />
      </>}
      {data && data.items.length === 0 && <Empty title="Nobody is below the threshold" copy="Every active member outside the protected and newcomer groups is above it. Lower the number above to widen the list." />}
      {data && data.items.length > 0 && <p className="muted small page-note">{data.items.length} {data.items.length === 1 ? 'member' : 'members'} in total · {tickedMembers.length} ticked for the purge · checked against the latest scan.</p>}
    </section>

    {purgeOpen && <div className="modal-backdrop" onMouseDown={() => { if (!busy) setPurgeOpen(false) }}><div className="modal modal-wide" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal-icon danger-icon"><UserRoundMinus /></div><h2>Purge: give the ticked members a role</h2>
      <p className="role-audience">This gives <strong>{roleId ? roleName : 'a role'}</strong> to <strong>the {tickedMembers.length} member{tickedMembers.length === 1 ? '' : 's'} ticked on this report</strong>. The bot cannot kick anyone and will not try: removing people is done by you, in Discord. <HelpLink topic="purge" /></p>
      {roles && !roles.bot_can_manage_roles && <p className="estimate-warning">The bot has no <strong>Manage Roles</strong> permission in Discord. Server Settings → Roles → the bot's role → enable Manage Roles, and drag the bot's role above the role you want it to give.</p>}
      <label>Role to give<select value={roleId} onChange={(e) => setRoleId(e.target.value)} disabled={busy}><option value="">Pick a role…</option>{roles?.roles.map((r) => <option key={r.id} value={r.id} disabled={!r.assignable}>{r.name}{r.assignable ? '' : r.managed ? ' (managed by an integration)' : ' (above the bot, cannot assign)'}</option>)}</select></label>
      <RoleExclusionPicker roles={roles?.roles ?? []} value={excludeRoleIds} onChange={setExcludeRoleIds} disabled={busy} />
      {preview?.unverified?.length ? <p className="estimate-warning"><strong>{preview.unverified.length}</strong> left out because Discord would not say which roles they hold ({preview.unverified.slice(0, 6).map((u) => u.discord_username).join(', ')}{preview.unverified.length > 6 ? '…' : ''}). Try again in a minute.</p> : null}
      {preview && !result && <p className="import-summary"><strong>{preview.matched}</strong> will get the role{preview.skipped?.length ? <> · <strong>{preview.skipped.length}</strong> left alone because of their roles ({preview.skipped.slice(0, 6).map((s) => `${s.discord_username} (${s.roles})`).join(', ')}{preview.skipped.length > 6 ? '…' : ''})</> : null}.</p>}
      {result && <p className="import-summary">Gave {roleName} to <strong>{result.changed?.length ?? 0}</strong> of {result.matched}{result.failed?.length ? ` · ${result.failed.length} failed: ${result.failed.slice(0, 4).map((f) => `${f.discord_username} (${f.error})`).join(', ')}` : ''}{result.skipped?.length ? ` · ${result.skipped.length} left alone because of their roles` : ''}.</p>}
      <div className="modal-actions">
        <button type="button" className="button ghost" onClick={() => setPurgeOpen(false)} disabled={busy}>{result ? 'Done' : 'Cancel'}</button>
        {!result && <button type="button" className="button" onClick={runPreview} disabled={busy || !roleId}>{busy && !preview ? 'Checking…' : 'Preview who gets it'}</button>}
        {!result && <button type="button" className="button danger" onClick={apply} disabled={busy || !roleId || !preview} title={preview ? undefined : 'Run the preview first so you can see exactly who this hits'}>{busy && preview ? 'Working…' : preview ? `Give ${roleName} to ${preview.matched}` : `Give ${roleId ? roleName : 'the role'}`}</button>}
      </div>
    </div></div>}
  </div>
}
