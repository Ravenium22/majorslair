import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react'
import {
  Activity,
  BookOpenCheck,
  Bot,
  ChartNoAxesColumnIncreasing,
  CircleGauge,
  Database,
  LogOut,
  Menu,
  Radar,
  Settings2,
  Users,
  X,
} from 'lucide-react'
import useSWR from 'swr'
import { api, mutateApi, type ApiError } from './api'
import type { Session } from './types'
import OverviewPage from './pages/OverviewPage'
import MembersPage from './pages/MembersPage'
import PostsPage from './pages/PostsPage'
import ScoringPage from './pages/ScoringPage'

const ActivityPage = lazy(() => import('./pages/ActivityPage'))
const AuditPage = lazy(() => import('./pages/AuditPage'))

const routes = [
  { id: 'overview', label: 'Overview', icon: CircleGauge },
  { id: 'members', label: 'Members', icon: Users },
  { id: 'activity', label: 'Activity log', icon: Activity },
  { id: 'posts', label: 'Tracked posts', icon: Radar },
  { id: 'scoring', label: 'Scoring rules', icon: Settings2 },
  { id: 'audit', label: 'Audit trail', icon: BookOpenCheck },
] as const

type RouteId = (typeof routes)[number]['id']

const fetcher = <T,>(url: string) => api<T>(url)

function Login() {
  return (
    <main className="login-shell">
      <div className="login-grid" aria-hidden="true" />
      <section className="login-card">
        <div className="brand-mark"><ChartNoAxesColumnIncreasing size={28} /></div>
        <p className="eyebrow">Major's Lair · Internal</p>
        <h1>Engagement<br />control center.</h1>
        <p className="login-copy">
          Track meaningful X activity, tune scoring, and manage leaderboard cycles from one
          operational console.
        </p>
        <a className="button primary login-button" href="/auth/login">
          <Bot size={18} /> Continue with Discord
        </a>
        <div className="login-foot">
          <span><i className="status-dot" /> Admin role required</span>
          <span>OAuth secured</span>
        </div>
      </section>
      <aside className="login-aside">
        <p>Signal over noise</p>
        <strong>Reward thoughtful engagement.<br />See every scoring decision.</strong>
        <div className="signal-bars" aria-hidden="true">
          {[18, 30, 44, 62, 84, 54, 72, 92].map((height, index) => (
            <span key={index} style={{ height: `${height}%` }} />
          ))}
        </div>
      </aside>
    </main>
  )
}

function Shell({ session, children }: { session: Session; children: ReactNode }) {
  const [route, setRoute] = useState<RouteId>(() => {
    const hash = window.location.hash.slice(1) as RouteId
    return routes.some((item) => item.id === hash) ? hash : 'overview'
  })
  const [mobileOpen, setMobileOpen] = useState(false)

  useEffect(() => {
    const onHash = () => {
      const hash = window.location.hash.slice(1) as RouteId
      if (routes.some((item) => item.id === hash)) setRoute(hash)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const navigate = (next: RouteId) => {
    window.location.hash = next
    setRoute(next)
    setMobileOpen(false)
  }

  const logout = async () => {
    await mutateApi('/api/logout', session.csrf_token, 'POST')
    window.location.reload()
  }

  const page = (() => {
    switch (route) {
      case 'members': return <MembersPage session={session} />
      case 'activity': return <ActivityPage />
      case 'posts': return <PostsPage session={session} />
      case 'scoring': return <ScoringPage session={session} />
      case 'audit': return <AuditPage />
      default: return <OverviewPage session={session} />
    }
  })()

  return (
    <div className="app-shell">
      <button className="mobile-menu" onClick={() => setMobileOpen(true)} aria-label="Open menu">
        <Menu />
      </button>
      {mobileOpen && <button className="nav-scrim" onClick={() => setMobileOpen(false)} />}
      <aside className={`sidebar ${mobileOpen ? 'open' : ''}`}>
        <button className="mobile-close" onClick={() => setMobileOpen(false)} aria-label="Close menu">
          <X />
        </button>
        <div className="brand">
          <div className="brand-mark small"><ChartNoAxesColumnIncreasing size={20} /></div>
          <div><strong>MAJOR'S LAIR</strong><span>ENGAGEMENT OPS</span></div>
        </div>
        <nav>
          <p className="nav-label">Control room</p>
          {routes.map((item) => {
            const Icon = item.icon
            return (
              <button
                key={item.id}
                className={route === item.id ? 'active' : ''}
                onClick={() => navigate(item.id)}
              >
                <Icon size={18} /> {item.label}
              </button>
            )
          })}
        </nav>
        <div className="sidebar-system">
          <p className="nav-label">Infrastructure</p>
          <div><Database size={16} /><span>Railway Postgres<small>Managed · persistent</small></span></div>
          <div><Bot size={16} /><span>Discord gateway<small>Role protected</small></span></div>
        </div>
        <div className="profile">
          {session.user.avatar_url ? (
            <img src={session.user.avatar_url} alt="" />
          ) : (
            <span className="avatar-fallback">{session.user.username[0]}</span>
          )}
          <span><strong>{session.user.username}</strong><small>Engagement admin</small></span>
          <button onClick={logout} aria-label="Sign out"><LogOut size={17} /></button>
        </div>
      </aside>
      <main className="workspace">
        <Suspense fallback={<div className="page-loading"><span /> Loading control surface…</div>}>
          {page}
        </Suspense>
      </main>
      {children}
    </div>
  )
}

export default function App() {
  const { data, error, isLoading } = useSWR<Session, ApiError>('/api/session', fetcher, {
    shouldRetryOnError: false,
  })

  if (isLoading) {
    return <div className="boot"><div className="brand-mark"><ChartNoAxesColumnIncreasing /></div><span>Initializing console</span></div>
  }
  if (error?.status === 401 || error?.status === 403 || !data) return <Login />
  if (error) return <div className="fatal"><h1>Console unavailable</h1><p>{error.message}</p></div>
  return <Shell session={data}><span /></Shell>
}
