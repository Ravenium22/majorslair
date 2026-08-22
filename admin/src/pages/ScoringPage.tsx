import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, RotateCcw, Save, SlidersHorizontal } from 'lucide-react'
import useSWR from 'swr'
import { api, mutateApi } from '../api'
import { PageHeader, Toast } from '../components'
import type { ConfigEntry, Session } from '../types'

const groups: Record<string, string[]> = {
  'Target accounts': ['primary_handle', 'secondary_handle'],
  'Primary account points': ['reply_primary', 'quote_primary', 'retweet_primary', 'mention_primary'],
  'Secondary account points': ['reply_secondary', 'quote_secondary', 'retweet_secondary', 'mention_secondary'],
  'Quality signals': ['minimum_words', 'low_effort_multiplier', 'word_bonus_8', 'word_bonus_20', 'question_bonus', 'reference_bonus', 'media_bonus', 'link_bonus', 'quality_bonus_cap'],
  'Scan guardrails': ['default_check_period', 'default_refresh_period', 'max_source_pages', 'max_action_pages_per_post', 'max_mention_pages', 'daily_scored_action_cap', 'low_activity_threshold'],
  'Content rules': ['blacklist', 'reference_keywords'],
}

export default function ScoringPage({ session }: { session: Session }) {
  const { data, mutate } = useSWR<ConfigEntry[]>('/api/config', api)
  const [values, setValues] = useState<Record<string, string>>({})
  const [notice, setNotice] = useState<{ text: string; kind: 'success' | 'error' | 'loading' }>()
  const [showReset, setShowReset] = useState(false)
  const [confirmation, setConfirmation] = useState('')
  useEffect(() => { if (data) setValues(Object.fromEntries(data.map((entry) => [entry.key, entry.value]))) }, [data])
  const entries = useMemo(() => new Map(data?.map((entry) => [entry.key, entry])), [data])

  const save = async () => {
    const editable = Object.fromEntries(Object.entries(values).filter(([key]) => key !== 'current_cycle_id' && key !== 'cycle_started_at'))
    setNotice({ text: 'Validating rules and recalculating the current cycle…', kind: 'loading' })
    try {
      const result = await mutateApi<{ rescored_actions: number }>('/api/config', session.csrf_token, 'PUT', { values: editable })
      setNotice({ text: `Rules saved. ${result.rescored_actions} action records rescored.`, kind: 'success' })
      await mutate()
    } catch (error) { setNotice({ text: error instanceof Error ? error.message : 'Save failed', kind: 'error' }) }
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
    <PageHeader eyebrow="Decision engine" title="Scoring rules" copy="Tune point weights, quality bonuses, content filters, and safe scan limits without redeploying the bot." actions={<button className="button primary" onClick={save}><Save size={17} /> Save & rescore</button>} />
    {notice && <Toast message={notice.text} kind={notice.kind} />}
    <div className="callout"><AlertTriangle size={19} /><div><strong>Changes are immediate and audited.</strong><p>Saving recalculates every action in the current cycle. Historical snapshots remain unchanged.</p></div></div>
    <div className="config-layout"><aside className="config-index"><SlidersHorizontal /><strong>Rule groups</strong>{Object.keys(groups).map((group) => <a key={group} href={`#${group.toLowerCase().replaceAll(' ', '-')}`}>{group}</a>)}</aside><div className="config-groups">
      {Object.entries(groups).map(([group, keys]) => <section className="panel config-group" id={group.toLowerCase().replaceAll(' ', '-')} key={group}><div className="panel-head"><div><p className="eyebrow">Configuration</p><h2>{group}</h2></div><span>{keys.length} rules</span></div><div className="field-grid">{keys.map((key) => { const entry = entries.get(key); const long = key === 'blacklist' || key === 'reference_keywords'; return <label className={long ? 'wide' : ''} key={key}><span>{key.replaceAll('_', ' ')}</span>{long ? <textarea value={values[key] ?? ''} onChange={(e) => setValues({ ...values, [key]: e.target.value })} /> : <input value={values[key] ?? ''} onChange={(e) => setValues({ ...values, [key]: e.target.value })} />}<small>{entry?.description}</small></label> })}</div></section>)}
      <section className="danger-zone"><div><p className="eyebrow">Cycle operations</p><h2>Reset the leaderboard</h2><p>Save a ranked historical snapshot, zero current scores, and begin a new scoring cycle.</p></div><button className="button danger" onClick={() => setShowReset(true)}><RotateCcw size={17} /> Reset cycle</button></section>
    </div></div>
    {showReset && <div className="modal-backdrop" onMouseDown={() => setShowReset(false)}><div className="modal" onMouseDown={(e) => e.stopPropagation()}><div className="modal-icon danger-icon"><AlertTriangle /></div><p className="eyebrow">Destructive operation</p><h2>Begin a new leaderboard cycle?</h2><p>Current scores will be zeroed after a permanent snapshot is saved. Type <strong>RESET LEADERBOARD</strong> to continue.</p><label>Confirmation<input autoFocus value={confirmation} onChange={(e) => setConfirmation(e.target.value)} placeholder="RESET LEADERBOARD" /></label><div className="modal-actions"><button className="button ghost" onClick={() => setShowReset(false)}>Cancel</button><button className="button danger" disabled={confirmation !== 'RESET LEADERBOARD'} onClick={reset}>Reset leaderboard</button></div></div></div>}
  </div>
}
