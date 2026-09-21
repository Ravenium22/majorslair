import { Component, useEffect, useState, type ErrorInfo, type ReactNode } from 'react'
import { AlertCircle, Check, ChevronDown, ChevronUp, ChevronsUpDown, LoaderCircle, X } from 'lucide-react'
import { formatCount } from './api'

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
  return <span className={`status ${value}`}><i />{state}</span>
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
