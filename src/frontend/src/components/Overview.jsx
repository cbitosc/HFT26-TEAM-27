import Panel, { SourceBadge } from './Panel'
import { trackStats } from './ReconstructionTrace'

const SEVERITY_DOT = { high: 'bg-red-500', medium: 'bg-amber-500', low: 'bg-slate-300' }

function Stat({ label, value, sub, tone = 'text-slate-900' }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-1 text-3xl font-semibold ${tone}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-500">{sub}</div>}
    </div>
  )
}

// One-screen summary of everything the pipeline produced, with a way into each tab.
export default function Overview({ results, run, onOpen }) {
  if (!results) return <Panel title="Overview">Loading…</Panel>
  const { observations, timeline, conflicts, reconstruction, eventAnalysis } = results

  const bySource = {}
  for (const set of observations) bySource[set.source_type] = (bySource[set.source_type] ?? 0) + set.observations.length
  const nObs = Object.values(bySource).reduce((a, b) => a + b, 0)
  const sev = { high: 0, medium: 0, low: 0 }
  for (const c of conflicts.conflicts) sev[c.severity] += 1
  const top = [...conflicts.conflicts].sort((a, b) => ['high', 'medium', 'low'].indexOf(a.severity) - ['high', 'medium', 'low'].indexOf(b.severity)).slice(0, 4)
  const events = [...timeline.events].sort((a, b) => a.t_start_s - b.t_start_s)
  const recon = reconstruction?.vehicles?.length ? reconstruction : null
  const stats = recon ? trackStats(recon.vehicles) : null
  const kind = !recon ? 'none' : recon.scene.units === 'schematic' ? 'schematic' : 'metric'
  const stages = run?.stages ?? []

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-900">
        This is a reconstruction of what <b>may</b> have happened, built from the evidence supplied. Every item is labelled as
        observed, reported, derived or AI-inferred. It is decision support, not a legal finding.
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Observations" value={nObs}
          sub={Object.entries(bySource).map(([k, n]) => `${n} ${k}`).join(' · ') || 'no evidence yet'} />
        <Stat label="Timeline events" value={events.length}
          sub={events.length ? `t=${events[0].t_start_s}s to ${events.at(-1).t_end_s}s` : 'nothing placed in time'} />
        <Stat label="Contradictions" value={conflicts.conflicts.length}
          tone={sev.high ? 'text-red-600' : 'text-slate-900'}
          sub={`${sev.high} high · ${sev.medium} medium · ${sev.low} low`} />
        <Stat label="Reconstruction" value={kind === 'none' ? '—' : stats.mean != null ? `${Math.round(stats.mean * 100)}%` : '—'}
          tone={kind === 'schematic' ? 'text-amber-600' : 'text-slate-900'}
          sub={kind === 'none' ? 'no positional evidence' : `${recon.vehicles.length} tracks · ${kind === 'schematic' ? 'schematic, not to scale' : 'metric map'} · mean confidence`} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Pipeline" actions={<button onClick={() => onOpen('evidence')} className="text-xs text-sky-700 hover:underline">Evidence →</button>}>
          {stages.length === 0 && <p className="text-xs text-slate-400">No run yet. Add evidence and run the analysis.</p>}
          <ol className="space-y-1.5">
            {stages.map((s) => (
              <li key={s.stage} className="flex items-center gap-2 text-xs" title={s.message}>
                <span className={`h-2 w-2 shrink-0 rounded-full ${s.status === 'real' ? 'bg-emerald-500' : s.status === 'error' ? 'bg-red-500' : 'bg-slate-300'}`} />
                <span className="min-w-0 flex-1 truncate">{s.stage}</span>
                <span className="shrink-0 text-slate-400">{s.status}</span>
              </li>
            ))}
          </ol>
        </Panel>

        <Panel title="Most important contradictions" actions={<button onClick={() => onOpen('contradictions')} className="text-xs text-sky-700 hover:underline">All →</button>}>
          {top.length === 0 && <p className="text-xs text-slate-400">None found between the supplied sources. With a single source there is nothing to compare.</p>}
          <ul className="space-y-2">
            {top.map((c) => (
              <li key={c.id} className="flex gap-2 text-xs">
                <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${SEVERITY_DOT[c.severity]}`} />
                <span><span className="font-medium">{c.title}</span><span className="block text-slate-500">{c.description}</span></span>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel title="Timeline at a glance" actions={<button onClick={() => onOpen('timeline')} className="text-xs text-sky-700 hover:underline">Open →</button>}>
          {events.length === 0 && <p className="text-xs text-slate-400">No events yet.</p>}
          <ul className="space-y-1.5">
            {events.slice(0, 6).map((e) => (
              <li key={e.id} className="flex items-start gap-2 text-xs">
                <span className="w-14 shrink-0 font-mono text-slate-400">t={e.t_start_s}s</span>
                <span className="min-w-0 flex-1">{e.label}</span>
                <span className="flex shrink-0 gap-1">{e.source_types.map((s) => <SourceBadge key={s} type={s} />)}</span>
              </li>
            ))}
          </ul>
          {events.length > 6 && <p className="mt-2 text-[0.75rem] text-slate-400">+{events.length - 6} more</p>}
        </Panel>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <button onClick={() => onOpen('reconstruction')} className="rounded-lg border border-slate-200 bg-white p-4 text-left shadow-sm hover:border-slate-400">
          <div className="text-sm font-semibold">Reconstruction →</div>
          <p className="mt-1 text-xs text-slate-500">{kind === 'none' ? (reconstruction?.note ?? 'Needs positional evidence.') : 'Replay vehicle movements, trace each one to its evidence, see confidence.'}</p>
        </button>
        <button onClick={() => onOpen('footage')} className="rounded-lg border border-slate-200 bg-white p-4 text-left shadow-sm hover:border-slate-400">
          <div className="text-sm font-semibold">Footage analysis →</div>
          <p className="mt-1 text-xs text-slate-500">{eventAnalysis ? 'Detected vehicles and tracks drawn over the actual video frames.' : 'No video analysed yet.'}</p>
        </button>
      </div>
    </div>
  )
}
