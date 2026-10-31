import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from './api'
import ContradictionExplorer from './components/ContradictionExplorer'
import EventAnalysis from './components/EventAnalysis'
import EvidenceManager from './components/EvidenceManager'
import IncidentTimeline from './components/IncidentTimeline'
import Overview from './components/Overview'
import ReconstructionViewer from './components/ReconstructionViewer'

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'evidence', label: 'Evidence' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'contradictions', label: 'Contradictions' },
  { id: 'reconstruction', label: 'Reconstruction' },
  { id: 'footage', label: 'Footage' },
]
const tabFromHash = () => {
  const id = window.location.hash.slice(1)
  return TABS.some((t) => t.id === id) ? id : 'overview'
}

export default function App() {
  const [results, setResults] = useState(null)
  const [run, setRun] = useState(null)
  const [error, setError] = useState(null)
  const [time, setTime] = useState(0)
  const [selectedObs, setSelectedObs] = useState([])
  const [tab, setTab] = useState(tabFromHash)
  const [analysing, setAnalysing] = useState(false)

  const open = useCallback((id) => { window.location.hash = id }, [])
  useEffect(() => {
    const onHash = () => setTab(tabFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const loadResults = useCallback(async () => {
    try {
      const [observations, timeline, conflicts, reconstruction, eventAnalysis, latest] = await Promise.all([
        api.observations(), api.timeline(), api.conflicts(), api.reconstruction(), api.eventAnalysis(), api.latestRun(),
      ])
      setResults({ observations, timeline, conflicts, reconstruction, eventAnalysis })
      // One clock for every panel: clamp to whatever the views cover.
      const spans = [reconstruction, eventAnalysis].filter((r) => r && r.t_max_s > r.t_min_s)
      if (spans.length) {
        const lo = Math.min(...spans.map((r) => r.t_min_s))
        const hi = Math.max(...spans.map((r) => r.t_max_s))
        setTime((t) => Math.min(Math.max(t, lo), hi))
      }
      setRun(latest)
      setError(null)
    } catch (e) {
      setError(`Could not reach the API: ${e.message}. Is the backend running on :8000?`)
    }
  }, [])

  useEffect(() => { loadResults() }, [loadResults])

  const observationsById = useMemo(() => {
    const map = {}
    for (const set of results?.observations ?? []) for (const o of set.observations) map[o.id] = o
    return map
  }, [results])

  const focus = (t, obsIds = []) => {
    if (t != null) setTime(t)
    setSelectedObs(obsIds)
  }

  const counts = {
    evidence: results?.observations.length,
    timeline: results?.timeline.events.length,
    contradictions: results?.conflicts.conflicts.length,
    reconstruction: results?.reconstruction?.vehicles?.length,
    footage: results?.eventAnalysis ? '●' : null,
  }
  const high = results?.conflicts.conflicts.filter((c) => c.severity === 'high').length ?? 0
  const confirmed = results?.timeline && run
  const schematic = results?.reconstruction?.scene?.units === 'schematic'

  // Every tab stays mounted (hidden when inactive) so a running analysis and replay state survive tab changes.
  const pane = (id, node) => <div key={id} hidden={tab !== id}>{node}</div>

  return (
    <div className="min-h-screen">
      <header className="bg-slate-900 text-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-2 px-4 py-3">
          <div className="min-w-0">
            <h1 className="text-lg font-semibold leading-tight">Incident Reconstruction</h1>
            <p className="truncate text-xs text-slate-400">
              {results?.timeline.incident_id ?? '—'} · reference time {results?.timeline.reference_time ?? '—'}
            </p>
          </div>
          <div className="flex items-center gap-3 text-xs text-slate-300">
            {analysing && <span className="flex items-center gap-1.5 rounded-full bg-sky-500/20 px-2.5 py-1 text-sky-200">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-sky-300" />Analysing…</span>}
            {run && <span>last run {run.run_id}</span>}
          </div>
        </div>
        <nav className="mx-auto flex max-w-7xl gap-1 overflow-x-auto px-3" role="tablist" aria-label="Dashboard sections">
          {TABS.map((t) => {
            const active = tab === t.id
            const n = counts[t.id]
            return (
              <button key={t.id} role="tab" aria-selected={active} onClick={() => open(t.id)}
                className={`flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2 text-sm transition-colors ${active ? 'border-sky-400 text-white' : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
                {t.label}
                {n != null && n !== 0 && (
                  <span className={`rounded-full px-1.5 text-[10px] font-medium leading-4 ${t.id === 'contradictions' && high ? 'bg-red-500 text-white' : 'bg-slate-700 text-slate-200'}`}>{n}</span>
                )}
              </button>
            )
          })}
        </nav>
      </header>

      <main className="mx-auto max-w-7xl space-y-4 px-4 py-4 *:min-w-0">
        {error && <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</div>}
        {pane('overview', <Overview results={results} run={run} onOpen={open} />)}
        {pane('evidence', <div className="mx-auto max-w-2xl"><EvidenceManager run={run} observations={results?.observations}
          onAnalysisDone={loadResults} onRunningChange={setAnalysing} /></div>)}
        {pane('timeline', <IncidentTimeline timeline={results?.timeline} time={time} selectedObs={selectedObs} onFocus={focus}
          observationsById={observationsById} />)}
        {pane('contradictions', <ContradictionExplorer report={results?.conflicts} observationsById={observationsById} onFocus={focus} />)}
        {pane('reconstruction', <ReconstructionViewer data={results?.reconstruction} time={time} onTimeChange={setTime}
          timeline={results?.timeline} conflicts={results?.conflicts} observationsById={observationsById} onFocus={focus} />)}
        {pane('footage', <EventAnalysis data={results?.eventAnalysis} time={time} onTimeChange={setTime} />)}
      </main>
      <footer className="mx-auto max-w-7xl px-4 pb-6 text-[11px] text-slate-400">
        {confirmed && schematic && 'Reconstruction is a schematic estimate. '}Observations are not legal conclusions.
      </footer>
    </div>
  )
}
