import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from './api'
import ContradictionExplorer from './components/ContradictionExplorer'
import EvidenceManager from './components/EvidenceManager'
import IncidentTimeline from './components/IncidentTimeline'
import ReconstructionViewer from './components/ReconstructionViewer'

export default function App() {
  const [results, setResults] = useState(null)
  const [run, setRun] = useState(null)
  const [error, setError] = useState(null)
  const [time, setTime] = useState(0)
  const [selectedObs, setSelectedObs] = useState([])

  const loadResults = useCallback(async () => {
    try {
      const [observations, timeline, conflicts, reconstruction, latest] = await Promise.all([
        api.observations(), api.timeline(), api.conflicts(), api.reconstruction(), api.latestRun(),
      ])
      setResults({ observations, timeline, conflicts, reconstruction })
      setTime((t) => Math.min(Math.max(t, reconstruction.t_min_s), reconstruction.t_max_s))
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

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
          <div>
            <h1 className="text-lg font-semibold">Incident Reconstruction</h1>
            <p className="text-xs text-slate-500">
              {results?.timeline.incident_id ?? '—'} · reference time {results?.timeline.reference_time ?? '—'}
            </p>
          </div>
          {run && <span className="text-xs text-slate-500">last run {run.run_id}</span>}
        </div>
      </header>

      <main className="mx-auto grid max-w-7xl gap-4 px-4 py-4 *:min-w-0 lg:grid-cols-3">
        {error && <div className="rounded-md bg-red-50 p-3 text-sm text-red-700 lg:col-span-3">{error}</div>}
        <div className="lg:col-span-1">
          <EvidenceManager run={run} observations={results?.observations} onAnalysisDone={loadResults} />
        </div>
        <div className="lg:col-span-2">
          <ReconstructionViewer data={results?.reconstruction} time={time} onTimeChange={setTime} />
        </div>
        <div className="lg:col-span-2">
          <IncidentTimeline timeline={results?.timeline} time={time} selectedObs={selectedObs} onFocus={focus}
            observationsById={observationsById} />
        </div>
        <div className="lg:col-span-1">
          <ContradictionExplorer report={results?.conflicts} observationsById={observationsById} onFocus={focus} />
        </div>
      </main>
    </div>
  )
}
