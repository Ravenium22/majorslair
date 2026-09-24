import { useMemo, useState, type FormEvent } from 'react'
import { ExternalLink, PauseCircle, Plus, Radar, Search, ToggleLeft, ToggleRight } from 'lucide-react'
import useSWR from 'swr'
import { api, formatCount, formatDate, mutateApi } from '../api'
import { Empty, Loading, PageHeader, Pagination, Toast, useConfirm, useEscape } from '../components'
import type { Session, TrackedPost } from '../types'

export default function PostsPage({ session }: { session: Session }) {
  const { data, mutate, isLoading } = useSWR<TrackedPost[]>('/api/tracked-posts', api)
  const [showAdd, setShowAdd] = useState(false)
  const [origin, setOrigin] = useState<'all' | 'manual' | 'auto'>('all')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [sort, setSort] = useState<'published' | 'checked' | 'account'>('published')
  const [confirmPause, setConfirmPause] = useState<TrackedPost>()
  const PAGE = 24
  const manualCount = (data ?? []).filter((post) => post.origin === 'manual').length
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return (data ?? [])
      .filter((post) => origin === 'all' || post.origin === origin)
      .filter((post) => !needle || post.tweet_id.includes(needle) || post.source_handle.toLowerCase().includes(needle) || post.url.toLowerCase().includes(needle))
      .sort((a, b) => {
        if (sort === 'account') return a.source_handle.localeCompare(b.source_handle) || (b.post_created_at || '').localeCompare(a.post_created_at || '')
        if (sort === 'checked') return (b.last_checked_at || '').localeCompare(a.last_checked_at || '')
        return (b.post_created_at || '').localeCompare(a.post_created_at || '')
      })
  }, [data, origin, search, sort])
  const visible = filtered.slice((page - 1) * PAGE, page * PAGE)
  useEscape(showAdd, () => setShowAdd(false))
  useEscape(Boolean(confirmPause), () => setConfirmPause(undefined))
  const [notice, setNotice] = useState<{ text: string; kind: 'success' | 'error' | 'loading' }>()
  const confirm = useConfirm()

  const add = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const url = String(new FormData(event.currentTarget).get('url'))
    if (!(await confirm({
      title: 'Track this post?',
      body: 'Its replies, quotes and retweets count towards scoring from the next scan on. The post is checked on X first, and only posts by the two tracked accounts are accepted.',
      confirmLabel: 'Track post',
    }))) return
    setNotice({ text: 'Verifying X post…', kind: 'loading' })
    try {
      await mutateApi('/api/tracked-posts', session.csrf_token, 'POST', { url })
      setNotice({ text: 'Post verified and added to the scan scope.', kind: 'success' })
      setShowAdd(false)
      await mutate()
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Could not track post', kind: 'error' }) }
  }

  const toggle = async (post: TrackedPost) => {
    setConfirmPause(undefined)
    if (!post.active && !(await confirm({
      title: 'Resume tracking this post?',
      body: `Post ${post.tweet_id} from @${post.source_handle} is back in every scan from the next one on, and engagement on it counts again.`,
      confirmLabel: 'Resume tracking',
    }))) return
    try {
      await mutateApi(`/api/tracked-posts/${post.tweet_id}`, session.csrf_token, 'PATCH', { active: !post.active })
      setNotice({ text: post.active ? `Paused. Post ${post.tweet_id} is out of every future scan until you resume it.` : `Resumed. Post ${post.tweet_id} is back in the scan scope.`, kind: 'success' })
      await mutate()
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Update failed', kind: 'error' }) }
  }

  return <div className="page">
    <PageHeader title="Tracked posts" copy="Every post whose replies, quotes, and retweets feed scoring. Scans add the tracked accounts' recent posts automatically (origin: auto) so deleted posts can be detected later and any post can be paused; pinned posts you add by hand are origin: manual." actions={<><div className="segmented">{(['all', 'auto', 'manual'] as const).map((value) => <button className={origin === value ? 'active' : ''} onClick={() => setOrigin(value)} key={value}>{value === 'all' ? `All ${data?.length ?? 0}` : value === 'auto' ? `Auto ${(data?.length ?? 0) - manualCount}` : `Manual ${manualCount}`}</button>)}</div><button className="button primary" onClick={() => setShowAdd(true)}><Plus size={17} /> Add post</button></>} />
    {notice && <Toast message={notice.text} kind={notice.kind} />}
    <section className="panel posts-toolbar"><div className="toolbar"><label className="search"><Search size={17} /><input value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} placeholder="Search post ID, account or URL" /></label><select value={sort} onChange={(e) => { setSort(e.target.value as typeof sort); setPage(1) }} aria-label="Sort posts"><option value="published">Newest published first</option><option value="checked">Most recently checked</option><option value="account">By account</option></select><span className="filter-count">{formatCount(filtered.length)} posts</span></div></section>
    {isLoading && <Loading />}
    <section className="posts-grid">
      {visible.map((post) => <article className={`post-card ${post.active ? '' : 'inactive'}`} key={post.tweet_id}><div className="post-top"><div className="x-mark">𝕏</div><span className={`status ${post.active ? 'complete' : 'failed'}`}><i />{post.active ? 'Tracking' : 'Paused'}</span></div><p className="eyebrow">@{post.source_handle}</p><h3>{post.post_created_at ? `Posted ${formatDate(post.post_created_at)}` : 'Publish date unknown'}</h3><dl><div><dt>Origin</dt><dd>{post.origin}</dd></div><div><dt>Last checked</dt><dd>{formatDate(post.last_checked_at)}</dd></div><div><dt>Post ID</dt><dd className="mono">{post.tweet_id}</dd></div></dl><div className="post-actions"><a href={post.url} target="_blank">Open on X <ExternalLink size={14} /></a><button className="icon-button" onClick={() => (post.active ? setConfirmPause(post) : toggle(post))} title={post.active ? 'Pause tracking' : 'Resume tracking'} aria-label={post.active ? `Pause tracking post ${post.tweet_id}` : `Resume tracking post ${post.tweet_id}`}>{post.active ? <ToggleRight /> : <ToggleLeft />}</button></div></article>)}
      {!isLoading && !visible.length && <div className="panel span-all"><Empty title={search || origin !== 'all' ? 'No posts match' : 'No posts tracked yet'} copy="Automatic scans discover recent target-account posts. You can also pin an important post manually." /></div>}
    </section>
    <section className="panel"><Pagination page={page} size={PAGE} total={filtered.length} onChange={setPage} /></section>
    {confirmPause && <div className="modal-backdrop" onMouseDown={() => setConfirmPause(undefined)}><div className="modal modal-narrow" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal-icon danger-icon"><PauseCircle /></div><h2>Pause this post?</h2>
      <p>Post {confirmPause.tweet_id} from @{confirmPause.source_handle} drops out of every future scan. Points already awarded from it stay. You can resume it here at any time.</p>
      <div className="modal-actions"><button className="button ghost" onClick={() => setConfirmPause(undefined)}>Cancel</button><button className="button danger" onClick={() => toggle(confirmPause)}>Pause tracking</button></div>
    </div></div>}

    {showAdd && <div className="modal-backdrop" onMouseDown={() => setShowAdd(false)}><form className="modal" onSubmit={add} onMouseDown={(e) => e.stopPropagation()}><div className="modal-icon"><Radar /></div><h2>Track an X post</h2><p>Only posts from the two configured target handles are accepted.</p><label>Full X status URL<input required type="url" name="url" placeholder="https://x.com/handle/status/…" /></label><div className="modal-actions"><button type="button" className="button ghost" onClick={() => setShowAdd(false)}>Cancel</button><button className="button primary">Verify & track</button></div></form></div>}
  </div>
}
