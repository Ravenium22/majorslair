import { useEffect, useMemo, useState, type ChangeEvent, type FormEvent, type ReactNode } from 'react'
import { BadgeCheck, Download, FileUp, Pencil, Plus, RefreshCw, Search, Shield, ShieldCheck, Tags, UserRoundCheck, UserRoundSearch, UserRoundX } from 'lucide-react'
import useSWR from 'swr'
import { api, formatCount, formatDate, formatDay, formatScore, formatUsd, mutateApi } from '../api'
import { Empty, HelpLink, Loading, PageHeader, Pagination, RoleExclusionPicker, SortTh, Toast, ToolsMenu, useConfirm, useEscape } from '../components'
import { parseCsv, rowsFromSheet, type ImportRow } from '../csv'
import type { DiscordRole, DiscordSyncResponse, FollowCheckResult, FollowEstimate, ImportResponse, ImportStatus, LinkedUser, Paginated, RoleBulkResult, Session, VerifyResponse } from '../types'

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
  { id: 'low', label: 'At or below threshold' },
] as const
const FOLLOWS = [
  { id: 'any', label: 'Any follow state', query: {} },
  { id: 'both', label: 'Follows both', query: { follows: 'both' } },
  { id: 'missing', label: 'Not following', query: { follows: 'missing' } },
  { id: 'unchecked', label: 'Follow not checked', query: { follows: 'unchecked' } },
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

const hashParams = () => new URLSearchParams(window.location.hash.split('?')[1] ?? '')

type FilterState = { filter: string; segment: string; protection: string; points: string; joined: string; follows: string }
const DEFAULT_FILTERS: FilterState = { filter: 'active', segment: 'everyone', protection: 'any', points: 'any', joined: 'any', follows: 'any' }
// Six rows of toggles were one screen's worth of decisions before the table even started.
// These named lists cover the jobs that actually come up; the toggles stay behind More filters.
const VIEWS: { id: string; label: string; hint: string; set: Partial<FilterState> }[] = [
  { id: 'all', label: 'Everyone', hint: 'Everyone still in the server', set: {} },
  { id: 'not-following', label: 'Not following', hint: 'Proven by the last follow check not to follow at least one account', set: { follows: 'missing' } },
  { id: 'no-x', label: 'No X linked', hint: 'Cannot score until they link an X account', set: { segment: 'unlinked' } },
  { id: 'zero', label: '0 points', hint: 'Nothing scored this cycle, protected members and newcomers included', set: { points: 'zero' } },
  { id: 'x-issues', label: 'X suspended', hint: 'Their linked X account is suspended or gone', set: { segment: 'xissues' } },
  { id: 'protected', label: 'Protected', hint: 'Never on the low-activity report', set: { protection: 'protected' } },
  { id: 'inactive', label: 'Left or deactivated', hint: 'Out of the leaderboard and scans, history kept', set: { filter: 'inactive' } },
]

export default function MembersPage({ session }: { session: Session }) {
  const initial = useMemo(hashParams, [])
  const [search, setSearch] = useState(() => initial.get('search') ?? '')
  const [filter, setFilter] = useState(() => initial.get('active') ?? 'active')
  const [segment, setSegment] = useState<(typeof SEGMENTS)[number]['id']>(() => (SEGMENTS.find((s) => s.id === initial.get('view'))?.id ?? 'everyone'))
  const [protection, setProtection] = useState<(typeof PROTECTION)[number]['id']>(() => (PROTECTION.find((s) => s.id === initial.get('role'))?.id ?? 'any'))
  const [points, setPoints] = useState<(typeof POINTS)[number]['id']>(() => (POINTS.find((s) => s.id === initial.get('points'))?.id ?? 'any'))
  const [lowThreshold, setLowThreshold] = useState(() => initial.get('threshold') ?? '')
  const [joined, setJoined] = useState<(typeof JOINED)[number]['id']>(() => (JOINED.find((s) => s.id === initial.get('joined'))?.id ?? 'any'))
  const [follows, setFollows] = useState<(typeof FOLLOWS)[number]['id']>(() => (FOLLOWS.find((s) => s.id === initial.get('follows'))?.id ?? 'any'))
  const [sort, setSort] = useState<(typeof SORTS)[number]['id']>(() => (SORTS.find((s) => s.id === initial.get('sort'))?.id ?? 'score_desc'))
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
  const [followPlan, setFollowPlan] = useState<FollowEstimate>()
  const [followResult, setFollowResult] = useState<FollowCheckResult>()
  const [followBusy, setFollowBusy] = useState(false)
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
  const [excludeRoleIds, setExcludeRoleIds] = useState<string[]>([])
  // Ticked rows. Naming three people should not mean building a filter that matches
  // exactly those three, which was the only way to do it before.
  const [picked, setPicked] = useState<string[]>([])
  // A bulk role change is the most consequential thing on this page, so it starts from a
  // known audience (everyone in the server) rather than silently inheriting whatever the
  // table happens to be filtered to. Ticking the box opts into the page filters.
  const [inheritFilters, setInheritFilters] = useState(false)
  const filterQuery = useMemo(() => new URLSearchParams({
    search,
    ...(filter !== 'all' ? { active: String(filter === 'active') } : {}),
    ...SEGMENTS.find((item) => item.id === segment)?.query,
    ...PROTECTION.find((item) => item.id === protection)?.query,
    ...(points !== 'any' ? { points } : {}),
    ...(points === 'low' && lowThreshold !== '' ? { threshold: lowThreshold } : {}),
    ...(joined !== 'any' ? { joined } : {}),
    ...FOLLOWS.find((item) => item.id === follows)?.query,
    ...(sort !== 'score_desc' ? { sort } : {}),
  }).toString(), [search, filter, segment, protection, points, lowThreshold, joined, follows, sort])
  const query = `${filterQuery}&page=${page}&page_size=25`
  // Navigating to Members (the sidebar link, a step on Overview, a pasted link) re-reads the
  // filters from the address bar. Clicking "Members" therefore always lands on a clean list
  // instead of quietly keeping the last filter someone built to find people to remove.
  useEffect(() => {
    const onHash = () => {
      if (window.location.hash.split('?')[0] !== '#members') return
      const next = hashParams()
      // Forward an old drawer link before touching any state: a state change here would
      // re-write the address back to the Members list after the forward.
      if (next.get('open')) { window.location.replace(`#member?id=${next.get('open')}`); return }
      setSearch(next.get('search') ?? '')
      setFilter(next.get('active') ?? 'active')
      setSegment(SEGMENTS.find((item) => item.id === next.get('view'))?.id ?? 'everyone')
      setProtection(PROTECTION.find((item) => item.id === next.get('role'))?.id ?? 'any')
      setPoints(POINTS.find((item) => item.id === next.get('points'))?.id ?? 'any')
      setLowThreshold(next.get('threshold') ?? '')
      setJoined(JOINED.find((item) => item.id === next.get('joined'))?.id ?? 'any')
      setFollows(FOLLOWS.find((item) => item.id === next.get('follows'))?.id ?? 'any')
      setSort(SORTS.find((item) => item.id === next.get('sort'))?.id ?? 'score_desc')
      setPage(1)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  // Mirror the filters into the address bar so a reload, a bookmark or a pasted link
  // reopens the same view.
  useEffect(() => {
    const params = new URLSearchParams()
    if (search) params.set('search', search)
    if (filter !== 'active') params.set('active', filter)
    if (segment !== 'everyone') params.set('view', segment)
    if (protection !== 'any') params.set('role', protection)
    if (points !== 'any') params.set('points', points)
    if (points === 'low' && lowThreshold) params.set('threshold', lowThreshold)
    if (joined !== 'any') params.set('joined', joined)
    if (follows !== 'any') params.set('follows', follows)
    if (sort !== 'score_desc') params.set('sort', sort)
    const next = params.toString()
    // Only ever rewrite a Members address; never pull someone back from a page they left for.
    if (window.location.hash.split('?')[0] !== '#members') return
    const target = `#members${next ? `?${next}` : ''}`
    if (window.location.hash !== target) window.history.replaceState(null, '', target)
  }, [search, filter, segment, protection, points, lowThreshold, joined, follows, sort])
  const { data, mutate, isLoading } = useSWR<Paginated<LinkedUser>>(`/api/users?${query}`, api)
  // Links from before the member page existed (#members?open=<id>) still land on the member.
  useEffect(() => {
    const legacy = initial.get('open')
    if (legacy) window.location.replace(`#member?id=${legacy}`)
  }, [initial])
  useEffect(() => {
    const note = sessionStorage.getItem('member-deleted')
    if (!note) return
    sessionStorage.removeItem('member-deleted')
    setNotice({ text: note, kind: 'success' })
  }, [])
  const confirm = useConfirm()
  const resetPage = <T,>(setter: (value: T) => void) => (value: T) => { setter(value); setPage(1) }
  const currentFilters: FilterState = { filter, segment, protection, points, joined, follows }
  const activeView = VIEWS.find((view) => (Object.keys(DEFAULT_FILTERS) as (keyof FilterState)[]).every((key) => currentFilters[key] === (view.set[key] ?? DEFAULT_FILTERS[key])))
  const [showFilters, setShowFilters] = useState(false)
  const filtersOpen = showFilters || !activeView
  const extraFilters = (Object.keys(DEFAULT_FILTERS) as (keyof FilterState)[]).filter((key) => currentFilters[key] !== DEFAULT_FILTERS[key]).length
  const applyView = (view: (typeof VIEWS)[number]) => {
    const next = { ...DEFAULT_FILTERS, ...view.set }
    setFilter(next.filter)
    setSegment(next.segment as typeof segment)
    setProtection(next.protection as typeof protection)
    setPoints(next.points as typeof points)
    setJoined(next.joined as typeof joined)
    setFollows(next.follows as typeof follows)
    setLowThreshold('')
    setPage(1)
  }
  const hasFilters = Boolean(search) || filter !== 'active' || segment !== 'everyone' || protection !== 'any' || points !== 'any' || joined !== 'any' || follows !== 'any'
  const clearFilters = () => { setSearch(''); setFilter('active'); setSegment('everyone'); setProtection('any'); setPoints('any'); setLowThreshold(''); setJoined('any'); setFollows('any'); setSort('score_desc'); setPage(1) }

  const patch = async (user: LinkedUser, body: Record<string, unknown>, success: string) => {
    try {
      await mutateApi(`/api/users/${user.discord_user_id}`, session.csrf_token, 'PATCH', body)
      setNotice({ text: success, kind: 'success' })
      await mutate()
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Update failed', kind: 'error' }) }
  }
  const toggleActive = async (user: LinkedUser) => {
    if (!(await confirm(user.active ? {
      title: `Deactivate ${user.discord_username}?`,
      body: 'They drop out of the leaderboard, the low-activity report and every scan. Their history and points are kept, and you can reactivate them at any time.',
      confirmLabel: 'Deactivate',
      tone: 'danger',
    } : {
      title: `Reactivate ${user.discord_username}?`,
      body: 'They go back on the leaderboard with the points and history they had, and the next scans include them again.',
      confirmLabel: 'Reactivate',
    }))) return
    return patch(user, { active: !user.active }, `${user.discord_username} ${user.active ? 'deactivated' : 'reactivated'}.`)
  }
  const toggleProtected = async (user: LinkedUser) => {
    const protecting = !user.special_role
    if (!(await confirm(protecting ? {
      title: `Protect ${user.discord_username}?`,
      body: 'They will never appear on the low-activity report, and scans leave them out whenever "skip protected members" is ticked. Nothing changes in Discord.',
      confirmLabel: 'Protect',
    } : {
      title: `Remove protection from ${user.discord_username}?`,
      body: <>They can appear on the low-activity report again.{user.role_protected_names ? <> They still hold <strong>{user.role_protected_names}</strong> in Discord, so the next sync will protect them again unless that role is removed there.</> : null}</>,
      confirmLabel: 'Remove protection',
      tone: 'danger',
    }))) return
    return patch(user, { special_role: protecting }, `${user.discord_username} is ${protecting ? 'now protected from the low-activity report' : 'no longer protected'}.`)
  }

  const [linking, setLinking] = useState(false)
  const link = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const values = Object.fromEntries(new FormData(event.currentTarget))
    const handle = String(values.twitter_handle ?? '').replace(/^@/, '')
    if (!(await confirm({
      title: `Link ${String(values.discord_username || values.discord_user_id || 'this member')} to @${handle}?`,
      body: 'The X account is checked on X before anything is saved. An X account can belong to only one member, so this fails rather than taking it from someone else.',
      confirmLabel: 'Link member',
    }))) return
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
    if (!(await confirm({
      title: `Import ${importRows.length} row${importRows.length === 1 ? '' : 's'}?`,
      body: <>Members missing from the registry are added, and {withHandles} X handle{withHandles === 1 ? '' : 's'} will be checked on X and linked{withSpecial ? <>. {withSpecial} will be marked protected by hand</> : null}. Existing members keep their points. Rows that conflict are listed afterwards instead of being forced.</>,
      confirmLabel: 'Import',
    }))) return
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
    if (!(await confirm({
      title: 'Sync with the Discord server?',
      body: <><p>This reads the server's member list and brings the registry in line with it:</p><ul className="confirm-list"><li>registers members who are missing, skipping bots and ignored IDs</li><li>updates handles and join dates</li><li>protects or unprotects people by the Discord roles they hold right now</li><li>marks people who left the server as inactive, keeping their points</li></ul><p>It never changes anyone's Discord account and uses no X credits.</p></>,
      confirmLabel: 'Sync now',
    }))) return
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

  const openMember = (user: LinkedUser) => { window.location.hash = `#member?id=${user.discord_user_id}` }

  const usingPicked = picked.length > 0
  const roleFilters = () => ({
    search: inheritFilters ? search : '',
    active: !inheritFilters ? true : filter === 'all' ? null : filter === 'active',
    ...(inheritFilters && segment === 'linked' ? { linked: true } : inheritFilters && segment === 'unlinked' ? { linked: false } : {}),
    ...(inheritFilters && segment === 'xissues' ? { x_ok: false } : {}),
    ...(inheritFilters && protection !== 'any' ? { protected: protection === 'protected' } : {}),
    points: inheritFilters ? points : 'any',
    threshold: inheritFilters && points === 'low' && lowThreshold !== '' ? Number(lowThreshold) : null,
    joined: inheritFilters ? joined : 'any',
    follows: inheritFilters ? follows : 'any',
    min_score: roleMin === '' ? null : Number(roleMin),
    max_score: roleMax === '' ? null : Number(roleMax),
  })

  // The same audience written as a sentence, so it is read rather than reconstructed.
  const pageClauses = [
    search ? `matching "${search}"` : null,
    filter === 'inactive' ? 'who have left the server' : filter === 'all' ? 'whether or not they are still in the server' : null,
    segment === 'linked' ? 'with an X account linked' : segment === 'unlinked' ? 'with no X account yet' : segment === 'xissues' ? 'whose X account is suspended or renamed' : null,
    protection === 'protected' ? 'holding a protected role' : protection === 'regular' ? 'without a protected role' : null,
    points === 'positive' ? 'who have points' : points === 'zero' ? 'on 0 points' : points === 'low' ? `at or below ${lowThreshold || 'the threshold'} points` : null,
    joined === 'new' ? 'who joined recently' : joined === 'established' ? 'who joined a while ago' : null,
    follows === 'both' ? 'who follow both accounts' : follows === 'missing' ? 'who do not follow both accounts' : follows === 'unchecked' ? 'whose follows have never been checked' : null,
  ].filter(Boolean) as string[]
  const rangeClause = roleMin !== '' && roleMax !== '' ? `between ${roleMin} and ${roleMax} points`
    : roleMin !== '' ? `on ${roleMin} points or more`
    : roleMax !== '' ? `on ${roleMax} points or fewer` : null
  const audienceClauses = [...(inheritFilters ? pageClauses : []), ...(rangeClause ? [rangeClause] : [])]
  const audience = usingPicked
    ? `the ${picked.length} member${picked.length === 1 ? '' : 's'} you ticked`
    : audienceClauses.length
      ? `every member ${audienceClauses.join(', ')}`
      : 'every member in the server'

  // Any change to who is targeted invalidates a preview taken before it.
  useEffect(() => { setRolePreview(undefined) }, [inheritFilters, roleMin, roleMax, roleAction, picked, search, filter, segment, protection, points, lowThreshold, joined])

  const openRoles = async () => {
    setShowRoles(true)
    setRoleResult(undefined)
    setRolePreview(undefined)
    setInheritFilters(false)
    setRoleMin('')
    setRoleMax('')
    try {
      const loaded = await api<{ roles: DiscordRole[]; bot_can_manage_roles: boolean }>('/api/discord/roles')
      setRoles(loaded)
      // Nitro boosters are protected from role changes by default; the admin can untick.
      setExcludeRoleIds(loaded.roles.filter((r) => r.booster).map((r) => r.id))
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Could not load roles', kind: 'error' }) }
  }


  const previewRoles = async () => {
    setRoleBusy(true)
    try { setRolePreview(await mutateApi<RoleBulkResult>('/api/users/roles', session.csrf_token, 'POST', { role_id: roleId || '000000', action: roleAction, filters: roleFilters(), discord_user_ids: usingPicked ? picked : [], dry_run: true, exclude_role_ids: excludeRoleIds })) }
    catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Preview failed', kind: 'error' }) }
    finally { setRoleBusy(false) }
  }

  const applyRoles = async () => {
    if (!roleId) { setNotice({ text: 'Pick a role first', kind: 'error' }); return }
    const roleName = roles?.roles.find((r) => r.id === roleId)?.name ?? 'this role'
    const count = rolePreview?.matched ?? 0
    if (!(await confirm({
      title: `${roleAction === 'add' ? 'Give' : 'Remove'} ${roleName} ${roleAction === 'add' ? 'to' : 'from'} ${count} member${count === 1 ? '' : 's'}?`,
      body: 'This changes their roles in Discord straight away. Anyone holding a role you chose to leave alone is checked again at this moment and skipped. Undoing it means running the opposite action.',
      confirmLabel: `${roleAction === 'add' ? 'Give' : 'Remove'} the role`,
      tone: 'danger',
    }))) return
    setRoleBusy(true)
    try {
      const result = await mutateApi<RoleBulkResult>('/api/users/roles', session.csrf_token, 'POST', { role_id: roleId, action: roleAction, filters: roleFilters(), discord_user_ids: usingPicked ? picked : [], dry_run: false, exclude_role_ids: excludeRoleIds })
      setRoleResult(result)
      setPicked([])
      setNotice({ text: `${roleAction === 'add' ? 'Gave' : 'Removed'} the role for ${result.changed?.length ?? 0} of ${result.matched} members${result.failed?.length ? `, ${result.failed.length} failed` : ''}.`, kind: result.failed?.length ? 'error' : 'success' })
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Role update failed', kind: 'error' }) }
    finally { setRoleBusy(false) }
  }




  const openFollowCheck = async () => {
    setFollowBusy(true)
    try { setFollowPlan(await api<FollowEstimate>('/api/users/follow-estimate')) }
    catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Could not read the follower counts', kind: 'error' }) }
    finally { setFollowBusy(false) }
  }

  const runFollowCheck = async () => {
    setFollowBusy(true)
    try {
      const result = await mutateApi<FollowCheckResult>('/api/users/check-follows', session.csrf_token, 'POST')
      setFollowPlan(undefined)
      setFollowResult(result)
      await mutate()
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Follow check failed', kind: 'error' }) }
    finally { setFollowBusy(false) }
  }


  const anyModal = Boolean(followPlan || followResult || showLink || showImport || showRoles || syncResult || verifyResult || verifyPlan)
  const anyBusy = importing || roleBusy || verifying || syncing || followBusy
  useEscape(anyModal && !anyBusy, () => {
    setShowLink(false); setShowImport(false); setShowRoles(false); setSyncResult(undefined); setVerifyResult(undefined); setVerifyPlan(undefined); setFollowPlan(undefined); setFollowResult(undefined)
  })

  return <div className="page">
    <PageHeader title="Linked members" copy="Everyone in the community, with or without an X account. Protected members never appear in the low-activity report." actions={<button className="button primary" onClick={() => setShowLink(true)}><Plus size={17} /> Link member</button>} toolbar={<>
      <button className="button" onClick={runSync} disabled={syncing} title="Bring the registry in line with the Discord server"><RefreshCw size={17} className={syncing ? 'spin' : ''} /> {syncing ? 'Syncing…' : 'Sync from Discord'}</button>
      <button className="button" onClick={openRoles}><Tags size={17} /> Give role</button>
      <ToolsMenu items={[
        { label: verifying ? 'Checking X accounts…' : 'Verify X accounts', hint: 'Find suspended, deleted or renamed accounts', icon: <BadgeCheck size={16} />, onSelect: openVerify, disabled: verifying },
        { label: followBusy ? 'Checking follows…' : 'Check follows', hint: 'Who follows both tracked accounts', icon: <UserRoundSearch size={16} />, onSelect: openFollowCheck, disabled: followBusy },
        { label: 'Import CSV', hint: 'Add or link members from a sheet', icon: <FileUp size={16} />, onSelect: () => setShowImport(true) },
        { label: 'Export CSV', hint: 'Download the list as filtered below', icon: <Download size={16} />, href: `/api/users/export?${filterQuery}` },
      ]} />
    </>} />
    {notice && <Toast message={notice.text} kind={notice.kind} />}
    <section className="panel">
      {picked.length > 0 && <div className="picked-bar"><strong>{picked.length} member{picked.length === 1 ? '' : 's'} selected</strong><button className="button" onClick={openRoles}><Tags size={15} /> Give or remove a role</button><button className="link-button" onClick={() => setPicked([])}>Clear selection</button></div>}
      <div className="toolbar filters">
        <label className="search"><Search size={17} /><input value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} placeholder="Search Discord handle, X handle, ID, or role" /></label>
        <select value={sort} onChange={(e) => resetPage(setSort)(e.target.value as typeof sort)} aria-label="Sort">{SORTS.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select>
      </div>
      <div className="toolbar views" role="group" aria-label="Lists">
        {VIEWS.map((view) => <button key={view.id} type="button" className={`view-pill ${activeView?.id === view.id ? 'active' : ''}`} aria-pressed={activeView?.id === view.id} title={view.hint} onClick={() => applyView(view)}>{view.label}</button>)}
        {!activeView && <span className="view-pill custom active" aria-live="polite">Custom filter</span>}
        {activeView && <button type="button" className={`link-button more-filters ${filtersOpen ? 'open' : ''}`} aria-expanded={filtersOpen} aria-controls="member-filters" onClick={() => setShowFilters(!filtersOpen)}>{filtersOpen ? 'Fewer filters' : 'More filters'}{extraFilters && !filtersOpen ? ` (${extraFilters})` : ''}</button>}
        {!filtersOpen && <span className="filter-count">{data ? `${formatCount(data.total)} member${data.total === 1 ? '' : 's'}` : ''}</span>}
      </div>
      {filtersOpen && <div className="toolbar filters wrap" id="member-filters">
        <div className="segmented">{SEGMENTS.map((item) => <button className={segment === item.id ? 'active' : ''} aria-pressed={segment === item.id} onClick={() => resetPage(setSegment)(item.id)} key={item.id}>{item.label}</button>)}</div>
        <div className="segmented">{PROTECTION.map((item) => <button className={protection === item.id ? 'active' : ''} aria-pressed={protection === item.id} onClick={() => resetPage(setProtection)(item.id)} key={item.id}>{item.label}</button>)}</div>
        <div className="segmented">{POINTS.map((item) => <button className={points === item.id ? 'active' : ''} aria-pressed={points === item.id} onClick={() => resetPage(setPoints)(item.id)} key={item.id} title={item.id === 'low' ? 'Everyone at or below the threshold, protected members and newcomers included. The Low-activity report leaves those out.' : undefined}>{item.label}</button>)}</div>
        {points === 'low' && <label className="threshold-box">≤<input type="number" min={0} step={1} inputMode="numeric" placeholder="pts" value={lowThreshold} onChange={(e) => { const v = e.target.value.replace(/[^0-9]/g, ''); setLowThreshold(v); setPage(1) }} title="Points at or below this count as low activity for this view. Empty = the threshold from Scoring rules." /><span className="muted small">pts{lowThreshold === '' ? ' (from Scoring rules)' : ''}</span></label>}
        {points === 'low' && <span className="filter-note">Includes protected members and recent joiners. <a href="#low-activity">Low-activity report</a> leaves them out.</span>}
        <div className="segmented">{FOLLOWS.map((item) => <button className={follows === item.id ? 'active' : ''} aria-pressed={follows === item.id} onClick={() => resetPage(setFollows)(item.id)} key={item.id} title={item.id === 'missing' ? 'Proven not to follow at least one account by the last check. Members who were never checked are not in here.' : undefined}>{item.label}</button>)}</div>
        <div className="segmented">{JOINED.map((item) => <button className={joined === item.id ? 'active' : ''} aria-pressed={joined === item.id} onClick={() => resetPage(setJoined)(item.id)} key={item.id} title={item.id === 'new' ? 'Joined Discord within the grace period (newcomer_grace_days in Scoring rules); never in the low-activity report' : item.id === 'established' ? 'Joined before the grace period, or join date unknown' : undefined}>{item.label}</button>)}</div>
        <div className="segmented">{['active', 'inactive', 'all'].map((value) => <button className={filter === value ? 'active' : ''} aria-pressed={filter === value} onClick={() => resetPage(setFilter)(value)} key={value}>{value === 'active' ? 'Active' : value === 'inactive' ? 'Inactive' : 'All'}</button>)}</div>
        <span className="filter-count">{data ? `${formatCount(data.total)} member${data.total === 1 ? '' : 's'}` : ''}{hasFilters && <button className="link-button" onClick={clearFilters}>Clear filters</button>}</span>
      </div>}
      <div className="table-wrap"><table className="members-table"><thead><tr>
        <th className="pick-cell"><label className="pick-box"><input type="checkbox" aria-label="Select every member on this page" checked={Boolean(data?.items.length) && (data?.items ?? []).every((u) => picked.includes(u.discord_user_id))} onChange={(e) => { const ids = (data?.items ?? []).map((u) => u.discord_user_id); setPicked(e.target.checked ? [...new Set([...picked, ...ids])] : picked.filter((id) => !ids.includes(id))) }} /></label></th>
        <SortTh label="Discord" direction={sort === 'name' ? 'asc' : undefined} onToggle={() => resetPage(setSort)('name')} />
        <th>X identity</th>
        <th>Special role</th>
        <SortTh label="Points" className="score" direction={sort === 'score_desc' ? 'desc' : sort === 'score_asc' ? 'asc' : undefined} onToggle={() => resetPage(setSort)(sort === 'score_desc' ? 'score_asc' : 'score_desc')} />
        <SortTh label="Last signal" direction={sort === 'last_signal' ? 'desc' : undefined} onToggle={() => resetPage(setSort)('last_signal')} />
        <SortTh label="Joined Discord" direction={sort === 'joined' ? 'desc' : undefined} onToggle={() => resetPage(setSort)('joined')} />
        <th className="row-actions-head" aria-label="Actions" />
      </tr></thead><tbody>
        {/* The row stays clickable for the mouse, but the keyboard and screen readers get a
            single real link in the name cell. A row that was itself a button, wrapping a
            link and three icon buttons, was invalid nesting and roughly 125 tab stops. */}
        {data?.items.map((user) => <tr key={user.discord_user_id} className={`member-row ${picked.includes(user.discord_user_id) ? 'picked' : ''}`} onClick={() => openMember(user)}>
          <td className="pick-cell" onClick={(e) => e.stopPropagation()}><label className="pick-box"><input type="checkbox" checked={picked.includes(user.discord_user_id)} aria-label={`Select ${user.discord_username}`} onChange={(e) => setPicked(e.target.checked ? [...picked, user.discord_user_id] : picked.filter((id) => id !== user.discord_user_id))} /></label></td>
          <td><a className="member-cell linked-cell" href={`#member?id=${user.discord_user_id}`} onClick={(e) => e.stopPropagation()}><strong>{user.discord_username}{!user.active && <span className="status failed inline-status"><i />Inactive</span>}</strong><small className="mono">{user.discord_user_id}</small></a></td>
          <td>{user.twitter_user_id ? <span className="protected-cell"><a href={`https://x.com/${user.twitter_handle}`} target="_blank" rel="noreferrer">@{user.twitter_handle}</a>{(user.x_status === 'suspended' || user.x_status === 'unavailable') && <span className="status failed"><i />X {user.x_status}</span>}{user.follows_primary === 'yes' && user.follows_secondary === 'yes' ? <small className="follow-ok">follows both</small> : user.follows_primary === 'no' || user.follows_secondary === 'no' ? <small className="follow-missing" title={user.follows_primary === 'no' && user.follows_secondary === 'no' ? 'Follows neither tracked account' : user.follows_primary === 'no' ? 'Does not follow the primary account' : 'Does not follow the secondary account'}>{user.follows_primary === 'no' && user.follows_secondary === 'no' ? 'follows neither' : 'missing a follow'}</small> : null}</span> : <span className="muted">Not linked</span>}</td>
          <td>{user.special_role ? <span className="protected-cell"><span className="status complete"><i />Protected</span><small title={user.role_protected_names ? 'Granted by a Discord role. Removing the role in Discord removes this at the next sync.' : 'Set here by hand. Sync never changes it.'}>{user.role_protected_names ? `role: ${user.role_protected_names}` : `by hand${user.special_role_names ? `: ${user.special_role_names}` : ''}`}</small></span> : <span className="muted">—</span>}</td>
          <td className="score">{formatScore(user.score)}</td>
          <td title={user.last_active_at ? formatDate(user.last_active_at) : undefined}>{formatDay(user.last_active_at)}</td>
          <td>{user.discord_joined_at ? <span className="protected-cell">{formatDay(user.discord_joined_at)}<small>{daysAgo(user.discord_joined_at)} days ago</small></span> : <span className="muted" title="Run Sync from Discord to fill join dates">—</span>}</td>
          <td className="row-actions" onClick={(e) => e.stopPropagation()}>
            <button className="icon-button" title="Edit this member" aria-label={`Edit ${user.discord_username}`} onClick={() => { window.location.hash = `#member?id=${user.discord_user_id}&tool=edit` }}><Pencil size={17} /></button>
            {/* Shield and ShieldOff differ by one diagonal stroke, and one of these buttons
                takes a member out of the removal list. The state is named, not drawn. */}
            <button className={`icon-button labelled ${user.special_role ? 'on' : ''}`} title={user.special_role ? 'Protected from the low-activity report. Click to remove protection.' : 'Not protected. Click to protect from the low-activity report.'} aria-pressed={user.special_role} aria-label={`${user.special_role ? 'Remove protection from' : 'Protect'} ${user.discord_username}`} onClick={() => toggleProtected(user)}>{user.special_role ? <ShieldCheck size={17} /> : <Shield size={17} />}<span>{user.special_role ? 'Protected' : 'Protect'}</span></button>
            <button className="icon-button" title={user.active ? 'Deactivate this member' : 'Reactivate this member'} aria-label={`${user.active ? 'Deactivate' : 'Reactivate'} ${user.discord_username}`} onClick={() => toggleActive(user)}>{user.active ? <UserRoundX size={18} /> : <UserRoundCheck size={18} />}</button>
          </td>
        </tr>)}
      </tbody></table></div>
      {isLoading && !data && <Loading label="Loading members…" />}
      {!isLoading && !data?.items.length && <Empty title="No matching members" copy="Change the filters, sync from Discord, or import the community sheet." />}
      <Pagination page={page} size={25} total={data?.total ?? 0} onChange={setPage} />
    </section>

    {followPlan && <div className="modal-backdrop" onMouseDown={() => { if (!followBusy) setFollowPlan(undefined) }}><div className="modal" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal-icon"><UserRoundSearch /></div><h2>Check who follows the tracked accounts</h2><HelpLink topic="follows" />
      <p>This reads the follower list of each tracked account once and matches your {followPlan.linked_members} linked members against it. That is far cheaper than asking about each member, and it is the only way to be sure.</p>
      <dl className="estimate-grid" data-dialog-focus tabIndex={-1} aria-live="polite">
        {followPlan.accounts.map((account) => <div key={account.slot}><dt className="handle">@{account.handle}</dt><dd>{account.error ? '—' : formatCount(account.followers ?? 0)}<small>{account.error ? account.error : 'followers to read'}</small></dd></div>)}
        <div className="wide cost-cell"><dt>This check will cost about</dt><dd>{formatUsd(followPlan.credits)}<small>{formatCount(followPlan.credits)} credits · one per follower read</small></dd></div>
      </dl>
      <p className="field-hint">A member is only recorded as not following when the whole follower list was read. If a list is too long to finish, they stay marked unchecked rather than being blamed for it.</p>
      <div className="modal-actions"><button className="button ghost" onClick={() => setFollowPlan(undefined)} disabled={followBusy}>Cancel</button><button className="button primary" onClick={runFollowCheck} disabled={followBusy}>{followBusy ? 'Checking…' : 'Run the check'}</button></div>
    </div></div>}

    {followResult && <div className="modal-backdrop" onMouseDown={() => setFollowResult(undefined)}><div className="modal" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal-icon"><UserRoundSearch /></div><h2>Follow check finished</h2>
      <p className="import-summary">Checked <strong>{followResult.checked}</strong> linked members · <strong>{followResult.follows_both}</strong> follow both accounts · {followResult.follows_primary} follow @{followResult.primary.handle} · {followResult.follows_secondary} follow @{followResult.secondary.handle} · cost {formatUsd(followResult.credits)}.</p>
      {followResult.unknown > 0 && <p className="estimate-warning">{followResult.unknown} member{followResult.unknown === 1 ? ' was' : 's were'} left unchecked because a follower list could not be read to the end. They are not counted as non-followers.</p>}
      <p className="muted small">Use the <strong>Not following</strong> filter to see everyone proven to be missing at least one follow.</p>
      <div className="modal-actions"><button className="button ghost" onClick={() => setFollowResult(undefined)}>Close</button><button className="button primary" onClick={() => { setFollowResult(undefined); resetPage(setFollows)('missing') }}>Show who is not following</button></div>
    </div></div>}

    {showRoles && <div className="modal-backdrop" onMouseDown={() => { if (!roleBusy) setShowRoles(false) }}><div className="modal modal-wide" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal-icon"><Tags /></div><h2>Give or remove a role in bulk</h2><HelpLink topic="purge" />
      <p className="role-audience">This will affect <strong>{audience}</strong>{roleId ? <>, {roleAction === 'add' ? 'giving them' : 'taking away'} <strong>{roles?.roles.find((r) => r.id === roleId)?.name}</strong></> : null}. Preview first, then apply. Every run is logged in the Audit trail.</p>
      {roles && !roles.bot_can_manage_roles && <p className="estimate-warning">The bot has no <strong>Manage Roles</strong> permission in Discord. Server Settings → Roles → the bot's role → enable Manage Roles, and drag the bot's role above the roles you want it to give.</p>}
      <div className="role-grid">
        <label>Role<select value={roleId} onChange={(e) => setRoleId(e.target.value)} disabled={roleBusy}><option value="">Pick a role…</option>{roles?.roles.map((r) => <option key={r.id} value={r.id} disabled={!r.assignable}>{r.name}{r.assignable ? '' : r.managed ? ' (managed by an integration)' : ' (above the bot, cannot assign)'}</option>)}</select></label>
        <label>Action<select value={roleAction} onChange={(e) => setRoleAction(e.target.value as 'add' | 'remove')} disabled={roleBusy}><option value="add">Give the role</option><option value="remove">Remove the role</option></select></label>
        <label>Min points<input type="number" step="1" value={roleMin} onChange={(e) => setRoleMin(e.target.value)} placeholder="any" disabled={roleBusy} /></label>
        <label>Max points<input type="number" step="1" value={roleMax} onChange={(e) => setRoleMax(e.target.value)} placeholder="any" disabled={roleBusy} /></label>
      </div>
      <RoleExclusionPicker roles={roles?.roles ?? []} value={excludeRoleIds} onChange={setExcludeRoleIds} disabled={roleBusy} />
      {!usingPicked && pageClauses.length > 0 && <label className="role-inherit"><input type="checkbox" checked={inheritFilters} onChange={(e) => setInheritFilters(e.target.checked)} disabled={roleBusy} /><span>Narrow this to the filters set on the Members page<small>{pageClauses.join(', ')}</small></span></label>}
      {rolePreview?.unverified?.length ? <p className="estimate-warning"><strong>{rolePreview.unverified.length}</strong> member{rolePreview.unverified.length === 1 ? '' : 's'} left out because Discord would not say which roles they hold ({rolePreview.unverified.slice(0, 6).map((u) => u.discord_username).join(', ')}{rolePreview.unverified.length > 6 ? '…' : ''}). Rather than risk changing someone who is protected, they are not touched. Try again in a minute.</p> : null}
      {rolePreview && !roleResult && <><p className="import-summary"><strong>{rolePreview.matched}</strong> members match{rolePreview.skipped?.length ? <> · <strong>{rolePreview.skipped.length}</strong> left alone because of their roles ({rolePreview.skipped.slice(0, 6).map((s) => s.discord_username).join(', ')}{rolePreview.skipped.length > 6 ? '…' : ''})</> : null}. {rolePreview.matched > 500 ? 'Showing the first 500.' : ''}</p><div className="table-wrap import-results"><table><tbody>{rolePreview.members?.map((m) => <tr key={m.discord_user_id}><td><span className="member-cell"><strong>{m.discord_username}</strong><small className="mono">{m.discord_user_id}</small></span></td><td className="score">{formatScore(m.score)}</td><td>{m.special_role ? <span className="status complete"><i />Protected</span> : ''}</td></tr>)}</tbody></table></div></>}
      {roleResult && <><p className="import-summary">{roleAction === 'add' ? 'Gave' : 'Removed'} the role for <strong>{roleResult.changed?.length ?? 0}</strong> of {roleResult.matched} members{roleResult.failed?.length ? ` · ${roleResult.failed.length} failed` : ''}{roleResult.skipped?.length ? ` · ${roleResult.skipped.length} left alone because of their roles` : ''}.</p>{roleResult.skipped && roleResult.skipped.length > 0 && <div className="table-wrap import-results"><table><tbody>{roleResult.skipped.map((s) => <tr key={s.discord_user_id}><td><span className="member-cell"><strong>{s.discord_username}</strong><small className="mono">{s.discord_user_id}</small></span></td><td className="muted">{s.roles}</td></tr>)}</tbody></table></div>}{roleResult.failed && roleResult.failed.length > 0 && <div className="table-wrap import-results"><table><tbody>{roleResult.failed.map((f) => <tr key={f.discord_user_id}><td><span className="member-cell"><strong>{f.discord_username}</strong><small className="mono">{f.discord_user_id}</small></span></td><td className="muted">{f.error}</td></tr>)}</tbody></table></div>}</>}
      <div className="modal-actions"><button type="button" className="button ghost" onClick={() => setShowRoles(false)} disabled={roleBusy}>{roleResult ? 'Done' : 'Cancel'}</button>{!roleResult && <button type="button" className="button" onClick={previewRoles} disabled={roleBusy}>{roleBusy ? 'Working…' : 'Preview who matches'}</button>}{!roleResult && <button type="button" className="button primary" onClick={applyRoles} disabled={roleBusy || !roleId || !rolePreview} title={!rolePreview ? 'Run the preview first so you can see exactly who this hits' : undefined}>{roleBusy ? 'Working…' : !rolePreview ? (roleAction === 'add' ? 'Give the role' : 'Remove the role') : roleAction === 'add' ? `Give the role to ${rolePreview.matched} members` : `Remove the role from ${rolePreview.matched} members`}</button>}</div>
    </div></div>}

    {showLink && <div className="modal-backdrop" onMouseDown={() => setShowLink(false)}><form className="modal" onSubmit={link} onMouseDown={(e) => e.stopPropagation()}><div className="modal-icon"><ShieldCheck /></div><h2>Link a member</h2><p>Use the Discord username (the handle shown in the profile, not the nickname). The X handle is resolved through twitterapi.io and its stable account ID is stored.</p><label>Discord user ID<input required name="discord_user_id" pattern="\d+" inputMode="numeric" placeholder="e.g. 123456789012345678" disabled={linking} /><small className="field-hint">Discord → User Settings → Advanced → Developer Mode on, then right-click the member → Copy User ID.</small></label><label>Discord handle<input required name="discord_username" placeholder="e.g. luna.luna12" autoCapitalize="none" spellCheck={false} disabled={linking} /></label><label>X handle<input required name="twitter_handle" placeholder="e.g. @handle" autoCapitalize="none" spellCheck={false} disabled={linking} /></label><div className="modal-actions"><button type="button" className="button ghost" onClick={() => setShowLink(false)} disabled={linking}>Cancel</button><button className="button primary" disabled={linking}>{linking ? 'Verifying on X…' : 'Verify & link'}</button></div></form></div>}

    {showImport && <div className="modal-backdrop" onMouseDown={closeImport}><div className="modal modal-wide" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal-icon"><FileUp /></div><h2>Import members from a spreadsheet</h2>
      {!importResult && <>
        <p>Export the sheet as CSV. The first row must contain a <code>discord_id</code> column. Optional columns: <code>discord_username</code> (the Discord handle), <code>x_handle</code>, <code>SPECIAL ROLE</code> (YES/NO) and <code>SPECIAL ROLE NAMES</code>. Everyone in the sheet is registered; handles are verified through twitterapi.io (about 18 credits each) and members already linked to the same handle are left untouched. Re-importing is safe.</p>
        <label className="file-pick"><span className="file-pick-label">CSV file</span><input type="file" accept=".csv,text/csv" onChange={pickFile} disabled={importing} /><span className="button file-pick-button"><FileUp size={16} /> Choose a CSV file</span><span className="file-pick-name">{importName || 'No file chosen yet'}</span></label>
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
      <div className="modal-icon"><BadgeCheck /></div><h2>Check linked X accounts</h2>
      <p>Each account is looked up on X by its stable ID. Suspended or deleted accounts get flagged, renamed accounts get their handle updated. About 10 twitterapi.io credits per account.</p>
      <label className="check-row"><input type="checkbox" checked={skipProtected} onChange={(e) => setSkipProtected(e.target.checked)} disabled={verifying} /> Skip protected members ({verifyPlan.protectedLinked} linked)</label>
      <p className="import-summary">Will check <strong>{verifyPlan.linked - (skipProtected ? verifyPlan.protectedLinked : 0)}</strong> accounts · about {formatUsd((verifyPlan.linked - (skipProtected ? verifyPlan.protectedLinked : 0)) * 10)} ({formatCount((verifyPlan.linked - (skipProtected ? verifyPlan.protectedLinked : 0)) * 10)} credits).</p>
      <div className="modal-actions"><button type="button" className="button ghost" onClick={() => setVerifyPlan(undefined)} disabled={verifying}>Cancel</button><button className="button primary" onClick={runVerify} disabled={verifying || verifyPlan.linked - (skipProtected ? verifyPlan.protectedLinked : 0) === 0}>{verifying ? 'Checking X…' : 'Start check'}</button></div>
    </div></div>}

    {verifyResult && <div className="modal-backdrop" onMouseDown={() => setVerifyResult(undefined)}><div className="modal modal-wide" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal-icon"><BadgeCheck /></div><h2>Linked X accounts checked</h2>
      <p className="import-summary">{verifyResult.checked} accounts checked{verifyResult.include_protected ? '' : ' (protected members skipped)'} · {verifyResult.unavailable.length} suspended or gone · {verifyResult.renamed.length} renamed and updated automatically.</p>
      {verifyResult.unavailable.length > 0 && <><h3 className="sub-heading">Could not verify</h3><div className="table-wrap import-results"><table><tbody>{verifyResult.unavailable.map((row) => <tr key={row.discord_user_id}><td><span className="member-cell"><strong>{row.discord_username}</strong><small className="mono">{row.discord_user_id}</small></span></td><td><a href={`https://x.com/${row.twitter_handle}`} target="_blank">@{row.twitter_handle}</a></td><td><span className="status failed"><i />{row.status}</span></td><td className="muted">{row.reason}</td></tr>)}</tbody></table></div></>}
      {verifyResult.renamed.length > 0 && <><h3 className="sub-heading">Renamed on X</h3><div className="table-wrap import-results"><table><tbody>{verifyResult.renamed.map((row) => <tr key={row.discord_user_id}><td><span className="member-cell"><strong>{row.discord_username}</strong><small className="mono">{row.discord_user_id}</small></span></td><td className="muted">@{row.old_handle} → <a href={`https://x.com/${row.new_handle}`} target="_blank">@{row.new_handle}</a></td></tr>)}</tbody></table></div></>}
      {!verifyResult.unavailable.length && !verifyResult.renamed.length && <p className="import-summary">Every linked account is alive and still uses the handle on file.</p>}
      <div className="modal-actions"><button className="button primary" onClick={() => setVerifyResult(undefined)}>Done</button></div>
    </div></div>}

    {syncResult && <div className="modal-backdrop" onMouseDown={() => setSyncResult(undefined)}><div className="modal modal-wide" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal-icon"><RefreshCw /></div><h2>Server members compared with the registry</h2><HelpLink topic="sync" label="How sync works" />
      <p className="import-summary">{syncResult.discord_members} humans in the server · {syncResult.added.length} newly registered · {syncResult.already_registered_active} already present + {syncResult.already_registered_inactive} inactive · {syncResult.bots_skipped} bots skipped{syncResult.ignored ? ` · ${syncResult.ignored} ignored on purpose` : ''} · {syncResult.deactivated.length} deactivated after leaving the server.</p>
      <p className="import-summary">Registry now holds <strong>{syncResult.registry_active} active + {syncResult.registry_inactive} inactive</strong> members.</p>
      {syncResult.unmatched_role_names?.length > 0 && <p className="estimate-warning">No role in the server is named {syncResult.unmatched_role_names.map((row) => <code key={row.configured}>{row.configured}</code>).reduce<ReactNode[]>((acc, el, i) => i ? [...acc, ' or ', el] : [el], [])}, so nobody is protected by it. A typed name has to match the role exactly, emoji and spelling included.{syncResult.unmatched_role_names.some((row) => row.did_you_mean.length) && <> Did you mean {syncResult.unmatched_role_names.flatMap((row) => row.did_you_mean).map((name) => <code key={name}>{name}</code>).reduce<ReactNode[]>((acc, el, i) => i ? [...acc, ' or ', el] : [el], [])}?</>} Pick the role under <strong>Roles that protect whoever holds them</strong> on <a href="#scoring">Scoring rules</a> instead of typing it, then sync again.</p>}
      {syncResult.roles_unreadable && <p className="estimate-warning">Discord would not return the server's roles, so <strong>nobody's protection was changed</strong> this time. Run the sync again in a minute.</p>}
      {(syncResult.missing_role_ids?.length ?? 0) > 0 && <p className="estimate-warning">{syncResult.missing_role_ids?.length === 1 ? 'A role picked to protect members no longer exists' : `${syncResult.missing_role_ids?.length} roles picked to protect members no longer exist`} in the server. Remove {syncResult.missing_role_ids?.length === 1 ? 'it' : 'them'} on <a href="#scoring">Scoring rules</a>.</p>}
      {syncResult.protected_roles_configured.length === 0 && !syncResult.unmatched_role_names?.length && !syncResult.roles_unreadable && !syncResult.missing_role_ids?.length && <p className="estimate-warning">No roles protect anyone yet. Pick them under <strong>Roles that protect whoever holds them</strong> on <a href="#scoring">Scoring rules</a>, then sync again.</p>}
      {syncResult.protected_by_role.length > 0 && <><h3 className="sub-heading">Newly protected because of their Discord roles</h3><div className="table-wrap import-results"><table><tbody>{syncResult.protected_by_role.map((row) => <tr key={row.discord_user_id}><td><span className="member-cell"><strong>{row.discord_username}</strong><small className="mono">{row.discord_user_id}</small></span></td><td className="muted">{row.roles}</td></tr>)}</tbody></table></div></>}
      {syncResult.renamed.length > 0 && <><h3 className="sub-heading">Discord handles updated</h3><div className="table-wrap import-results"><table><tbody>{syncResult.renamed.map((row) => <tr key={row.discord_user_id}><td className="muted">{row.old} →</td><td><span className="member-cell"><strong>{row.discord_username}</strong><small className="mono">{row.discord_user_id}</small></span></td></tr>)}</tbody></table></div></>}
      {syncResult.added.length > 0 && <><h3 className="sub-heading">Newly registered (no X yet)</h3><div className="table-wrap import-results"><table><tbody>{syncResult.added.map((row) => <tr key={row.discord_user_id}><td><span className="member-cell"><strong>{row.discord_username}</strong><small className="mono">{row.discord_user_id}</small></span></td></tr>)}</tbody></table></div></>}
      {syncResult.partial_list_guard && <p className="estimate-warning">Discord served far fewer members than the registry holds, which usually means the member list was cut short. <strong>Nobody was deactivated.</strong> Run the sync again in a minute; if it keeps happening, check the bot's Server Members Intent.</p>}
      {syncResult.deactivated.length > 0 && <><h3 className="sub-heading">Left the server, now marked inactive</h3><p className="group-note">They are out of the leaderboard, the low-activity report and future scans. Their points and history are kept, so reactivating them puts everything back. Nothing happened to their Discord account.</p><div className="table-wrap import-results"><table><tbody>{syncResult.deactivated.map((row) => <tr key={row.discord_user_id}><td><span className="member-cell"><strong>{row.discord_username}</strong><small className="mono">{row.discord_user_id}</small></span></td></tr>)}</tbody></table></div></>}
      {syncResult.left_server.length > 0 && !syncResult.deactivated.length && <><h3 className="sub-heading">In the registry but not in the server</h3><div className="table-wrap import-results"><table><tbody>{syncResult.left_server.map((row) => <tr key={row.discord_user_id}><td><span className="member-cell"><strong>{row.discord_username}</strong><small className="mono">{row.discord_user_id}</small></span></td></tr>)}</tbody></table></div></>}
      {syncResult.back_in_server.length > 0 && <><h3 className="sub-heading">Back in the server, still marked inactive</h3><p className="group-note">Left alone on purpose, in case you deactivated them yourself. Open anyone here and reactivate them to put them back on the leaderboard.</p><div className="table-wrap import-results"><table><tbody>{syncResult.back_in_server.map((row) => <tr key={row.discord_user_id}><td><a className="member-cell linked-cell" href={`#member?id=${row.discord_user_id}`} onClick={() => setSyncResult(undefined)}><strong>{row.discord_username}</strong><small className="mono">{row.discord_user_id}</small></a></td></tr>)}</tbody></table></div></>}
      <div className="modal-actions"><button className="button primary" onClick={() => setSyncResult(undefined)}>Done</button></div>
    </div></div>}
  </div>
}
