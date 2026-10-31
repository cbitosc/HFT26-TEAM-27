import { useMemo, useState } from 'react'
import Panel, { BasisBadge, SourceBadge } from './Panel'
import { BAND_BAR, BAND_TEXT, band, flatObservations, histogram, mean, pct, trackSummary } from '../metrics'

const BINS = ['0–20%', '20–40%', '40–60%', '60–80%', '80–100%']
const BIN_COLORS = ['bg-rose-400', 'bg-rose-400', 'bg-amber-500', 'bg-emerald-500', 'bg-emerald-500']

function Card({ label, value, sub, tone = 'text-slate-900', hint }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm" title={hint}>
      <div className="text-[0.75rem] font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-0.5 text-2xl font-semibold ${tone}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </div>
  )
}

function Bar({ value }) {
  return (
    <div className="h-1.5 w-full rounded bg-slate-100">
      <div className={`h-1.5 rounded ${value == null ? '' : BAND_BAR[band(value)]}`} style={{ width: `${(value ?? 0) * 100}%` }} />
    </div>
  )
}

// Confidence spread: how many items fall in each fifth of the 0-100% range.
function Histogram({ title, values }) {
  const counts = histogram(values)
  const top = Math.max(1, ...counts)
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-xs">
        <span className="font-medium text-slate-700">{title}</span>
        <span className="text-slate-400">{values.length} items · mean {pct(mean(values))}</span>
      </div>
      <div className="flex h-16 items-end gap-1">
        {counts.map((n, i) => (
          <div key={i} className="flex h-full flex-1 flex-col items-center justify-end" title={`${BINS[i]}: ${n}`}>
            <span className="text-[0.7rem] leading-none text-slate-500">{n || ''}</span>
            <div className={`mt-0.5 w-full rounded-t ${BIN_COLORS[i]}`} style={{ height: `${(n / top) * 100}%`, minHeight: n ? 2 : 0 }} />
          </div>
        ))}
      </div>
      <div className="mt-0.5 flex gap-1 text-center text-[0.65rem] text-slate-400">{BINS.map((b) => <span key={b} className="flex-1">{b}</span>)}</div>
    </div>
  )
}

function GroupTable({ title, hint, rows }) {
  return (
    <div>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h4>
      <p className="mb-1.5 text-[0.75rem] text-slate-400">{hint}</p>
      <ul className="space-y-2">
        {rows.map((r) => (
          <li key={r.key} className="text-xs">
            <div className="mb-0.5 flex items-center justify-between gap-2">
              <span className="flex items-center gap-1.5">{r.badge}<span className="text-slate-400">{r.n} items</span></span>
              <span className={`font-mono ${r.mean == null ? '' : BAND_TEXT[band(r.mean)]}`}>{pct(r.mean)}</span>
            </div>
            <Bar value={r.mean} />
          </li>
        ))}
        {rows.length === 0 && <li className="text-xs text-slate-400">Nothing to show yet.</li>}
      </ul>
    </div>
  )
}

const group = (items, keyOf) => {
  const m = new Map()
  for (const it of items) {
    const k = keyOf(it)
    if (k) m.set(k, [...(m.get(k) ?? []), it.confidence])
  }
  return [...m.entries()].map(([key, cs]) => ({ key, n: cs.length, mean: mean(cs) }))
}

