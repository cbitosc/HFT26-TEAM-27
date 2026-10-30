import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import Panel, { SourceBadge } from './Panel'

const STATUS_STYLES = {
  real: 'bg-emerald-100 text-emerald-800',
  fixture: 'bg-slate-100 text-slate-600',
  skipped: 'bg-amber-100 text-amber-800',
  error: 'bg-red-100 text-red-700',
}

export default function EvidenceManager({ run, onAnalysisDone }) {
  const [evidence, setEvidence] = useState([])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null)
  const inputRef = useRef(null)

  const refresh = () => api.listEvidence().then(setEvidence).catch(() => {})
  useEffect(() => { refresh() }, [])

  const upload = async (files) => {
    setBusy(true)
    setMessage(null)
    try {
      for (const f of files) await api.uploadEvidence(f)
    } catch (e) {
      setMessage(e.message)
    }
    if (inputRef.current) inputRef.current.value = ''
    await refresh()
    setBusy(false)
  }

  const remove = async (id) => {
    await api.deleteEvidence(id)
    refresh()
  }

  const analyze = async () => {
    setBusy(true)
    setMessage(null)
    try {
      await api.runAnalysis()
      await onAnalysisDone()
    } catch (e) {
      setMessage(e.message)
    }
    setBusy(false)
  }

  return (
    <Panel title="Evidence Manager">
      <label className="block cursor-pointer rounded-md border-2 border-dashed border-slate-300 p-4 text-center text-sm text-slate-500 hover:border-slate-400">
        <input ref={inputRef} type="file" multiple accept=".mp4,.mov,.avi,.mkv,.txt,.csv" className="hidden"
          disabled={busy} onChange={(e) => upload([...e.target.files])} />
        Drop or choose files — dashcam video, witness .txt, sensor .csv
      </label>

      <ul className="mt-3 space-y-1.5">
        {evidence.length === 0
          ? <li className="text-xs text-slate-400">No evidence uploaded — analysis runs on demo fixtures.</li>
          : <li className="text-xs text-slate-400">Results use only your evidence; stages that can't run are skipped.</li>}
        {evidence.map((e) => (
          <li key={e.id} className="flex items-center gap-2 text-sm">
            <SourceBadge type={e.kind} />
            <span className="flex-1 truncate" title={e.filename}>{e.filename}</span>
            <span className="text-xs text-slate-400">{(e.size_bytes / 1024).toFixed(1)} KB</span>
            <button onClick={() => remove(e.id)} className="text-xs text-slate-400 hover:text-red-600">remove</button>
          </li>
        ))}
      </ul>

      <button onClick={analyze} disabled={busy}
        className="mt-4 w-full rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50">
        {busy ? 'Working…' : 'Run analysis'}
      </button>
      {message && <p className="mt-2 text-xs text-red-600">{message}</p>}

      {run && (
        <div className="mt-4">
          <h3 className="mb-1 text-xs font-semibold uppercase text-slate-500">Pipeline stages</h3>
          <ul className="space-y-1">
            {run.stages.map((s, i) => (
              <li key={i} className="flex items-center gap-2 text-xs" title={s.message}>
                <span className={`w-14 rounded px-1.5 py-0.5 text-center font-medium ${STATUS_STYLES[s.status]}`}>{s.status}</span>
                <span className="truncate">{s.stage}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  )
}
