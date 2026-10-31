import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import CameraTrajectories from './CameraTrajectories'
import Panel from './Panel'
import ReconstructionTrace, { isObservedSource } from './ReconstructionTrace'

const SIGNAL_COLORS = { red: '#dc2626', yellow: '#eab308', green: '#16a34a' }
// Inbound lanes (left-hand traffic): where each approach's stop line and signal head sit, in lane widths.
const APPROACHES = {
  NB: { name: 'Northbound', stop: [[-1, -1], [0, -1]], head: [-1.45, -1.3] },
  SB: { name: 'Southbound', stop: [[0, 1], [1, 1]], head: [1.45, 1.3] },
  EB: { name: 'Eastbound', stop: [[-1, 0], [-1, 1]], head: [-1.3, 1.45] },
  WB: { name: 'Westbound', stop: [[1, -1], [1, 0]], head: [1.3, -1.45] },
}
// Half-width of the view in metres: auto follows the vehicles, full shows the whole scene.
const ZOOMS = { auto: null, junction: 18, full: null }
const SPEEDS = [0.25, 0.5, 1, 2, 4]
const isNum = (v) => typeof v === 'number' && Number.isFinite(v)

// Linear interpolation of a vehicle's track at time t.
function positionAt(track, t) {
  if (t <= track[0].t_s) return track[0]
  for (let i = 1; i < track.length; i++) {
    const a = track[i - 1]
    const b = track[i]
    if (t <= b.t_s) {
      const k = (t - a.t_s) / (b.t_s - a.t_s)
      const dh = ((b.heading_deg - a.heading_deg + 540) % 360) - 180
      return {
        t_s: t,
        x_m: a.x_m + (b.x_m - a.x_m) * k,
        y_m: a.y_m + (b.y_m - a.y_m) * k,
        heading_deg: a.heading_deg + dh * k,
        speed_kmh: a.speed_kmh != null && b.speed_kmh != null ? a.speed_kmh + (b.speed_kmh - a.speed_kmh) * k : null,
        source: k < 0.5 ? a.source : b.source,
        confidence: a.confidence != null && b.confidence != null ? Math.min(a.confidence, b.confidence) : a.confidence ?? b.confidence,
      }
    }
  }
  return track[track.length - 1]
}

// The backend lists each vehicle's measured keyframes in source_observation_ids; look up
// their times so track points can be split into measured and interpolated.
function measuredTimes(vehicle, obsById) {
  const times = []
  for (const id of vehicle.source_observation_ids ?? []) {
    const o = obsById[id]
    if (o && isNum(o.t_rel_s) && isNum(o.attributes?.x_m) && isNum(o.attributes?.y_m)) times.push(o.t_rel_s)
  }
  return times
}

// Timed signal_state observations per approach, most trusted source first (sensor, video, witness).
const SOURCE_RANK = { sensor: 0, video: 1, witness: 2 }
function signalPhases(observations) {
  const byApproach = {}
  for (const o of observations) {
    const appr = o.attributes?.approach
    if (o.action !== 'signal_state' || !APPROACHES[appr] || !isNum(o.t_rel_s) || !SIGNAL_COLORS[o.attributes.signal_state]) continue
    ;(byApproach[appr] ??= []).push(o)
  }
  for (const list of Object.values(byApproach)) {
    list.sort((a, b) => (SOURCE_RANK[a.source_type] ?? 3) - (SOURCE_RANK[b.source_type] ?? 3) || a.t_rel_s - b.t_rel_s)
  }
  return byApproach
}

// Latest state at t from the most trusted source that has reported by then.
function signalAt(phases, t) {
  let state = null
  let rank = null
  for (const o of phases) {
    const r = SOURCE_RANK[o.source_type] ?? 3
    if (rank != null && r !== rank) break
    if (o.t_rel_s <= t + 1e-6) { state = o.attributes.signal_state; rank = r }
  }
  return state
}

