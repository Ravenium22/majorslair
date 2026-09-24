export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: 'same-origin',
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    if (response.status === 401 && path !== '/api/session') {
      // The session expired mid-work: hand the person back to the login screen rather
      // than surfacing "Sign in with Discord" as an error on whatever they clicked.
      window.setTimeout(() => window.location.reload(), 50)
    }
    throw new ApiError(response.status, payload.detail ?? `Request failed (${response.status})`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function mutateApi<T>(
  path: string,
  csrf: string,
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  body?: unknown,
): Promise<T> {
  return api<T>(path, {
    method,
    headers: { 'x-csrf-token': csrf },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export const formatScore = (value: number) =>
  new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(value)

/** Counts, credits and money all go through here. `toLocaleString()` follows the browser's
 *  locale while `formatScore` is pinned to en-US, so the two used to disagree on the same
 *  screen: "4.140 credits" next to "10,068 points". One separator everywhere instead. */
export const formatCount = (value: number) =>
  new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(value)

export const CREDIT_USD = 1 / 100000

export const formatUsd = (credits: number) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(credits * CREDIT_USD)

/** A date without the time, for table columns where the hour only costs width. */
export const formatDay = (value?: string) => {
  if (!value) return 'Never'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const sameYear = date.getFullYear() === new Date().getFullYear()
  return new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric', ...(sameYear ? {} : { year: 'numeric' }) }).format(date)
}

export const formatDate = (value?: string) => {
  if (!value) return 'Never'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const sameYear = date.getFullYear() === new Date().getFullYear()
  return new Intl.DateTimeFormat('en', {
    month: 'short',
    day: 'numeric',
    ...(sameYear ? {} : { year: 'numeric' }),
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}
