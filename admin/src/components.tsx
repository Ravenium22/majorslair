import type { ReactNode } from 'react'
import { AlertCircle, Check, LoaderCircle } from 'lucide-react'

export function PageHeader({
  eyebrow,
  title,
  copy,
  actions,
}: {
  eyebrow: string
  title: string
  copy: string
  actions?: ReactNode
}) {
  return (
    <header className="page-header">
      <div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p>{copy}</p></div>
      {actions && <div className="header-actions">{actions}</div>}
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
  return (
    <div className={`toast ${kind}`}>
      {kind === 'loading' ? <LoaderCircle className="spin" size={17} /> : kind === 'success' ? <Check size={17} /> : <AlertCircle size={17} />}
      {message}
    </div>
  )
}

export function Pagination({ page, size, total, onChange }: { page: number; size: number; total: number; onChange: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / size))
  return (
    <div className="pagination">
      <span>{total.toLocaleString()} records</span>
      <div><button disabled={page <= 1} onClick={() => onChange(page - 1)}>Previous</button><span>{page} / {pages}</span><button disabled={page >= pages} onClick={() => onChange(page + 1)}>Next</button></div>
    </div>
  )
}
