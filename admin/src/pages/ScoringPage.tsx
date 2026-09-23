import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Save, SlidersHorizontal } from 'lucide-react'
import useSWR from 'swr'
import { api, formatCount, mutateApi } from '../api'
import { PageHeader, Toast, useEscape } from '../components'
import type { ConfigEntry, Session } from '../types'

const BOOLEAN_KEYS = new Set(['skip_protected_members'])
const NUMERIC_KEYS = new Set(['max_source_pages', 'max_action_pages_per_post', 'max_mention_pages', 'max_reply_search_pages', 'member_timeline_pages', 'daily_scored_action_cap', 'low_activity_threshold', 'newcomer_grace_days', 'reply_primary', 'quote_primary', 'retweet_primary', 'mention_primary', 'reply_secondary', 'quote_secondary', 'retweet_secondary', 'mention_secondary', 'minimum_words', 'low_effort_multiplier', 'word_bonus_8', 'word_bonus_20', 'question_bonus', 'reference_bonus', 'media_bonus', 'link_bonus', 'quality_bonus_cap'])
const groups: Record<string, string[]> = {
  'Target accounts': ['primary_handle', 'secondary_handle'],
  'Primary account points': ['reply_primary', 'quote_primary', 'retweet_primary', 'mention_primary'],
  'Secondary account points': ['reply_secondary', 'quote_secondary', 'retweet_secondary', 'mention_secondary'],
  'Quality signals': ['minimum_words', 'low_effort_multiplier', 'word_bonus_8', 'word_bonus_20', 'question_bonus', 'reference_bonus', 'media_bonus', 'link_bonus', 'quality_bonus_cap'],
  'Who counts as inactive': ['low_activity_threshold', 'newcomer_grace_days', 'protected_role_names', 'skip_protected_members'],
  'Members the sync ignores': ['sync_ignored_discord_ids'],
  'Scan limits': ['default_check_period', 'default_refresh_period', 'daily_scored_action_cap', 'max_source_pages', 'max_action_pages_per_post', 'max_mention_pages', 'max_reply_search_pages', 'member_timeline_pages'],
  'Content rules': ['blacklist', 'reference_keywords'],
}

export default function ScoringPage({ session }: { session: Session }) {
  const { data, mutate } = useSWR<ConfigEntry[]>('/api/config', api)
  const [values, setValues] = useState<Record<string, string>>({})
  const [notice, setNotice] = useState<{ text: string; kind: 'success' | 'error' | 'loading' }>()
  const [showSave, setShowSave] = useState(false)
  const [saving, setSaving] = useState(false)
  useEscape(showSave && !saving, () => setShowSave(false))
  useEffect(() => { if (data) setValues(Object.fromEntries(data.map((entry) => [entry.key, entry.value]))) }, [data])
  const entries = useMemo(() => new Map(data?.map((entry) => [entry.key, entry])), [data])

  const changes = useMemo(() => (data ?? [])
    .filter((entry) => entry.key !== 'current_cycle_id' && entry.key !== 'cycle_started_at')
    .filter((entry) => values[entry.key] !== undefined && values[entry.key] !== entry.value)
    .map((entry) => ({ key: entry.key, from: entry.value, to: values[entry.key] })), [data, values])

  const save = async () => {
    const editable = Object.fromEntries(Object.entries(values).filter(([key]) => key !== 'current_cycle_id' && key !== 'cycle_started_at'))
    setSaving(true)
    setNotice({ text: 'Validating rules and recalculating the current cycle…', kind: 'loading' })
    try {
      const result = await mutateApi<{ rescored_actions: number; changed: number }>('/api/config', session.csrf_token, 'PUT', { values: editable })
      setNotice({
        text: result.changed === 0
          ? 'Nothing to save: no rule is different from what is already stored.'
          : `${result.changed} rule${result.changed === 1 ? '' : 's'} saved. ${formatCount(result.rescored_actions)} action records rescored.`,
        kind: 'success',
      })
      setShowSave(false)
      await mutate()
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Save failed', kind: 'error' }) }
    finally { setSaving(false) }
  }


  return <div className="page">
    <PageHeader title="Scoring rules" copy="Tune point weights, quality bonuses, content filters, and safe scan limits without redeploying the bot." actions={<button className="button primary" onClick={() => setShowSave(true)} disabled={!changes.length} title={changes.length ? undefined : 'Nothing changed yet'}><Save size={17} /> {changes.length ? `Review ${changes.length} change${changes.length === 1 ? '' : 's'}` : 'No changes'}</button>} />
    {notice && <Toast message={notice.text} kind={notice.kind} />}
    <div className="callout"><AlertTriangle size={19} /><div><strong>Changes are immediate and audited.</strong><p>Saving recalculates every action in the current cycle. Historical snapshots remain unchanged.</p></div></div>
    <div className="config-layout"><aside className="config-index"><SlidersHorizontal /><strong>Rule groups</strong>{Object.keys(groups).map((group) => <a key={group} href={`#${group.toLowerCase().replaceAll(' ', '-')}`}>{group}</a>)}</aside><div className="config-groups">
      {Object.entries(groups).map(([group, keys]) => <section className="panel config-group" id={group.toLowerCase().replaceAll(' ', '-')} key={group}><div className="panel-head"><div><h2>{group}</h2></div><span>{keys.length} rules</span></div><div className="field-grid">{keys.map((key) => { const entry = entries.get(key); const long = key === 'blacklist' || key === 'reference_keywords'; return <label className={long ? 'wide' : ''} key={key}><span>{key.replaceAll('_', ' ')}</span>{long ? <textarea value={values[key] ?? ''} onChange={(e) => setValues({ ...values, [key]: e.target.value })} /> : BOOLEAN_KEYS.has(key) ? <select value={(values[key] ?? 'false').toLowerCase() === 'true' ? 'true' : 'false'} onChange={(e) => setValues({ ...values, [key]: e.target.value })}><option value="false">Off</option><option value="true">On</option></select> : <input value={values[key] ?? ''} onChange={(e) => setValues({ ...values, [key]: e.target.value })} inputMode={NUMERIC_KEYS.has(key) ? 'decimal' : undefined} />}<small>{entry?.description}</small></label> })}</div></section>)}
      <section className="danger-zone"><div><h2>Starting a new cycle</h2><p>Resetting the leaderboard now lives with the rest of the monthly round, on the Overview page.</p></div><a className="button" href="#overview">Go to Overview</a></section>
    </div></div>
    {showSave && <div className="modal-backdrop" onMouseDown={() => { if (!saving) setShowSave(false) }}><div className="modal modal-wide" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal-icon"><Save /></div><h2>Save these {changes.length} change{changes.length === 1 ? '' : 's'}?</h2>
      <p>Every action in the current cycle is scored again with the new rules, so points can move up or down straight away. Past snapshots and scan reports are untouched.</p>
      <div className="save-diff"><table><tbody>{changes.map((c) => <tr key={c.key}><td><strong>{c.key.replaceAll('_', ' ')}</strong></td><td className="from">{c.from || '(empty)'}</td><td className="to">{c.to || '(empty)'}</td></tr>)}</tbody></table></div>
      <div className="modal-actions"><button className="button ghost" onClick={() => setShowSave(false)} disabled={saving}>Keep editing</button><button className="button primary" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save and rescore'}</button></div>
    </div></div>}

  </div>
}