// Confidence scores and the other numbers behind the reconstruction, all computed from what the API returned.
export default function Metrics({ results, run, onOpen }) {
  const [sort, setSort] = useState({ key: 'confidence', dir: 1 })
  const m = useMemo(() => {
    if (!results) return null
    const obs = flatObservations(results.observations)
    const events = results.timeline.events
    const recon = results.reconstruction?.vehicles?.length ? results.reconstruction : null
    const track = recon ? trackSummary(recon.vehicles) : null
    const points = recon ? recon.vehicles.flatMap((v) => v.track.map((p) => p.confidence).filter((c) => c != null)) : []
    const conflicts = results.conflicts.conflicts
    return { obs, events, recon, track, points, conflicts,
      low: obs.filter((o) => o.confidence < 0.4).length + events.filter((e) => e.confidence < 0.4).length }
  }, [results])
  if (!m) return <Panel title="Metrics">Loading…</Panel>

  const { obs, events, recon, track, points, conflicts } = m
  const sev = { high: 0, medium: 0, low: 0 }
  const byType = {}
  for (const c of conflicts) { sev[c.severity] += 1; byType[c.type] = (byType[c.type] ?? 0) + 1 }
  const gaps = obs.filter((o) => o.action === 'sensor_gap').length

  const sorted = [...events].sort((a, b) => {
    const k = sort.key
    const va = k === 'label' ? a.label : a[k]
    const vb = k === 'label' ? b.label : b[k]
    return (va < vb ? -1 : va > vb ? 1 : 0) * sort.dir
  })
  const head = (key, label, cls = '') => (
    <th className={`px-2 py-1 text-left font-medium ${cls}`}>
      <button onClick={() => setSort({ key, dir: sort.key === key ? -sort.dir : 1 })} className="hover:text-slate-900">
        {label}{sort.key === key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
      </button>
    </th>
  )

  return (
    <div className="space-y-4">
      <p className="rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-xs text-slate-600">
        <b>How to read these numbers.</b> A confidence score says how well the evidence supports an item (source quality, agreement,
        hedging in witness wording). It is not the probability that the event happened. Everything below is computed from this
        run's outputs; nothing new is measured here.
      </p>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
        <Card label="Observations" value={pct(mean(obs.map((o) => o.confidence)))} sub={`mean confidence · ${obs.length} items`} />
        <Card label="Timeline events" value={pct(mean(events.map((e) => e.confidence)))} sub={`mean confidence · ${events.length} events`} />
        <Card label="Map points" value={pct(mean(points))} sub={recon ? `mean confidence · ${points.length} points` : 'no reconstruction'}
          tone={recon && recon.scene.units === 'schematic' ? 'text-amber-600' : 'text-slate-900'} />
        <Card label="Observed share" value={pct(track?.observed)} sub={track ? `${pct(1 - track.observed)} inferred` : 'no reconstruction'} tone="text-emerald-700"
          hint="Share of map points read from a sensor sample or tracked in video" />
        <Card label="Low confidence" value={m.low} sub="items below 40%" tone={m.low ? 'text-rose-600' : 'text-slate-900'} />
        <Card label="Open issues" value={conflicts.length} sub={`${sev.high} high · ${sev.medium} medium · ${sev.low} low`} tone={sev.high ? 'text-red-600' : 'text-slate-900'} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Confidence spread">
          <div className="space-y-4">
            <Histogram title="Observations" values={obs.map((o) => o.confidence)} />
            <Histogram title="Timeline events" values={events.map((e) => e.confidence)} />
            {recon && <Histogram title="Map points" values={points} />}
          </div>
        </Panel>

        <Panel title="Confidence by source">
          <div className="space-y-5">
            <GroupTable title="Source type" hint="Which kind of evidence the observation came from"
              rows={group(obs, (o) => o.source_type).map((r) => ({ ...r, badge: <SourceBadge type={r.key} /> }))} />
            <GroupTable title="Basis" hint="Observation vs inference (measured, derived, reported, inferred)"
              rows={group(obs, (o) => o.basis).map((r) => ({ ...r, badge: <BasisBadge basis={r.key} /> }))} />
          </div>
        </Panel>

        <Panel title="Issues and coverage">
          <dl className="space-y-1.5 text-xs">
            {[
              ['Contradictions and gaps', conflicts.length],
              ...Object.entries(byType).map(([t, n]) => [`  ${t.replaceAll('_', ' ')}`, n]),
              ['Sensor dropouts', gaps],
              ['Evidence files', run?.evidence?.length ?? 0],
              ['Observations', obs.length],
              ['Timeline events', events.length],
              ['Tracked vehicles', recon?.vehicles.length ?? 0],
              ['Replay window', recon ? `${recon.t_min_s.toFixed(1)}–${recon.t_max_s.toFixed(1)} s` : '—'],
              ['Inferred collision', recon?.collision ? `t=${recon.collision.t_s}s · ${pct(recon.collision.confidence)}` : 'none'],
              ['Extraction warnings', results.observations.reduce((a, s) => a + s.warnings.length, 0)],
            ].map(([k, v]) => (
              <div key={k} className="flex justify-between gap-2 border-b border-slate-100 pb-1 last:border-0">
                <dt className="whitespace-pre text-slate-500">{k}</dt><dd className="font-mono text-slate-800">{v}</dd>
              </div>
            ))}
          </dl>
          {conflicts.length > 0 && <button onClick={() => onOpen('contradictions')} className="mt-3 text-xs text-sky-700 hover:underline">Open the Contradiction Explorer →</button>}
        </Panel>
      </div>

      {recon && (
        <Panel title="Vehicles">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[32rem] text-xs">
              <thead className="text-slate-500"><tr>
                <th className="px-2 py-1 text-left font-medium">Track</th><th className="px-2 py-1 text-left font-medium">Seen</th>
                <th className="px-2 py-1 text-right font-medium">Points</th><th className="px-2 py-1 text-left font-medium">Observed</th>
                <th className="px-2 py-1 text-right font-medium">Confidence</th>
              </tr></thead>
              <tbody>
                {[...recon.vehicles].sort((a, b) => b.track.length - a.track.length).slice(0, 15).map((v) => {
                  const s = trackSummary([v])
                  return (
                    <tr key={v.vehicle_id} className="border-t border-slate-100">
                      <td className="px-2 py-1"><span className="mr-1.5 inline-block h-2 w-3 rounded-sm align-middle" style={{ background: v.color }} />{v.label}</td>
                      <td className="px-2 py-1 font-mono text-slate-500">{v.track[0].t_s.toFixed(1)}–{v.track.at(-1).t_s.toFixed(1)}s</td>
                      <td className="px-2 py-1 text-right font-mono">{v.track.length}</td>
                      <td className="px-2 py-1">
                        <div className="flex items-center gap-2"><div className="h-1.5 w-24 overflow-hidden rounded bg-violet-200"><div className="h-1.5 bg-emerald-500" style={{ width: `${(s.observed ?? 0) * 100}%` }} /></div>
                          <span className="font-mono text-slate-500">{pct(s.observed)}</span></div>
                      </td>
                      <td className={`px-2 py-1 text-right font-mono ${s.mean == null ? '' : BAND_TEXT[band(s.mean)]}`}>{pct(s.mean)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
            {recon.vehicles.length > 15 && <p className="mt-1 px-2 text-[0.75rem] text-slate-400">Showing the 15 longest of {recon.vehicles.length} tracks.</p>}
          </div>
        </Panel>
      )}

      <Panel title="Timeline events by confidence">
        {events.length === 0 && <p className="text-xs text-slate-400">No events yet.</p>}
        {events.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[36rem] text-xs">
              <thead className="text-slate-500"><tr>
                {head('t_start_s', 'Time')}{head('label', 'Event')}{head('confidence', 'Confidence')}{head('uncertainty_s', '± time')}
                <th className="px-2 py-1 text-left font-medium">Basis</th><th className="px-2 py-1 text-left font-medium">Sources</th>
              </tr></thead>
              <tbody>
                {sorted.map((e) => (
                  <tr key={e.id} className="border-t border-slate-100">
                    <td className="whitespace-nowrap px-2 py-1 font-mono text-slate-500">{e.t_start_s}s</td>
                    <td className="px-2 py-1">{e.label}</td>
                    <td className="px-2 py-1">
                      <div className="flex items-center gap-2"><div className="w-20"><Bar value={e.confidence} /></div>
                        <span className={`font-mono ${BAND_TEXT[band(e.confidence)]}`}>{pct(e.confidence)}</span></div>
                    </td>
                    <td className="px-2 py-1 font-mono text-slate-500">{e.uncertainty_s ? `±${e.uncertainty_s}s` : '—'}</td>
                    <td className="px-2 py-1"><BasisBadge basis={e.basis} /></td>
                    <td className="px-2 py-1"><span className="flex gap-1">{e.source_types.map((s) => <SourceBadge key={s} type={s} />)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {run && (
        <Panel title="Run">
          <div className="grid gap-4 text-xs lg:grid-cols-2">
            <div>
              <h4 className="mb-1 font-semibold uppercase tracking-wide text-slate-500">Stages</h4>
              <ul className="space-y-1">
                {run.stages.map((s) => (
                  <li key={s.stage} className="flex items-center gap-2">
                    <span className={`h-2 w-2 shrink-0 rounded-full ${s.status === 'real' ? 'bg-emerald-500' : s.status === 'error' ? 'bg-red-500' : 'bg-slate-300'}`} />
                    <span className="min-w-0 flex-1 truncate" title={s.message}>{s.stage}</span>
                    <span className="shrink-0 text-slate-400">{s.status}</span>
                    <span className="w-12 shrink-0 text-right font-mono text-slate-500">{s.duration_s != null ? `${s.duration_s}s` : ''}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h4 className="mb-1 font-semibold uppercase tracking-wide text-slate-500">Evidence analysed (SHA-256)</h4>
              <ul className="space-y-1">
                {(run.evidence ?? []).map((e) => (
                  <li key={e.id} className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate" title={e.filename}>{e.filename}</span>
                    <code className="shrink-0 text-slate-400" title={e.sha256 ?? ''}>{e.sha256 ? e.sha256.slice(0, 10) : 'n/a'}…</code>
                  </li>
                ))}
                {(run.evidence ?? []).length === 0 && <li className="text-slate-400">Demo fixtures: no uploaded evidence.</li>}
              </ul>
            </div>
          </div>
        </Panel>
      )}
    </div>
  )
}
