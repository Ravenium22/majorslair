import { Component, createContext, useCallback, useContext, useEffect, useRef, useState, type ErrorInfo, type ReactNode } from 'react'
import { AlertCircle, AlertTriangle, Check, ChevronDown, ChevronUp, ChevronsUpDown, CircleHelp, Coins, Ellipsis, LoaderCircle, Search, X } from 'lucide-react'
import { formatCount } from './api'
import type { DiscordRole } from './types'

/** Close something with the Escape key while it is open. */
export function useEscape(active: boolean, onClose: () => void) {
  useEffect(() => {
    if (!active) return
    const handler = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [active, onClose])
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return <p className="loading-row" role="status"><LoaderCircle className="spin" size={16} /> {label}</p>
}

export function PageHeader({
  eyebrow,
  title,
  copy,
  actions,
  toolbar,
}: {
  eyebrow?: string
  title: string
  copy: string
  actions?: ReactNode
  /** Secondary tools rendered on their own row under the title, so many buttons never squeeze it. */
  toolbar?: ReactNode
}) {
  return (
    <header className={`page-header ${toolbar ? 'has-toolbar' : ''}`}>
      <div className="page-header-main">
        <div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h1>{title}</h1><p>{copy}</p></div>
        {actions && <div className="header-actions">{actions}</div>}
      </div>
      {toolbar && <div className="header-toolbar">{toolbar}</div>}
    </header>
  )
}

export function Status({ state }: { state: string }) {
  const value = state.toLowerCase()
  // States arrive as data ("complete", "running"); written as a word, not shouted.
  return <span className={`status ${value}`}><i />{value.charAt(0).toUpperCase() + value.slice(1)}</span>
}

export function Empty({ title, copy }: { title: string; copy: string }) {
  return <div className="empty"><AlertCircle size={22} /><strong>{title}</strong><p>{copy}</p></div>
}

export function Toast({ message, kind = 'success' }: { message: string; kind?: 'success' | 'error' | 'loading' }) {
  const [visible, setVisible] = useState(true)
  useEffect(() => {
    setVisible(true)
    if (kind === 'loading') return
    const timer = setTimeout(() => setVisible(false), kind === 'error' ? 10000 : 6000)
    return () => clearTimeout(timer)
  }, [message, kind])
  if (!visible) return null
  return (
    <div className={`toast ${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
      {kind === 'loading' ? <LoaderCircle className="spin" size={17} /> : kind === 'success' ? <Check size={17} /> : <AlertCircle size={17} />}
      <span>{message}</span>
      <button className="toast-close" onClick={() => setVisible(false)} aria-label="Dismiss"><X size={14} /></button>
    </div>
  )
}

export function Pagination({ page, size, total, onChange }: { page: number; size: number; total: number; onChange: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / size))
  return (
    <div className="pagination">
      <span>{formatCount(total)} records</span>
      {pages > 1 && <div><button disabled={page <= 1} onClick={() => onChange(page - 1)}>Previous</button><span>{page} / {pages}</span><button disabled={page >= pages} onClick={() => onChange(page + 1)}>Next</button>{pages > 2 && <label className="page-jump">Go to<input type="number" min={1} max={pages} defaultValue={page} key={page} onKeyDown={(e) => { if (e.key === 'Enter') { const value = Math.min(pages, Math.max(1, Number((e.target as HTMLInputElement).value) || 1)); onChange(value) } }} onBlur={(e) => { const value = Math.min(pages, Math.max(1, Number(e.target.value) || 1)); if (value !== page) onChange(value) }} /></label>}</div>}
    </div>
  )
}


/** A column header you can sort by. `direction` is undefined when this column is not the
 *  one in use, so the control still shows that sorting is available. */
export function SortTh({ label, direction, onToggle, className }: {
  label: string
  direction?: 'asc' | 'desc'
  onToggle: () => void
  className?: string
}) {
  return (
    <th
      className={`${className ?? ''} sortable`.trim()}
      aria-sort={direction === 'asc' ? 'ascending' : direction === 'desc' ? 'descending' : 'none'}
    >
      <button type="button" className={direction ? 'th-sort active' : 'th-sort'} onClick={onToggle}>
        {label}
        {direction === 'asc' ? <ChevronUp size={13} /> : direction === 'desc' ? <ChevronDown size={13} /> : <ChevronsUpDown size={13} className="th-sort-idle" />}
      </button>
    </th>
  )
}


/** One render error used to blank the whole dashboard, which is indistinguishable from a
 *  failed deploy. Show what broke and offer a way back instead. */
export class PageErrorBoundary extends Component<
  { children: ReactNode; onReset?: () => void },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Dashboard render failed', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="page">
        <div className="panel page-error">
          <h2>This page could not be drawn</h2>
          <p>
            Something in the data the server returned was not the shape this page expects.
            Nothing has been changed. Reloading usually clears it; if it keeps happening, the
            message below is what to report.
          </p>
          <pre>{this.state.error.message}</pre>
          <div className="modal-actions left">
            <button className="button" onClick={() => { this.setState({ error: null }); this.props.onReset?.() }}>
              Try this page again
            </button>
            <button className="button primary" onClick={() => window.location.reload()}>
              Reload the dashboard
            </button>
          </div>
        </div>
      </div>
    )
  }
}


export type ConfirmOptions = {
  title: string
  /** What will happen, in a sentence or two. Say the consequence, not "are you sure". */
  body: ReactNode
  confirmLabel: string
  cancelLabel?: string
  /** danger: changes something people will notice or hard to undo. cost: spends credits. */
  tone?: 'default' | 'danger' | 'cost'
}

type PendingConfirm = { options: ConfirmOptions; resolve: (answer: boolean) => void }

const ConfirmContext = createContext<(options: ConfirmOptions) => Promise<boolean>>(async () => true)

/** Every action that changes data asks first. One dialog for all of them, so each page only
 *  has to write `if (!(await confirm({...}))) return` in front of the call. */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<PendingConfirm>()
  const confirm = useCallback(
    (options: ConfirmOptions) => new Promise<boolean>((resolve) => setPending({ options, resolve })),
    [],
  )
  const answer = useCallback((value: boolean) => {
    setPending((current) => { current?.resolve(value); return undefined })
  }, [])

  useEffect(() => {
    if (!pending) return
    // Capture phase on the window, stopped here: pages close their own dialogs on Escape
    // from a window listener, and Escape on this box must not also close the one under it.
    const handler = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      answer(false)
    }
    window.addEventListener('keydown', handler, true)
    return () => window.removeEventListener('keydown', handler, true)
  }, [pending, answer])

  const tone = pending?.options.tone ?? 'default'
  const Icon = tone === 'danger' ? AlertTriangle : tone === 'cost' ? Coins : CircleHelp
  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {pending && (
        <div className="modal-backdrop stacked confirm-backdrop" onMouseDown={() => answer(false)}>
          <div className={`modal modal-narrow confirm-dialog ${tone}`} onMouseDown={(event) => event.stopPropagation()}>
            <div className={`modal-icon ${tone === 'danger' ? 'danger-icon' : ''}`}><Icon /></div>
            <h2>{pending.options.title}</h2>
            <div className="confirm-body">{pending.options.body}</div>
            <div className="modal-actions">
              {/* Something hard to undo opens on Cancel, so Enter does the safe thing. */}
              <button type="button" className="button ghost" onClick={() => answer(false)} data-dialog-focus={tone === 'danger' ? '' : undefined}>
                {pending.options.cancelLabel ?? 'Cancel'}
              </button>
              <button type="button" className={`button ${tone === 'danger' ? 'danger' : 'primary'}`} onClick={() => answer(true)} data-dialog-focus={tone === 'danger' ? undefined : ''}>
                {pending.options.confirmLabel}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  )
}

export const useConfirm = () => useContext(ConfirmContext)


/** Roles whose holders a bulk role change must leave alone: chips for what is picked, then a
 *  filterable list. Shared by Give role and the low-activity purge, so both behave the same. */
export function RoleExclusionPicker({ roles, value, onChange, disabled }: {
  roles: DiscordRole[]
  value: string[]
  onChange: (next: string[]) => void
  disabled?: boolean
}) {
  const [search, setSearch] = useState('')
  const picked = roles.filter((role) => value.includes(role.id))
  const visible = roles.filter((role) => role.name.toLowerCase().includes(search.trim().toLowerCase()))
  const boosterNote = (role: DiscordRole) => role.booster && !/boost/i.test(role.name)
  return (
    <fieldset className="exclude-roles" disabled={disabled}>
      <legend>Leave alone anyone who has one of these roles</legend>
      <p className="field-hint">Checked live at the moment you apply, so a role given after the preview still counts. Server Booster is on by default.</p>
      {picked.length > 0 && <ul className="role-chips">{picked.map((role) => <li key={role.id}><button type="button" onClick={() => onChange(value.filter((id) => id !== role.id))} aria-label={`Stop leaving ${role.name} alone`}>{role.name}{boosterNote(role) ? ' · booster' : ''}<X size={13} /></button></li>)}</ul>}
      <label className="role-search"><Search size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={`Search ${roles.length} roles`} /></label>
      <div className="role-options" role="group" aria-label="Roles to leave alone">
        {visible.map((role) => <label key={role.id}><input type="checkbox" checked={value.includes(role.id)} onChange={(event) => onChange(event.target.checked ? [...value, role.id] : value.filter((id) => id !== role.id))} /><span>{role.name}{boosterNote(role) ? <em>Server Booster</em> : null}</span></label>)}
        {visible.length === 0 && <p className="field-hint">No role matches that.</p>}
      </div>
    </fieldset>
  )
}


/** A quiet pointer to the section of How it works that explains the thing next to it. */
export function HelpLink({ topic, label = 'How this works' }: { topic: string; label?: string }) {
  return <a className="help-link" href={`#help?topic=${topic}`}><CircleHelp size={13} aria-hidden="true" />{label}</a>
}


export type MenuItem = {
  label: string
  hint?: string
  icon?: ReactNode
  onSelect?: () => void
  href?: string
  disabled?: boolean
}

/** Less frequent actions behind one button, so a page header shows only what is used weekly.
 *  Arrow keys move, Escape closes and hands focus back, a click outside closes. */
export function ToolsMenu({ label = 'More tools', items }: { label?: string; items: MenuItem[] }) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const first = root.current?.querySelector<HTMLElement>('[role="menuitem"]:not([aria-disabled="true"])')
    first?.focus()
    const onPointer = (event: MouseEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false) }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.stopPropagation(); setOpen(false); trigger.current?.focus(); return }
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
      const entries = [...(root.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([aria-disabled="true"])') ?? [])]
      if (!entries.length) return
      event.preventDefault()
      const at = entries.indexOf(document.activeElement as HTMLElement)
      const next = event.key === 'ArrowDown' ? (at + 1) % entries.length : (at - 1 + entries.length) % entries.length
      entries[next].focus()
    }
    document.addEventListener('mousedown', onPointer)
    window.addEventListener('keydown', onKey, true)
    return () => { document.removeEventListener('mousedown', onPointer); window.removeEventListener('keydown', onKey, true) }
  }, [open])

  return (
    <div className="tools-menu" ref={root}>
      <button ref={trigger} type="button" className="button" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        <Ellipsis size={17} /> {label}
      </button>
      {open && (
        <div className="tools-menu-list" role="menu" aria-label={label}>
          {items.map((item) => {
            const content = <>{item.icon}<span>{item.label}{item.hint && <small>{item.hint}</small>}</span></>
            return item.href
              ? <a key={item.label} role="menuitem" href={item.href} aria-disabled={item.disabled || undefined} onClick={() => setOpen(false)}>{content}</a>
              : <button key={item.label} type="button" role="menuitem" aria-disabled={item.disabled || undefined} onClick={() => { if (item.disabled) return; setOpen(false); item.onSelect?.() }}>{content}</button>
          })}
        </div>
      )}
    </div>
  )
}
