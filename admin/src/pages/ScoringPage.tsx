import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, RotateCcw, Save, SlidersHorizontal } from 'lucide-react'
import useSWR from 'swr'
import { api, mutateApi } from '../api'
import { PageHeader, Toast, useEscape } from '../components'
import type { ConfigEntry, Session } from '../types'

const BOOLEAN_KEYS = new Set(['skip_protected_members'])
const NUMERIC_KEYS = new Set(['max_source_pages', 'max_action_pages_per_post', 'max_mention_pages', 'max_reply_search_pages', 'member_timeline_pages', 'daily_scored_action_cap', 'low_activity_threshold', 'newcomer_grace_days', 'reply_primary', 'quote_primary', 'retweet_primary', 'mention_primary', 'reply_secondary', 'quote_secondary', 'retweet_secondary', 'mention_secondary', 'minimum_words', 'low_effort_multiplier', 'word_bonus_8', 'word_bonus_20', 'question_bonus', 'reference_bonus', 'media_bonus', 'link_bonus', 'quality_bonus_cap'])
const groups: Record<string, string[]> = {
  'Target accounts': ['primary_handle', 'secondary_handle'],
  'Primary account points': ['reply_primary', 'quote_primary', 'retweet_primary', 'mention_primary'],
  'Secondary account points': ['reply_secondary', 'quote_secondary', 'retweet_secondary', 'mention_secondary'],
  'Quality signals': ['minimum_words', 'low_effort_multiplier', 'word_bonus_8', 'word_bonus_20', 'question_bonus', 'reference_bonus', 'media_bonus', 'link_bonus', 'quality_bonus_cap'],
  'Scan guardrails': ['default_check_period', 'default_refresh_period', 'max_source_pages', 'max_action_pages_per_post', 'max_mention_pages', 'max_reply_search_pages', 'member_timeline_pages', 'daily_scored_action_cap', 'low_activity_threshold', 'newcomer_grace_days', 'skip_protected_members', 'protected_role_names'],
  'Content rules': ['blacklist', 'reference_keywords'],
}

