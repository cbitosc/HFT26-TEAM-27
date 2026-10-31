import { useState } from 'react'
import ObservationList from './ObservationList'
import Panel from './Panel'

const SEVERITY_STYLES = {
  high: 'border-l-red-500',
  medium: 'border-l-amber-500',
  low: 'border-l-slate-300',
}
const TYPES = ['all', 'numeric_mismatch', 'semantic', 'timestamp_overlap', 'missing_evidence']

export default function ContradictionExplorer({ report, observationsById, onFocus }) {
  const [filter, setFilter] = useState('all')
  const [open, setOpen] = useState(null)
  if (!report) return <Panel title="Contradiction Explorer">Loading…</Panel>

  const conflicts = report.conflicts.filter((c) => filter === 'all' || c.type === filter)

  const toggle = (c) => {
    setOpen(open === c.id ? null : c.id)
    onFocus(c.t_range_s?.[0] ?? null, c.observation_ids)
  }

  return (
    <Panel title={`Contradiction Explorer (${report.conflicts.length})`}
      actions={
        <select value={filter} onChange={(e) => setFilter(e.target.value)} className="rounded border border-slate-200 text-xs">
          {TYPES.map((t) => <option key={t} value={t}>{t.replace('_', ' ')}</option>)}
        </select>
      }>
      {report.conflicts.length > 0 && (
        <p className="mb-3 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <b>{report.conflicts.length} unresolved.</b> Nothing here is settled automatically: each item needs a person to
          weigh the sources. High severity first.
        </p>
      )}
      {conflicts.length === 0 && <p className="text-xs text-slate-400">No contradictions between the sources.</p>}
      <ul className="space-y-2">
        {[...conflicts].sort((a, b) => ['high', 'medium', 'low'].indexOf(a.severity) - ['high', 'medium', 'low'].indexOf(b.severity)).map((c) => (
          <li key={c.id} className={`rounded border border-l-4 border-slate-200 ${SEVERITY_STYLES[c.severity]}`}>
            <button onClick={() => toggle(c)} className="w-full p-2 text-left">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium"><span className="mr-1.5 rounded bg-amber-100 px-1.5 py-0.5 text-[0.7rem] font-medium text-amber-800">unresolved</span>{c.title}</span>
                <span className="shrink-0 text-[0.7rem] uppercase text-slate-400">
                  {c.type.replace('_', ' ')}{c.basis === 'llm' ? ' · AI-detected' : ' · rule'}
                </span>
              </div>
              <p className="mt-1 text-xs text-slate-600">{c.description}</p>
            </button>
            {open === c.id && (
              <div className="border-t border-slate-100 p-2">
                <ObservationList ids={c.observation_ids} observationsById={observationsById} />
              </div>
            )}
          </li>
        ))}
      </ul>
    </Panel>
  )
}
