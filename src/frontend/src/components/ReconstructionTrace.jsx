import ObservationList from './ObservationList'

const SOURCE_LABELS = {
  measured: 'measured at a sensor sample',
  tracked: 'tracked in video',
  interpolated: 'interpolated between samples',
  extrapolated: 'extrapolated beyond samples',
  estimated: 'estimated (no positional evidence)',
}
const pct = (c) => (c == null ? '—' : `${Math.round(c * 100)}%`)

// Mean confidence and point-source mix of one or more tracks.
export function trackStats(vehicles) {
  const counts = {}
  let sum = 0
  let n = 0
  for (const v of vehicles) {
    for (const p of v.track) {
      counts[p.source ?? 'estimated'] = (counts[p.source ?? 'estimated'] ?? 0) + 1
      if (p.confidence != null) { sum += p.confidence; n += 1 }
    }
  }
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  return { counts, total, mean: n ? sum / n : null }
}

function ConfidenceBar({ value }) {
  const c = value ?? 0
  const color = c >= 0.7 ? 'bg-emerald-500' : c >= 0.4 ? 'bg-amber-500' : 'bg-rose-400'
  return (
    <div className="h-1.5 w-full rounded bg-slate-100" title={`confidence ${pct(value)}`}>
      <div className={`h-1.5 rounded ${color}`} style={{ width: `${c * 100}%` }} />
    </div>
  )
}

function SourceMix({ stats }) {
  return (
    <ul className="space-y-0.5 text-[11px] text-slate-600">
      {Object.entries(stats.counts).map(([src, n]) => (
        <li key={src} className="flex justify-between gap-2">
          <span>{SOURCE_LABELS[src] ?? src}</span>
          <span className="font-mono text-slate-500">{Math.round((n / stats.total) * 100)}%</span>
        </li>
      ))}
    </ul>
  )
}

// What the map is built from, how far to trust it, and the evidence behind whatever is selected.
export default function ReconstructionTrace({ data, time, selected, onSelect, position, moments, observationsById, onSeek, schematic, onFocus }) {
  const all = trackStats(data.vehicles)
  const vehicle = data.vehicles.find((v) => v.vehicle_id === selected)
  const one = vehicle ? trackStats([vehicle]) : null
  const active = moments.filter((m) => m.t <= time + 1e-6).at(-1)

  return (
    <aside className="flex min-w-0 flex-col gap-3 text-xs">
      <section className="rounded border border-slate-200 p-2.5">
        <h3 className="mb-1 font-semibold text-slate-700">How far to trust this map</h3>
        <div className="mb-1 flex items-baseline justify-between">
          <span className="text-slate-500">Mean point confidence</span>
          <span className="font-mono font-semibold">{pct(all.mean)}</span>
        </div>
        <ConfidenceBar value={all.mean} />
        <div className="mt-2"><SourceMix stats={all} /></div>
        <p className="mt-2 text-[11px] leading-snug text-slate-500">
          {schematic
            ? 'Only who moved where relative to whom, and in what order, is supported. Distances, speeds and any impact point are not.'
            : 'Solid line = positions at real samples; dashed = filled in between. A map is a reconstruction of what may have happened, not a legal finding.'}
        </p>
      </section>

      <section className="rounded border border-slate-200 p-2.5">
        <h3 className="mb-1 font-semibold text-slate-700">Trace a vehicle</h3>
        {!vehicle && <p className="text-[11px] text-slate-500">Click a vehicle on the map or in the legend to see the evidence it is built from.</p>}
        {vehicle && (
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1.5 font-medium text-slate-800">
                <span className="inline-block h-2.5 w-3.5 rounded-sm" style={{ background: vehicle.color }} />{vehicle.label}
              </span>
              <button onClick={() => onSelect(null)} className="text-[11px] text-slate-400 hover:text-slate-700">clear</button>
            </div>
            {vehicle.description && <p className="text-slate-500">{vehicle.description}</p>}
            <dl className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-[11px]">
              <dt className="text-slate-500">Seen</dt>
              <dd className="font-mono">t={vehicle.track[0].t_s.toFixed(1)}–{vehicle.track.at(-1).t_s.toFixed(1)}s</dd>
              <dt className="text-slate-500">Now ({time.toFixed(1)}s)</dt>
              <dd className="font-mono">{position ? `${position.source ?? 'estimated'} · ${pct(position.confidence)}` : 'not in view'}</dd>
              <dt className="text-slate-500">Speed now</dt>
              <dd className="font-mono">{position?.speed_kmh != null ? `${position.speed_kmh.toFixed(0)} km/h` : 'not measurable'}</dd>
              <dt className="text-slate-500">Mean confidence</dt>
              <dd className="font-mono">{pct(one.mean)}</dd>
            </dl>
            <ConfidenceBar value={one.mean} />
            <SourceMix stats={one} />
            <div>
              <h4 className="mb-1 font-medium text-slate-600">Supporting evidence</h4>
              {vehicle.source_observation_ids.length
                ? <ObservationList ids={vehicle.source_observation_ids.slice(0, 5)} observationsById={observationsById} />
                : <p className="text-slate-400">No observation ids recorded for this track.</p>}
              {vehicle.source_observation_ids.length > 5 && (
                <p className="mt-1 text-slate-400">+{vehicle.source_observation_ids.length - 5} more in the positions export</p>
              )}
            </div>
          </div>
        )}
      </section>

      <section className="rounded border border-slate-200 p-2.5">
        <h3 className="mb-1 font-semibold text-slate-700">Key moments <span className="font-normal text-slate-400">({moments.length})</span></h3>
        {moments.length === 0 && <p className="text-[11px] text-slate-500">No timeline events or contradictions to jump to.</p>}
        <ul className="max-h-56 space-y-0.5 overflow-y-auto">
          {moments.map((m) => (
            <li key={m.id}>
              <button onClick={() => { onSeek(m.t); onFocus?.(m.t, m.observationIds) }}
                className={`flex w-full items-start gap-1.5 rounded px-1 py-0.5 text-left hover:bg-slate-50 ${active?.id === m.id ? 'bg-yellow-50 ring-1 ring-yellow-300' : ''}`}>
                <span className={`mt-1 inline-block h-2 w-2 shrink-0 rounded-full ${m.kind === 'conflict' ? 'bg-amber-500' : m.kind === 'collision' ? 'bg-red-600' : 'bg-slate-400'}`} />
                <span className="min-w-0">
                  <span className="font-mono text-slate-400">t={m.t.toFixed(1)}s </span>
                  <span className="text-slate-700">{m.label}</span>
                  <span className="ml-1 text-[10px] uppercase text-slate-400">{m.kind === 'conflict' ? 'contradiction' : m.basis ?? ''}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    </aside>
  )
}