export default function ScoringPage({ session }: { session: Session }) {
  const { data, mutate } = useSWR<ConfigEntry[]>('/api/config', api)
  const [values, setValues] = useState<Record<string, string>>({})
  const [notice, setNotice] = useState<{ text: string; kind: 'success' | 'error' | 'loading' }>()
  const [showReset, setShowReset] = useState(false)
  const [showSave, setShowSave] = useState(false)
  const [saving, setSaving] = useState(false)
  const [confirmation, setConfirmation] = useState('')
  useEscape(showReset, () => setShowReset(false))
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
      const result = await mutateApi<{ rescored_actions: number }>('/api/config', session.csrf_token, 'PUT', { values: editable })
      setNotice({ text: `Rules saved. ${result.rescored_actions} action records rescored.`, kind: 'success' })
      setShowSave(false)
      await mutate()
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Save failed', kind: 'error' }) }
    finally { setSaving(false) }
  }

  const reset = async () => {
    setNotice({ text: 'Snapshotting scores and starting a new cycle…', kind: 'loading' })
    try {
      const result = await mutateApi<{ snapshots: number }>('/api/reset', session.csrf_token, 'POST', { confirmation })
      setNotice({ text: `New cycle started. ${result.snapshots} member snapshots preserved.`, kind: 'success' })
      setShowReset(false)
      setConfirmation('')
      await mutate()
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Reset failed', kind: 'error' }) }
  }

  return <div className="page">
    <PageHeader eyebrow="Decision engine" title="Scoring rules" copy="Tune point weights, quality bonuses, content filters, and safe scan limits without redeploying the bot." actions={<button className="button primary" onClick={() => setShowSave(true)} disabled={!changes.length} title={changes.length ? undefined : 'Nothing changed yet'}><Save size={17} /> {changes.length ? `Review ${changes.length} change${changes.length === 1 ? '' : 's'}` : 'No changes'}</button>} />
    {notice && <Toast message={notice.text} kind={notice.kind} />}
    <div className="callout"><AlertTriangle size={19} /><div><strong>Changes are immediate and audited.</strong><p>Saving recalculates every action in the current cycle. Historical snapshots remain unchanged.</p></div></div>
    <div className="config-layout"><aside className="config-index"><SlidersHorizontal /><strong>Rule groups</strong>{Object.keys(groups).map((group) => <a key={group} href={`#${group.toLowerCase().replaceAll(' ', '-')}`}>{group}</a>)}</aside><div className="config-groups">
      {Object.entries(groups).map(([group, keys]) => <section className="panel config-group" id={group.toLowerCase().replaceAll(' ', '-')} key={group}><div className="panel-head"><div><p className="eyebrow">Configuration</p><h2>{group}</h2></div><span>{keys.length} rules</span></div><div className="field-grid">{keys.map((key) => { const entry = entries.get(key); const long = key === 'blacklist' || key === 'reference_keywords'; return <label className={long ? 'wide' : ''} key={key}><span>{key.replaceAll('_', ' ')}</span>{long ? <textarea value={values[key] ?? ''} onChange={(e) => setValues({ ...values, [key]: e.target.value })} /> : BOOLEAN_KEYS.has(key) ? <select value={(values[key] ?? 'false').toLowerCase() === 'true' ? 'true' : 'false'} onChange={(e) => setValues({ ...values, [key]: e.target.value })}><option value="false">Off</option><option value="true">On</option></select> : <input value={values[key] ?? ''} onChange={(e) => setValues({ ...values, [key]: e.target.value })} inputMode={NUMERIC_KEYS.has(key) ? 'decimal' : undefined} />}<small>{entry?.description}</small></label> })}</div></section>)}
      <section className="danger-zone"><div><p className="eyebrow">Cycle operations</p><h2>Reset the leaderboard</h2><p>Save a ranked historical snapshot, zero current scores, and begin a new scoring cycle.</p></div><button className="button danger" onClick={() => setShowReset(true)}><RotateCcw size={17} /> Reset cycle</button></section>
    </div></div>
    {showSave && <div className="modal-backdrop" onMouseDown={() => { if (!saving) setShowSave(false) }}><div className="modal modal-wide" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal-icon"><Save /></div><h2>Save these {changes.length} change{changes.length === 1 ? '' : 's'}?</h2>
      <p>Every action in the current cycle is scored again with the new rules, so points can move up or down straight away. Past snapshots and scan reports are untouched.</p>
      <div className="save-diff"><table><tbody>{changes.map((c) => <tr key={c.key}><td><strong>{c.key.replaceAll('_', ' ')}</strong></td><td className="from">{c.from || '(empty)'}</td><td className="to">{c.to || '(empty)'}</td></tr>)}</tbody></table></div>
      <div className="modal-actions"><button className="button ghost" onClick={() => setShowSave(false)} disabled={saving}>Keep editing</button><button className="button primary" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save and rescore'}</button></div>
    </div></div>}

    {showReset && <div className="modal-backdrop" onMouseDown={() => setShowReset(false)}><div className="modal" onMouseDown={(e) => e.stopPropagation()}><div className="modal-icon danger-icon"><AlertTriangle /></div><p className="eyebrow">Destructive operation</p><h2>Begin a new leaderboard cycle?</h2><p>Current scores will be zeroed after a permanent snapshot is saved. Type <strong>RESET LEADERBOARD</strong> to continue.</p><label>Confirmation<input autoFocus value={confirmation} onChange={(e) => setConfirmation(e.target.value)} placeholder="RESET LEADERBOARD" /></label><div className="modal-actions"><button className="button ghost" onClick={() => setShowReset(false)}>Cancel</button><button className="button danger" disabled={confirmation !== 'RESET LEADERBOARD'} onClick={reset}>Reset leaderboard</button></div></div></div>}
  </div>
}
