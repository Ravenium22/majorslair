import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react'
import type React from 'react'
import {
  Activity,
  BookOpenCheck,
  Bot,
  ChartNoAxesColumnIncreasing,
  CircleGauge,
  LogOut,
  Menu,
  Coins,
  Radar,
  ScrollText,
  UserRoundMinus,
  Settings2,
  Users,
  X,
} from 'lucide-react'
import useSWR from 'swr'
import { api, mutateApi, type ApiError } from './api'
import type { Overview, Session } from './types'
import OverviewPage from './pages/OverviewPage'
import MembersPage from './pages/MembersPage'
import PostsPage from './pages/PostsPage'
import ScoringPage from './pages/ScoringPage'

// Pages below load as separate files. After a deploy the old file names vanish, so a
// dashboard that was already open would show a blank page until reloaded. On a failed
// load we reload once to pick up the new build instead of leaving the page blank.
function lazyPage<T extends { default: React.ComponentType<any> }>(load: () => Promise<T>) {
  return lazy(() => load().then((module) => { try { sessionStorage.removeItem('chunk-reload') } catch { /* ignore */ } return module }).catch((error: unknown) => {
    let reloaded = false
    try { reloaded = sessionStorage.getItem('chunk-reload') === '1'; if (!reloaded) sessionStorage.setItem('chunk-reload', '1') } catch { /* ignore */ }
    if (!reloaded) { window.location.reload(); return new Promise<T>(() => {}) }
    throw error
  }))
}

const ActivityPage = lazyPage(() => import('./pages/ActivityPage'))
const AuditPage = lazyPage(() => import('./pages/AuditPage'))
const ScansPage = lazyPage(() => import('./pages/ScansPage'))
const LowActivityPage = lazyPage(() => import('./pages/LowActivityPage'))

const routes = [
  { id: 'overview', label: 'Overview', icon: CircleGauge },
  { id: 'members', label: 'Members', icon: Users },
  { id: 'low-activity', label: 'Low-activity report', icon: UserRoundMinus },
  { id: 'activity', label: 'Activity log', icon: Activity },
  { id: 'posts', label: 'Tracked posts', icon: Radar },
  { id: 'scans', label: 'Scan reports', icon: ScrollText },
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
  const { data: overview } = useSWR<Overview>('/api/overview', fetcher, { refreshInterval: 30000 })
  // Routes look like #members?points=low, so a filtered view can be reloaded or linked.
  const readRoute = () => {
    const hash = window.location.hash.slice(1).split('?')[0] as RouteId
    return routes.some((item) => item.id === hash) ? hash : 'overview'
  }
  const [route, setRoute] = useState<RouteId>(readRoute)
  const [mobileOpen, setMobileOpen] = useState(false)

  useEffect(() => {
    const onHash = () => setRoute(readRoute())
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
      case 'activity': return <ActivityPage session={session} />
      case 'posts': return <PostsPage session={session} />
      case 'scans': return <ScansPage />
      case 'low-activity': return <LowActivityPage />
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
          {routes.map((item) => {
            const Icon = item.icon
            return (
              <a
                key={item.id}
                href={`#${item.id}`}
                className={route === item.id ? 'active' : ''}
                onClick={(event) => {
                  // Plain left-click navigates in place; modified clicks and right-click
                  // "open in new tab" keep the browser's default link behaviour.
                  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
                  event.preventDefault()
                  navigate(item.id)
                }}
              >
                <Icon size={18} /> {item.label}
              </a>
            )
          })}
        </nav>
        <div className="sidebar-system">
          <div><Coins size={16} /><span>{overview ? `$${(overview.credits_this_month / 100000).toFixed(2)} this month` : 'Spend this month'}<small>{overview ? `${overview.scans_this_month} scan${overview.scans_this_month === 1 ? '' : 's'} on twitterapi.io` : 'twitterapi.io'}</small></span></div>
          <div><Bot size={16} /><span>Discord bot<small>{overview?.bot_connected ? 'connected' : 'starting up'}</small></span></div>
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

/** Every dialog in the app is an inline `.modal-backdrop`; rather than rewrite a dozen
 *  call sites, watch for one appearing and give it the semantics and focus behaviour a
 *  dialog needs: a name, a focus trap, and the focus back where it came from. */
function useDialogBehaviour() {
  useEffect(() => {
    let restoreTo: HTMLElement | null = null
    const focusable = (root: HTMLElement) => [...root.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )].filter((el) => el.offsetParent !== null)

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return
      const dialog = document.querySelector<HTMLElement>('.modal-backdrop .modal')
      if (!dialog) return
      const items = focusable(dialog)
      if (!items.length) return
      const first = items[0]
      const last = items[items.length - 1]
      const active = document.activeElement as HTMLElement | null
      if (event.shiftKey && (active === first || !dialog.contains(active))) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && active === last) { event.preventDefault(); first.focus() }
    }

    const sync = () => {
      const dialog = document.querySelector<HTMLElement>('.modal-backdrop .modal')
      if (dialog) {
        if (!dialog.getAttribute('role')) {
          restoreTo = document.activeElement as HTMLElement | null
          dialog.setAttribute('role', 'dialog')
          dialog.setAttribute('aria-modal', 'true')
          const heading = dialog.querySelector('h2')
          if (heading) {
            if (!heading.id) heading.id = `dialog-title-${Math.random().toString(36).slice(2, 8)}`
            dialog.setAttribute('aria-labelledby', heading.id)
          }
          if (!dialog.contains(document.activeElement)) (focusable(dialog)[0] ?? dialog).focus()
        }
        document.body.style.overflow = 'hidden'
      } else {
        document.body.style.overflow = ''
        if (restoreTo?.isConnected) restoreTo.focus()
        restoreTo = null
      }
    }

    const observer = new MutationObserver(sync)
    observer.observe(document.body, { childList: true, subtree: true })
    document.addEventListener('keydown', onKeyDown, true)
    sync()
    return () => { observer.disconnect(); document.removeEventListener('keydown', onKeyDown, true); document.body.style.overflow = '' }
  }, [])
}

export default function App() {
  useDialogBehaviour()
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
