import { useState } from 'react'
import { SOURCE_LABELS, evidenceRuns, isObservedSource, pct, trackSummary } from '../metrics'

const HATCH = 'repeating-linear-gradient(45deg,transparent 0 3px,rgba(255,255,255,.55) 3px 5px)'
const SOURCE_STYLE = {
  measured: 'bg-emerald-600',
  tracked: 'bg-emerald-400',
  interpolated: 'bg-violet-500',
  extrapolated: 'bg-violet-400',
  estimated: 'bg-violet-300',
}
const ROWS_SHOWN = 8

// How much of every vehicle's path is observed and how much is inferred, over the replay window.
// Click anywhere on a strip to jump the map to that moment.
export default function ObservedInferredChart({ data, time, onSeek, selected, onSelect }) {
  const [all, setAll] = useState(false)
  const span = data.t_max_s - data.t_min_s || 1
  const x = (t) => `${Math.min(100, Math.max(0, ((t - data.t_min_s) / span) * 100))}%`
  const total = trackSummary(data.vehicles)

  // Longest-lived tracks first: a 58-track camera plan would otherwise bury the chart.
  const ranked = [...data.vehicles].sort((a, b) => b.track.at(-1).t_s - b.track[0].t_s - (a.track.at(-1).t_s - a.track[0].t_s))
  const rows = all ? ranked : ranked.slice(0, ROWS_SHOWN)
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => data.t_min_s + f * span)

  const seekFromClick = (e) => {
    const r = e.currentTarget.getBoundingClientRect()
    onSeek(data.t_min_s + ((e.clientX - r.left) / r.width) * span)
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4" aria-label="Observed versus inferred over time">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-700">Observed vs inferred over time</h3>
          <p className="mt-0.5 max-w-xl text-xs text-slate-500">
            Green is what the evidence directly shows. Hatched violet is filled in by interpolation, extrapolation or assumption.
            Click a strip to jump the map to that moment.
          </p>
        </div>
        <div className="text-right text-xs">
          <div><span className="text-lg font-semibold text-emerald-700">{pct(total.observed)}</span> <span className="text-slate-500">observed</span></div>
          <div><span className="text-lg font-semibold text-violet-700">{pct(total.observed == null ? null : 1 - total.observed)}</span> <span className="text-slate-500">inferred</span></div>
        </div>
      </div>

      <div className="mt-3 flex items-center gap-4 text-[0.75rem] text-slate-600">
        <span className="flex items-center gap-1.5"><span className="inline-block h-2.5 w-6 rounded-sm bg-emerald-500" />Observed</span>
        <span className="flex items-center gap-1.5"><span className="inline-block h-2.5 w-6 rounded-sm bg-violet-500 opacity-60" style={{ backgroundImage: HATCH }} />Inferred</span>
        <span className="flex items-center gap-1.5"><span className="inline-block h-2.5 w-6 rounded-sm border border-dashed border-slate-300" />Not in view</span>
        <span className="flex items-center gap-1.5"><span className="inline-block h-3 w-px bg-red-500" />Now</span>
        {data.collision && <span className="flex items-center gap-1.5"><span className="inline-block h-3 border-l border-dashed border-red-600" />Inferred impact</span>}
      </div>

      <div className="mt-3 grid grid-cols-[minmax(5rem,9rem)_minmax(0,1fr)_3rem] items-center gap-x-3 gap-y-1.5">
        {rows.map((v) => {
          const s = trackSummary([v])
          const runs = evidenceRuns(v.track)
          const active = selected === v.vehicle_id
          return (
            <div key={v.vehicle_id} className="contents">
              <button onClick={() => onSelect(active ? null : v.vehicle_id)} title="Select this vehicle on the map"
                className={`flex min-w-0 items-center gap-1.5 rounded px-1 py-0.5 text-left text-xs ${active ? 'bg-slate-900 text-white' : 'hover:bg-slate-100'}`}>
                <span className="inline-block h-2 w-3 shrink-0 rounded-sm" style={{ background: v.color }} />
                <span className="truncate">{v.label}</span>
              </button>
              <div role="slider" aria-label={`${v.label} observed vs inferred; click to seek`} aria-valuenow={Math.round(time)} onClick={seekFromClick}
                className={`relative h-4 cursor-pointer overflow-hidden rounded border border-dashed ${active ? 'border-slate-500' : 'border-slate-300'}`}>
                {runs.map((r, i) => (
                  <div key={i} className={`absolute inset-y-0 ${r.observed ? 'bg-emerald-500' : 'bg-violet-500 opacity-60'}`}
                    title={`${r.observed ? 'observed' : 'inferred'} · t=${r.start.toFixed(1)}–${r.end.toFixed(1)}s`}
                    style={{ left: x(r.start), width: `max(2px, calc(${x(r.end)} - ${x(r.start)}))`, backgroundImage: r.observed ? undefined : HATCH }} />
                ))}
                {data.collision && <div className="absolute inset-y-0 border-l border-dashed border-red-600" style={{ left: x(data.collision.t_s) }} />}
                <div className="absolute inset-y-0 w-px bg-red-500" style={{ left: x(time) }} />
              </div>
              <span className="text-right font-mono text-xs text-slate-600" title="share of this track that is observed">{pct(s.observed)}</span>
            </div>
          )
        })}
        <div />
        <div className="relative h-4 text-[0.7rem] text-slate-400">
          {ticks.map((t, i) => (
            <span key={i} className={`absolute ${i === 0 ? '' : i === ticks.length - 1 ? '-translate-x-full' : '-translate-x-1/2'}`} style={{ left: x(t) }}>{t.toFixed(0)}s</span>
          ))}
        </div>
        <div />
      </div>
      {ranked.length > ROWS_SHOWN && (
        <button onClick={() => setAll(!all)} className="mt-2 text-xs text-sky-700 hover:underline">
          {all ? 'Show fewer tracks' : `Show all ${ranked.length} tracks`}
        </button>
      )}

      <div className="mt-4">
        <div className="mb-1 flex items-center justify-between text-xs text-slate-600">
          <span className="font-medium">Where every point comes from</span>
          <span className="text-slate-400">{total.total} points</span>
        </div>
        <div className="flex h-3 overflow-hidden rounded bg-slate-100">
          {Object.keys(SOURCE_STYLE).filter((k) => total.counts[k]).map((k) => (
            <div key={k} className={SOURCE_STYLE[k]} title={`${SOURCE_LABELS[k]}: ${total.counts[k]} points`}
              style={{ width: `${(total.counts[k] / total.total) * 100}%`, backgroundImage: isObservedSource(k) ? undefined : HATCH }} />
          ))}
        </div>
        <ul className="mt-2 grid gap-x-6 gap-y-0.5 text-xs text-slate-600 sm:grid-cols-2">
          {Object.keys(SOURCE_STYLE).filter((k) => total.counts[k]).map((k) => (
            <li key={k} className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1.5"><span className={`inline-block h-2 w-2 rounded-sm ${SOURCE_STYLE[k]}`} />{SOURCE_LABELS[k]}</span>
              <span className="font-mono text-slate-500">{pct(total.counts[k] / total.total)}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}
