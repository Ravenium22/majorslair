import { useState, type FormEvent } from 'react'
import { ExternalLink, Plus, Radar, ToggleLeft, ToggleRight } from 'lucide-react'
import useSWR from 'swr'
import { api, formatDate, mutateApi } from '../api'
import { Empty, PageHeader, Toast } from '../components'
import type { Session, TrackedPost } from '../types'

export default function PostsPage({ session }: { session: Session }) {
  const { data, mutate, isLoading } = useSWR<TrackedPost[]>('/api/tracked-posts', api)
  const [showAdd, setShowAdd] = useState(false)
  const [origin, setOrigin] = useState<'all' | 'manual' | 'auto'>('all')
  const visible = (data ?? []).filter((post) => origin === 'all' || post.origin === origin)
  const manualCount = (data ?? []).filter((post) => post.origin === 'manual').length
  const [notice, setNotice] = useState<{ text: string; kind: 'success' | 'error' | 'loading' }>()

  const add = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const url = String(new FormData(event.currentTarget).get('url'))
    setNotice({ text: 'Verifying X post…', kind: 'loading' })
    try {
      await mutateApi('/api/tracked-posts', session.csrf_token, 'POST', { url })
      setNotice({ text: 'Post verified and added to the scan scope.', kind: 'success' })
      setShowAdd(false)
      await mutate()
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Could not track post', kind: 'error' }) }
  }

  const toggle = async (post: TrackedPost) => {
    try {
      await mutateApi(`/api/tracked-posts/${post.tweet_id}`, session.csrf_token, 'PATCH', { active: !post.active })
      await mutate()
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Update failed', kind: 'error' }) }
  }

  return <div className="page">
    <PageHeader eyebrow="Source intelligence" title="Tracked posts" copy="Every post whose replies, quotes, and retweets feed scoring. Scans add the tracked accounts' recent posts automatically (origin: auto) so deleted posts can be detected later and any post can be paused; pinned posts you add by hand are origin: manual." actions={<><div className="segmented">{(['all', 'auto', 'manual'] as const).map((value) => <button className={origin === value ? 'active' : ''} onClick={() => setOrigin(value)} key={value}>{value === 'all' ? `All ${data?.length ?? 0}` : value === 'auto' ? `Auto ${(data?.length ?? 0) - manualCount}` : `Manual ${manualCount}`}</button>)}</div><button className="button primary" onClick={() => setShowAdd(true)}><Plus size={17} /> Add post</button></>} />
    {notice && <Toast message={notice.text} kind={notice.kind} />}
    <section className="posts-grid">
      {visible.map((post) => <article className={`post-card ${post.active ? '' : 'inactive'}`} key={post.tweet_id}><div className="post-top"><div className="x-mark">𝕏</div><span className={`status ${post.active ? 'complete' : 'failed'}`}><i />{post.active ? 'Tracking' : 'Paused'}</span></div><p className="eyebrow">@{post.source_handle}</p><h3>Post {post.tweet_id}</h3><dl><div><dt>Origin</dt><dd>{post.origin}</dd></div><div><dt>Published</dt><dd>{formatDate(post.post_created_at)}</dd></div><div><dt>Last checked</dt><dd>{formatDate(post.last_checked_at)}</dd></div></dl><div className="post-actions"><a href={post.url} target="_blank">Open on X <ExternalLink size={14} /></a><button className="icon-button" onClick={() => toggle(post)} title={post.active ? 'Pause tracking' : 'Resume tracking'}>{post.active ? <ToggleRight /> : <ToggleLeft />}</button></div></article>)}
      {!isLoading && !visible.length && <div className="panel span-all"><Empty title="No posts tracked yet" copy="Automatic scans discover recent target-account posts. You can also pin an important post manually." /></div>}
    </section>
    {showAdd && <div className="modal-backdrop" onMouseDown={() => setShowAdd(false)}><form className="modal" onSubmit={add} onMouseDown={(e) => e.stopPropagation()}><div className="modal-icon"><Radar /></div><p className="eyebrow">Manual source</p><h2>Track an X post</h2><p>Only posts from the two configured target handles are accepted.</p><label>Full X status URL<input required type="url" name="url" placeholder="https://x.com/handle/status/…" /></label><div className="modal-actions"><button type="button" className="button ghost" onClick={() => setShowAdd(false)}>Cancel</button><button className="button primary">Verify & track</button></div></form></div>}
  </div>
}