// Place labels beside their anchors (clear of a car body), nudging later ones down until no box overlaps.
function layoutLabels(items, fontSize) {
  const placed = []
  const h = fontSize * 1.3
  const gap = Math.max(fontSize * 1.1, 3)
  for (const it of [...items].sort((a, b) => a.y - b.y)) {
    const w = it.text.length * fontSize * 0.58
    let x = it.side === 'left' ? it.x - gap - w : it.x + gap
    let y = it.y - fontSize * 0.4
    const hits = () => placed.some((p) => x < p.x + p.w && x + w > p.x && y - h < p.y && y > p.y - p.h)
    for (let i = 0; i < 12 && hits(); i++) y += h * 1.05
    placed.push({ ...it, ax: it.x, ay: it.y, x, y, w, h, moved: Math.abs(y - (it.y - fontSize * 0.4)) > 1e-6 })
  }
  return placed
}

export default function ReconstructionViewer({ data, time, onTimeChange, timeline, conflicts, observationsById = {}, onFocus }) {
  const [playing, setPlaying] = useState(false)
  const [selected, setSelected] = useState(null)
  useEffect(() => { setSelected(null) }, [data])
  // A plan built from camera tracks has nothing to show before its first track: open there.
  useEffect(() => {
    if (data?.scene?.units !== 'schematic' || !data.vehicles.length) return
    const first = Math.min(...data.vehicles.map((v) => v.track[0].t_s))
    if (timeRef.current < first) onTimeChange(first)
  }, [data]) // eslint-disable-line react-hooks/exhaustive-deps
  const [zoom, setZoom] = useState('auto')
  const [rate, setRate] = useState(1)
  const rateRef = useRef(1)
  useEffect(() => { rateRef.current = rate }, [rate])
  const [observations, setObservations] = useState(null)
  const timeRef = useRef(time)
  useEffect(() => { timeRef.current = time }, [time])

  // Observations explain which track points were measured and what the signals showed.
  useEffect(() => {
    if (!data) return
    let alive = true
    api.observations()
      .then((sets) => { if (alive) setObservations(sets.flatMap((s) => s.observations)) })
      .catch(() => { if (alive) setObservations([]) })
    return () => { alive = false }
  }, [data])

  useEffect(() => {
    if (!playing || !data) return
    let frame
    let last = performance.now()
    const tick = (now) => {
      const next = timeRef.current + ((now - last) / 1000) * rateRef.current
      last = now
      if (next >= data.t_max_s) {
        onTimeChange(data.t_max_s)
        setPlaying(false)
        return
      }
      onTimeChange(next)
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [playing, data, onTimeChange])

  const derived = useMemo(() => {
    const obsById = Object.fromEntries((observations ?? []).map((o) => [o.id, o]))
    const measured = {}
    for (const v of data?.vehicles ?? []) measured[v.vehicle_id] = measuredTimes(v, obsById)
    return { measured, signals: signalPhases(observations ?? []), known: observations != null && observations.length > 0 }
  }, [data, observations])

  // Timeline events and contradictions that fall inside the replay window, as jump targets.
  const moments = useMemo(() => {
    if (!data) return []
    const out = []
    for (const e of timeline?.events ?? []) {
      out.push({ id: e.id, t: e.t_start_s, label: e.label, kind: e.category === 'collision' ? 'collision' : 'event', basis: e.basis, observationIds: e.observation_ids })
    }
    for (const c of conflicts?.conflicts ?? []) {
      if (c.t_range_s) out.push({ id: c.id, t: c.t_range_s[0], label: c.title, kind: 'conflict', observationIds: c.observation_ids })
    }
    return out.filter((m) => m.t >= data.t_min_s - 1e-6 && m.t <= data.t_max_s + 1e-6).sort((a, b) => a.t - b.t)
  }, [data, timeline, conflicts])

  if (!data) return <Panel title="Reconstruction">Loading…</Panel>
  if (data.scene.type === 'camera_image') {
    return <CameraTrajectories data={data} time={time} onTimeChange={onTimeChange} Panel={Panel} title="Reconstruction" />
  }
  if (data.vehicles.length === 0) {
    return (
      <Panel title="Reconstruction">
        <div className="flex h-full min-h-32 flex-col items-center justify-center gap-1 text-center">
          <p className="text-sm text-slate-600">No top-down reconstruction for this evidence.</p>
          <p className="max-w-lg text-xs text-slate-400">
            {data.note ?? 'Reconstruction needs positional evidence: telemetry/GPS with positions, or a calibrated camera view.'}
          </p>
        </div>
      </Panel>
    )
  }

  const { extent_m: sceneE, lane_width_m: L = 3.5, roads } = data.scene
  // A declared junction is drawn with roads; a plain metric map shows only the evidence.
  const junction = data.scene.type !== 'metric'
  let [cx, cy] = data.scene.center_m ?? [0, 0]
  let fitE = sceneE
  if (data.scene.units === 'schematic') {
    // No scene to anchor to: frame whatever the tracks cover.
    const xs = data.vehicles.flatMap((v) => v.track.map((q) => q.x_m))
    const ys = data.vehicles.flatMap((v) => v.track.map((q) => q.y_m))
    cx = (Math.min(...xs) + Math.max(...xs)) / 2
    cy = (Math.min(...ys) + Math.max(...ys)) / 2
    fitE = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) / 2 + 3
  }
  const roadBand = data.scene.units === 'schematic' ? {
    x0: Math.min(...data.vehicles.flatMap((v) => v.track.map((q) => q.x_m))) - 2,
    x1: Math.max(...data.vehicles.flatMap((v) => v.track.map((q) => q.x_m))) + 2,
  } : null
  const R = 1.5 * sceneE // roads run past the scene edge so the auto view never shows them ending
  const schematic = data.scene.units === 'schematic'
  // Camera tracks exist only while the vehicle is seen; sensor tracks span the whole window.
  const visible = data.vehicles.filter((v) => !schematic || (time >= v.track[0].t_s - 0.3 && time <= v.track[v.track.length - 1].t_s + 0.3))
  const current = visible.map((v) => ({ v, p: positionAt(v.track, time) }))
  const E = !junction
    ? fitE
    : zoom === 'auto'
      ? Math.min(1.5 * sceneE, Math.max(ZOOMS.junction, ...current.map(({ p }) => Math.max(Math.abs(p.x_m), Math.abs(p.y_m)) + 8)))
      : ZOOMS[zoom] ?? sceneE
  const carScale = schematic ? Math.max(0.3, Math.min(1, E / 40)) : 1 // schematic units are not metres: keep icons small
  const s = E / 60 // scale strokes and text with the view
  const fs = 2.2 * s
  const half = L // two lanes per road -> half-width = one lane width each side
  const openHere = (conflicts?.conflicts ?? []).filter((c) => c.t_range_s && time >= c.t_range_s[0] - 0.5 && time <= c.t_range_s[1] + 0.5)
  // Actual (observed) paths are solid lines; inferred and still-to-come paths are dotted.
  const dotW = 0.6 * Math.max(s, 0.4)
  const dotted = `0 ${2.2 * dotW}`
  const showCollision = data.collision && time >= data.collision.t_s - 1e-6

  // Observed = read from a sensor sample or tracked in video. Everything else (interpolated, extrapolated,
  // estimated) is an inference. Old data without a point source falls back to the measured keyframes.
  const obsPoint = (vid, q) => (q.source ? isObservedSource(q.source) : !derived.known || derived.measured[vid].some((m) => Math.abs(m - q.t_s) < 0.01))

  const step = (dir) => {
    setPlaying(false)
    const nxt = time + dir * 0.5
    onTimeChange(Math.min(data.t_max_s, Math.max(data.t_min_s, Math.round(nxt * 2) / 2)))
  }
  const onKey = (e) => {
    if (e.key === 'ArrowRight') { e.preventDefault(); step(1) }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1) }
    else if (e.key === ' ') { e.preventDefault(); togglePlay() }
  }

  const seek = (t) => { setPlaying(false); onTimeChange(Math.min(data.t_max_s, Math.max(data.t_min_s, t))) }
  const selectedVehicle = data.vehicles.find((v) => v.vehicle_id === selected)
  const selectedPos = selectedVehicle && time >= selectedVehicle.track[0].t_s - 0.3 && time <= selectedVehicle.track.at(-1).t_s + 0.3
    ? positionAt(selectedVehicle.track, time) : null

  const togglePlay = () => {
    if (!playing && time >= data.t_max_s) onTimeChange(data.t_min_s)
    setPlaying(!playing)
  }

  const labels = layoutLabels([
    ...current.map(({ v, p }) => ({
      key: v.vehicle_id, x: p.x_m, y: -p.y_m, color: 'var(--color-slate-900)',
      text: `${v.vehicle_id}${p.speed_kmh != null ? ` · ${p.speed_kmh.toFixed(0)} km/h` : ''}${p.confidence != null ? ` · ${Math.round(p.confidence * 100)}%` : ''}${selected === v.vehicle_id ? (obsPoint(v.vehicle_id, p) ? ' · observed' : ' · inferred') : ''}`,
    })),
    ...(showCollision ? [{ key: 'collision', x: data.collision.x_m, y: -data.collision.y_m + 2 * fs, color: 'var(--color-red-700)', side: 'left',
      text: `inferred impact t=${data.collision.t_s.toFixed(1)}s · ${Math.round(data.collision.confidence * 100)}%` }] : []),
  ], fs)

  const signalNow = Object.entries(derived.signals).map(([appr, phases]) => [appr, signalAt(phases, time)]).filter(([, st]) => st)

  return (
    <Panel title="Reconstruction"
      actions={
        <div className="flex items-center gap-2 text-xs">
          <span className="text-slate-500" title={data.note ?? ''}>
            {schematic ? 'schematic plan · not to scale' : junction ? 'declared junction · metres' : data.scene.frame_note ?? 'top-down map · metres'}
          </span>
          <div className={`flex overflow-hidden rounded border border-slate-200 ${junction ? '' : 'hidden'}`}>
            {Object.keys(ZOOMS).map((z) => (
              <button key={z} onClick={() => setZoom(z)}
                className={`px-2 py-0.5 capitalize ${zoom === z ? 'bg-slate-900 text-white' : 'bg-white text-slate-600'}`}>{z}</button>
            ))}
          </div>
        </div>
      }>
      {schematic && (
        <div className="mb-2 rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900">
          <b>Schematic estimate, not a measurement.</b> {data.note}
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_21rem]">
      <div className="min-w-0">
      <svg viewBox={`${cx - E} ${-cy - E} ${2 * E} ${2 * E}`} className="mx-auto block aspect-square w-full max-w-xl rounded bg-lime-50">
        {/* World coordinates: +y is north, so flip the y axis. */}
        <g transform="scale(1,-1)">
          {junction && <>
          <rect x={-R} y={-half} width={2 * R} height={2 * half} fill="var(--color-slate-300)" />
          <rect x={-half} y={-R} width={2 * half} height={2 * R} fill="var(--color-slate-300)" />
          <line x1={-R} y1={0} x2={-half} y2={0} stroke="white" strokeWidth={0.2} strokeDasharray="2 2" />
          <line x1={half} y1={0} x2={R} y2={0} stroke="white" strokeWidth={0.2} strokeDasharray="2 2" />
          <line x1={0} y1={-R} x2={0} y2={-half} stroke="white" strokeWidth={0.2} strokeDasharray="2 2" />
          <line x1={0} y1={half} x2={0} y2={R} stroke="white" strokeWidth={0.2} strokeDasharray="2 2" />

          {/* Stop lines on the inbound lanes, signal heads on the left kerb. */}
          {Object.entries(APPROACHES).map(([appr, a]) => (
            <line key={appr} x1={a.stop[0][0] * L} y1={a.stop[0][1] * L} x2={a.stop[1][0] * L} y2={a.stop[1][1] * L}
              stroke="white" strokeWidth={0.45} />
          ))}
          {signalNow.map(([appr, st]) => {
            const [hx, hy] = APPROACHES[appr].head
            return (
              <g key={appr} transform={`translate(${hx * L},${hy * L})`}>
                <circle r={Math.max(0.75, 1.6 * s)} fill="#0f172a" />
                <circle r={Math.max(0.5, 1.1 * s)} fill={SIGNAL_COLORS[st]} />
              </g>
            )
          })}
          </>}

          {roadBand && (
            <g>
              <rect x={roadBand.x0} y={cy - E} width={roadBand.x1 - roadBand.x0} height={2 * E} fill="var(--color-slate-300)" fillOpacity={0.55} />
            </g>
          )}

          {showCollision && (
            <g transform={`translate(${data.collision.x_m},${data.collision.y_m})`} opacity={0.85}>
              <circle r={2.2} fill="#fecaca" fillOpacity={0.5} stroke="#dc2626" strokeWidth={0.2} strokeDasharray="0.6 0.5" />
              <path d="M-1,-1 L1,1 M-1,1 L1,-1" stroke="#dc2626" strokeWidth={0.2} />
            </g>
          )}

          {current.map(({ v, p }) => {
            const past = v.track.filter((q) => q.t_s < time)
            const pts = [...past, p]
            return (
              <g key={v.vehicle_id} onClick={() => setSelected(selected === v.vehicle_id ? null : v.vehicle_id)}
                style={{ cursor: 'pointer' }} opacity={selected && selected !== v.vehicle_id ? 0.35 : 1}>
                {(!schematic || selected === v.vehicle_id) && (
                  <polyline points={[p, ...v.track.filter((q) => q.t_s > time)].map((q) => `${q.x_m},${q.y_m}`).join(' ')}
                    fill="none" stroke={v.color} strokeWidth={dotW} strokeLinecap="round" strokeDasharray={dotted}
                    strokeOpacity={selected === v.vehicle_id ? 0.7 : 0.5} />
                )}
                {pts.slice(1).map((b, i) => {
                  const a = pts[i]
                  // the last segment ends at the vehicle, inside the grid step that ends at nextPoint
                  const end = b === p ? nextPoint(v.track, time) : b
                  const solid = obsPoint(v.vehicle_id, a) && obsPoint(v.vehicle_id, end)
                  return (
                    <line key={i} x1={a.x_m} y1={a.y_m} x2={b.x_m} y2={b.y_m} stroke={v.color}
                      strokeWidth={solid ? 0.5 * Math.max(s, 0.4) : dotW} strokeOpacity={solid ? 0.85 : 0.9}
                      strokeLinecap={solid ? undefined : 'round'} strokeDasharray={solid ? undefined : dotted} />
                  )
                })}
                {past.filter((q) => obsPoint(v.vehicle_id, q)).map((q) => (
                  <circle key={q.t_s} cx={q.x_m} cy={q.y_m} r={0.5 * Math.max(s, 0.4)} fill={v.color} stroke="white" strokeWidth={0.1} />
                ))}
                {selected === v.vehicle_id && past.filter((q) => !obsPoint(v.vehicle_id, q)).map((q) => (
                  <circle key={q.t_s} cx={q.x_m} cy={q.y_m} r={0.4 * Math.max(s, 0.4)} fill="var(--color-white)" stroke={v.color} strokeWidth={0.12 * Math.max(s, 0.4)} />
                ))}
                <g transform={`translate(${p.x_m},${p.y_m}) rotate(${-p.heading_deg}) scale(${carScale})`}>
                  {selected === v.vehicle_id && <circle r={3.2} fill="none" stroke="#0f172a" strokeWidth={0.2} strokeDasharray="0.6 0.5" />}
                  {schematic && <ellipse rx={2.4} ry={3.4} fill={v.color} fillOpacity={0.12} />}
                  <rect x={-0.9} y={-2.25} width={1.8} height={4.5} rx={0.4} fill={v.color} fillOpacity={obsPoint(v.vehicle_id, p) ? 1 : 0.5}
                    stroke="#0f172a" strokeWidth={0.15} strokeDasharray={obsPoint(v.vehicle_id, p) ? undefined : '0.5 0.4'} />
                  <rect x={-0.7} y={0.8} width={1.4} height={0.9} fill="#e2e8f0" />
                </g>
              </g>
            )
          })}

        </g>

        {junction && <text x={half + 1 * s} y={-E + 4 * s} fontSize={2.5 * s} fill="var(--color-slate-600)">{roads?.ns}</text>}
        {junction && <text x={-E + 1 * s} y={half + 3 * s} fontSize={2.5 * s} fill="var(--color-slate-600)">{roads?.ew}</text>}
        <text x={cx - E + 1 * s} y={-cy + E - 2 * s} fontSize={2.5 * s} fill="var(--color-slate-600)">N ↑</text>
        {roadBand && <text x={cx - E + 1 * s} y={-cy - E + 3 * s} fontSize={2 * s} fill="var(--color-slate-500)">grey band: road extent assumed from where vehicles were seen</text>}
        {labels.map((l) => (
          <g key={l.key}>
            <line x1={l.ax} y1={l.ay} x2={l.side === 'left' ? l.x + l.w + 0.3 * fs : l.x - 0.3 * fs} y2={l.y - l.h * 0.45}
              stroke="var(--color-slate-500)" strokeWidth={0.12 * Math.max(s, 0.4)} />
            <rect x={l.x - 0.3 * fs} y={l.y - l.h * 0.9} width={l.w + 0.6 * fs} height={l.h} rx={0.3 * fs}
              fill="var(--color-white)" fillOpacity={0.85} />
            <text x={l.x} y={l.y - l.h * 0.2} fontSize={fs} fontWeight="600" fill={l.color}>{l.text}</text>
          </g>
        ))}
      </svg>

      <div className="mt-3 flex flex-wrap items-center gap-2" tabIndex={0} onKeyDown={onKey} aria-label="Replay controls">
        <button onClick={() => step(-1)} aria-label="Step back" className="rounded border border-slate-200 px-2 py-1 text-xs">◀</button>
        <button onClick={togglePlay} className="w-16 rounded bg-slate-900 px-2 py-1 text-xs font-medium text-white">
          {playing ? 'Pause' : 'Play'}
        </button>
        <button onClick={() => step(1)} aria-label="Step forward" className="rounded border border-slate-200 px-2 py-1 text-xs">▶</button>
        <div className="relative min-w-40 flex-1 basis-full sm:basis-0">
          <input type="range" min={data.t_min_s} max={data.t_max_s} step={0.1} value={time} aria-label="Time"
            onChange={(e) => { setPlaying(false); onTimeChange(Number(e.target.value)) }} className="w-full" />
          {data.t_max_s > data.t_min_s && moments.map((m) => (
            <span key={m.id} title={`t=${m.t}s ${m.label}`} className={`pointer-events-none absolute top-4 h-1.5 w-1.5 -translate-x-1/2 rounded-full ${m.kind === 'conflict' ? 'bg-amber-500' : 'bg-slate-400'}`}
              style={{ left: `${((m.t - data.t_min_s) / (data.t_max_s - data.t_min_s)) * 100}%` }} />
          ))}
          {data.collision && data.t_max_s > data.t_min_s && (
            <span title={`inferred impact t=${data.collision.t_s}s`} className="pointer-events-none absolute top-5 -translate-x-1/2 text-[0.7rem] leading-none text-red-600"
              style={{ left: `${((data.collision.t_s - data.t_min_s) / (data.t_max_s - data.t_min_s)) * 100}%` }}>▲ inferred impact</span>
          )}
        </div>
        <select value={rate} onChange={(e) => setRate(Number(e.target.value))} aria-label="Playback speed"
          className="rounded border border-slate-200 bg-white px-1 py-1 text-xs">
          {SPEEDS.map((r) => <option key={r} value={r}>{r}×</option>)}
        </select>
        <span className="w-16 text-right font-mono text-xs">t={time.toFixed(1)}s</span>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-600">
        {schematic && <span>{data.vehicles.length} tracks · {visible.length} visible at this time</span>}
        {(schematic ? visible.slice(0, 6) : data.vehicles).map((v) => (
          <button key={v.vehicle_id} onClick={() => setSelected(selected === v.vehicle_id ? null : v.vehicle_id)}
            className={`flex items-center gap-1 rounded px-1 ${selected === v.vehicle_id ? 'bg-slate-900 text-white' : 'hover:bg-slate-100'}`}>
            <span className="inline-block h-2 w-3 rounded-sm" style={{ background: v.color }} />{v.label}
          </button>
        ))}
        <a href={api.positionsUrl} className="ml-auto rounded border border-slate-200 px-2 py-0.5 text-slate-600 hover:bg-slate-50">
          Download positions (JSON)
        </a>
      </div>
      {openHere.length > 0 && (
        <div className="mt-2 rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900">
          <b>⚠ Open at t={time.toFixed(1)}s:</b> {openHere.map((c) => c.title).join(' · ')}.
          <a href="#contradictions" className="ml-1 font-semibold underline">Contradictions</a>
        </div>
      )}
      <div className="mt-2 grid gap-2 rounded border border-slate-200 p-2 text-xs sm:grid-cols-2">
        <div className="flex items-start gap-2">
          <svg width="30" height="14" className="mt-0.5 shrink-0"><line x1="0" y1="7" x2="30" y2="7" stroke="var(--color-slate-700)" strokeWidth="2.5" /><circle cx="15" cy="7" r="3.5" fill="var(--color-slate-700)" stroke="var(--color-white)" /></svg>
          <span><b className="text-emerald-700">Observed</b>
            <span className="text-slate-500"> · read from a sensor sample or tracked in video. Actual path: solid line, filled dot, solid vehicle.</span></span>
        </div>
        <div className="flex items-start gap-2">
          <svg width="30" height="14" className="mt-0.5 shrink-0"><line x1="0" y1="7" x2="30" y2="7" stroke="var(--color-slate-700)" strokeWidth="3" strokeLinecap="round" strokeDasharray="0 6" /><circle cx="15" cy="7" r="3.5" fill="var(--color-white)" stroke="var(--color-slate-700)" strokeWidth="1.5" /></svg>
          <span><b className="text-violet-700">Inferred</b>
            <span className="text-slate-500"> · filled in between or beyond samples, or assumed. Dotted line, hollow dot, faded vehicle. The faint dotted line ahead of a vehicle is the path it has still to take.</span></span>
        </div>
        {data.collision && (
          <div className="flex items-start gap-2 sm:col-span-2">
            <svg width="30" height="14" className="mt-0.5 shrink-0"><circle cx="15" cy="7" r="6" fill="#fecaca" fillOpacity="0.5" stroke="#dc2626" strokeDasharray="3 2" /></svg>
            <span><b className="text-red-600">Inferred collision</b>
              <span className="text-slate-500"> · t={data.collision.t_s}s, confidence {Math.round(data.collision.confidence * 100)}%. Placed where the tracks come closest; it was not witnessed or recorded as an event.</span></span>
          </div>
        )}
      </div>
      {signalNow.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-3 text-xs text-slate-600">
          {signalNow.map(([appr, st]) => (
            <span key={appr} className="flex items-center gap-1">
              <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: SIGNAL_COLORS[st] }} />
              {APPROACHES[appr].name} {st}
            </span>
          ))}
        </div>
      )}
      </div>
      <ReconstructionTrace data={data} time={time} selected={selected} onSelect={setSelected} position={selectedPos}
        moments={moments} observationsById={observationsById} onSeek={seek} schematic={schematic} onFocus={onFocus} />
      </div>
    </Panel>
  )
}

// First track point at or after t: the end of the grid segment the vehicle is currently in.
function nextPoint(track, t) {
  return track.find((q) => q.t_s >= t) ?? track[track.length - 1]
}
