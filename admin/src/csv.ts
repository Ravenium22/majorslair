export type ImportRow = { discord_user_id: string; discord_username: string; twitter_handle: string }

const ID_HEADERS = ['discord_id', 'discord_user_id', 'discord id', 'user_id', 'id']
const NAME_HEADERS = ['discord_username', 'discord_name', 'username', 'discord']
const HANDLE_HEADERS = ['x_handle', 'twitter_handle', 'twitter', 'x', 'handle']

/** Minimal RFC 4180 parser: quoted fields, doubled quotes, CRLF or LF line endings. */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = []
  let row: string[] = []
  let field = ''
  let quoted = false
  const source = text.replace(/^﻿/, '')
  for (let i = 0; i < source.length; i += 1) {
    const char = source[i]
    if (quoted) {
      if (char === '"') {
        if (source[i + 1] === '"') { field += '"'; i += 1 } else quoted = false
      } else field += char
    } else if (char === '"') quoted = true
    else if (char === ',') { row.push(field); field = '' }
    else if (char === '\n' || char === '\r') {
      if (char === '\r' && source[i + 1] === '\n') i += 1
      row.push(field); field = ''
      if (row.some((cell) => cell.trim())) rows.push(row)
      row = []
    } else field += char
  }
  row.push(field)
  if (row.some((cell) => cell.trim())) rows.push(row)
  return rows
}

const findColumn = (headers: string[], candidates: string[]) =>
  headers.findIndex((header) => candidates.includes(header.trim().toLowerCase()))

/** Map a parsed sheet onto import rows. Throws a readable message when the headers do not fit. */
export function rowsFromSheet(rows: string[][]): ImportRow[] {
  if (!rows.length) throw new Error('The file is empty')
  const headers = rows[0]
  const idIndex = findColumn(headers, ID_HEADERS)
  const nameIndex = findColumn(headers, NAME_HEADERS)
  const handleIndex = findColumn(headers, HANDLE_HEADERS)
  if (idIndex < 0 || handleIndex < 0) {
    throw new Error('Need a discord_id column and an x_handle column in the first row')
  }
  const result: ImportRow[] = []
  for (const cells of rows.slice(1)) {
    const discord_user_id = (cells[idIndex] ?? '').trim()
    if (!/^\d{5,32}$/.test(discord_user_id)) continue
    const discord_username = (nameIndex >= 0 ? cells[nameIndex] ?? '' : '').trim() || discord_user_id
    result.push({
      discord_user_id,
      discord_username: discord_username.slice(0, 120),
      twitter_handle: (cells[handleIndex] ?? '').trim().replace(/^@/, '').slice(0, 64),
    })
  }
  if (!result.length) throw new Error('No rows with a numeric Discord ID were found')
  return result
}
