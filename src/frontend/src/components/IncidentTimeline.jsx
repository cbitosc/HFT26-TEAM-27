import Panel, { SourceBadge } from './Panel'

const CATEGORY_COLORS = {
  vehicle_motion: 'bg-blue-500',
  signal: 'bg-amber-500',
  collision: 'bg-red-600',
  post_collision: 'bg-violet-500',
  other: 'bg-slate-400',
}

export default function IncidentTimeline({ timeline, time, selectedObs, onFocus }) {
  if (!timeline) return <Panel title="Incident Timeline">Loading…</Panel>

  const events = [...timeline.events].sort((a, b) => a.t_start_s - b.t_start_s)
  // Axis covers the incident window; long-running context (e.g. a signal red since t=-20) is clamped.
  const tMin = 0
  const tMax = Math.max(...events.map((e) => e.t_end_s), 1)
  const pct = (t) => `${((Math.min(Math.max(t, tMin), tMax) - tMin) / (tMax - tMin)) * 100}%`

  return (
    <Panel title="Incident Timeline">
      <div className="relative">
        <div className="pointer-events-none absolute top-0 bottom-0 z-10 w-px bg-red-500" style={{ left: `calc(16rem + (100% - 16rem) * ${(time - tMin) / (tMax - tMin)})` }} />
        <ul className="space-y-1">
          {events.map((e) => {
            const highlighted = e.observation_ids.some((id) => selectedObs.includes(id))
            return (
              <li key={e.id} onClick={() => onFocus(e.t_start_s, e.observation_ids)}
                className={`flex cursor-pointer items-center rounded px-1 py-1 hover:bg-slate-50 ${highlighted ? 'bg-yellow-50 ring-1 ring-yellow-300' : ''}`}>
                <div className="w-64 shrink-0 pr-3">
                  <div className="text-xs font-medium">{e.label}</div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-1 text-[10px] text-slate-500">
                    t={e.t_start_s}s{e.t_end_s !== e.t_start_s && `–${e.t_end_s}s`}
                    {e.uncertainty_s > 0 && ` ±${e.uncertainty_s}s`}
                    {e.source_types.map((s) => <SourceBadge key={s} type={s} />)}
                  </div>
                </div>
                <div className="relative h-3 flex-1 rounded bg-slate-100" title={`confidence ${e.confidence}`}>
                  <div className={`absolute top-0 h-3 rounded ${CATEGORY_COLORS[e.category]}`}
                    style={{ left: pct(e.t_start_s), width: `max(4px, calc(${pct(e.t_end_s)} - ${pct(e.t_start_s)}))`, opacity: 0.4 + 0.6 * e.confidence }} />
                </div>
              </li>
            )
          })}
        </ul>
        <div className="mt-1 flex justify-between pl-64 text-[10px] text-slate-400">
          <span>{tMin}s</span><span>{tMax}s</span>
        </div>
      </div>
    </Panel>
  )
}
