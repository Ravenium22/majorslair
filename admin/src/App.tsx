import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react'
import type React from 'react'
import {
  Activity,
  BookOpenCheck,
  Bot,
  ChartNoAxesColumnIncreasing,
  CircleHelp,
  CircleGauge,
  LoaderCircle,
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
import { api, formatDate, formatUsd, mutateApi, type ApiError } from './api'
import type { Overview, Session } from './types'
import { ConfirmProvider, PageErrorBoundary, useConfirm } from './components'
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
const HelpPage = lazyPage(() => import('./pages/HelpPage'))
const MemberPage = lazyPage(() => import('./pages/MemberPage'))

const routes = [
  { id: 'overview', label: 'Overview', icon: CircleGauge },
  { id: 'members', label: 'Members', icon: Users },
  { id: 'low-activity', label: 'Low-activity report', icon: UserRoundMinus },
  { id: 'activity', label: 'Activity log', icon: Activity },
  { id: 'posts', label: 'Tracked posts', icon: Radar },
  { id: 'scans', label: 'Scan reports', icon: ScrollText },
  { id: 'scoring', label: 'Scoring rules', icon: Settings2 },
  { id: 'audit', label: 'Audit trail', icon: BookOpenCheck },
  { id: 'help', label: 'How it works', icon: CircleHelp },
] as const

// Pages reached from other pages rather than from the sidebar.
const hiddenRoutes = ['member'] as const
type RouteId = (typeof routes)[number]['id'] | (typeof hiddenRoutes)[number]

const fetcher = <T,>(url: string) => api<T>(url)

function Login() {
  return (
    <main className="login-shell">
      <section className="login-card">
        <div className="brand-mark"><ChartNoAxesColumnIncreasing size={28} /></div>
        <h1>Major's Lair</h1>
        <p className="login-copy">Engagement scoring for the community. Sign in with the Discord account that holds the admin role.</p>
        <a className="button primary login-button" href="/auth/login">
          <Bot size={18} /> Continue with Discord
        </a>
      </section>
    </main>
  )
}

function Shell({ session, children }: { session: Session; children: ReactNode }) {
  const { data: overview } = useSWR<Overview>('/api/overview', fetcher, { refreshInterval: 30000 })
  // Routes look like #members?points=low, so a filtered view can be reloaded or linked.
  const readRoute = () => {
    const hash = window.location.hash.slice(1).split('?')[0] as RouteId
    return routes.some((item) => item.id === hash) || (hiddenRoutes as readonly string[]).includes(hash) ? hash : 'overview'
  }
  const [route, setRoute] = useState<RouteId>(readRoute)
  const [mobileOpen, setMobileOpen] = useState(false)

  useEffect(() => {
    const onHash = () => setRoute(readRoute())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => {
    if (/[?&](topic|section)=/.test(window.location.hash)) return
    window.scrollTo({ top: 0, behavior: 'auto' })
  }, [route])

  const navigate = (next: RouteId) => {
    window.location.hash = next
    setRoute(next)
    setMobileOpen(false)
  }

  const confirm = useConfirm()
  const logout = async () => {
    if (!(await confirm({ title: 'Sign out?', body: 'You will need to sign in with Discord again to get back in. A scan that is running keeps running.', confirmLabel: 'Sign out' }))) return
    await mutateApi('/api/logout', session.csrf_token, 'POST')
    window.location.reload()
  }

  const page = (() => {
    switch (route) {
      case 'members': return <MembersPage session={session} />
      case 'activity': return <ActivityPage session={session} />
      case 'posts': return <PostsPage session={session} />
      case 'scans': return <ScansPage />
      case 'low-activity': return <LowActivityPage session={session} />
      case 'help': return <HelpPage />
      case 'member': return <MemberPage session={session} />
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
          <div><strong>MAJOR'S LAIR</strong><span>Engagement dashboard</span></div>
        </div>
        <nav>
          {routes.map((item) => {
            const Icon = item.icon
            return (
              <a
                key={item.id}
                href={`#${item.id}`}
                className={route === item.id || (route === 'member' && item.id === 'members') ? 'active' : ''}
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
          {overview?.last_scan?.status === 'running' && <a className="scan-ticker" href="#overview" onClick={(event) => { if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; event.preventDefault(); navigate('overview') }}><LoaderCircle size={15} className="spin" /><span>Scan running<small>{overview.last_scan.period} window · started {formatDate(overview.last_scan.started_at)}</small></span></a>}
          <div><Coins size={16} /><span>{overview ? `${formatUsd(overview.credits_this_month)} this month` : 'Spend this month'}<small>{overview ? `${overview.scans_this_month} scan${overview.scans_this_month === 1 ? '' : 's'} on twitterapi.io` : 'twitterapi.io'}</small></span></div>
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
        <PageErrorBoundary key={route}>
          <Suspense fallback={<div className="page-loading"><span /> Loading…</div>}>
            {page}
          </Suspense>
        </PageErrorBoundary>
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
    // Dialogs can now stack: a confirmation opens over the dialog that asked for it. Each one
    // remembers where focus was when it opened, and gets it back when it closes.
    const opened = new Map<HTMLElement, HTMLElement | null>()
    const focusable = (root: HTMLElement) => [...root.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )].filter((el) => el.offsetParent !== null)

    /** The dialog on top: a confirmation, then any stacked dialog, then the last one opened. */
    const topDialog = () => {
      for (const selector of ['.confirm-backdrop .modal', '.modal-backdrop.stacked .modal', '.modal-backdrop .modal']) {
        const found = document.querySelectorAll<HTMLElement>(selector)
        if (found.length) return found[found.length - 1]
      }
      return null
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return
      const dialog = topDialog()
      if (!dialog) return
      const items = focusable(dialog)
      if (!items.length) return
      const first = items[0]
      const last = items[items.length - 1]
      const active = document.activeElement as HTMLElement | null
      if (event.shiftKey && (active === first || !dialog.contains(active))) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && (active === last || !dialog.contains(active))) { event.preventDefault(); first.focus() }
    }

    const sync = () => {
      const present = new Set(document.querySelectorAll<HTMLElement>('.modal-backdrop .modal'))
      for (const [dialog, restoreTo] of [...opened]) {
        if (present.has(dialog)) continue
        opened.delete(dialog)
        if (restoreTo?.isConnected) restoreTo.focus()
      }
      for (const dialog of present) {
        if (opened.has(dialog)) continue
        opened.set(dialog, document.activeElement as HTMLElement | null)
        dialog.setAttribute('role', dialog.classList.contains('confirm-dialog') ? 'alertdialog' : 'dialog')
        dialog.setAttribute('aria-modal', 'true')
        const heading = dialog.querySelector('h2')
        if (heading) {
          if (!heading.id) heading.id = `dialog-title-${Math.random().toString(36).slice(2, 8)}`
          dialog.setAttribute('aria-labelledby', heading.id)
        }
      }
      const top = topDialog()
      if (top && !top.contains(document.activeElement)) {
        const preferred = top.querySelector<HTMLElement>('[data-dialog-focus]')
        ;(preferred ?? focusable(top)[0] ?? top).focus()
      }
      document.body.style.overflow = present.size ? 'hidden' : ''
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
    return <div className="boot"><div className="brand-mark"><ChartNoAxesColumnIncreasing /></div><span>Loading the dashboard…</span></div>
  }
  if (error?.status === 401 || error?.status === 403 || !data) return <Login />
  if (error) return <div className="fatal"><h1>Console unavailable</h1><p>{error.message}</p></div>
  return <ConfirmProvider><Shell session={data}><span /></Shell></ConfirmProvider>
}
