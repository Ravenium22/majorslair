import { useEffect, useState, type ReactNode } from 'react'
import { AlertCircle, Check, LoaderCircle, X } from 'lucide-react'

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
  eyebrow: string
  title: string
  copy: string
  actions?: ReactNode
  /** Secondary tools rendered on their own row under the title, so many buttons never squeeze it. */
  toolbar?: ReactNode
}) {
  return (
    <header className={`page-header ${toolbar ? 'has-toolbar' : ''}`}>
      <div className="page-header-main">
        <div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p>{copy}</p></div>
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
      <span>{total.toLocaleString()} records</span>
      <div><button disabled={page <= 1} onClick={() => onChange(page - 1)}>Previous</button><span>{page} / {pages}</span><button disabled={page >= pages} onClick={() => onChange(page + 1)}>Next</button>{pages > 2 && <label className="page-jump">Go to<input type="number" min={1} max={pages} defaultValue={page} key={page} onKeyDown={(e) => { if (e.key === 'Enter') { const value = Math.min(pages, Math.max(1, Number((e.target as HTMLInputElement).value) || 1)); onChange(value) } }} onBlur={(e) => { const value = Math.min(pages, Math.max(1, Number(e.target.value) || 1)); if (value !== page) onChange(value) }} /></label>}</div>
    </div>
  )
}
