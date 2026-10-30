import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import CameraTrajectories from './CameraTrajectories'
import Panel from './Panel'

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

export default function ReconstructionViewer({ data, time, onTimeChange }) {
  const [playing, setPlaying] = useState(false)
  const [zoom, setZoom] = useState('auto')
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
      const next = timeRef.current + (now - last) / 1000
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

  if (!data) return <Panel title="Reconstruction Viewer">Loading…</Panel>
  if (data.scene.type === 'camera_image') {
    return <CameraTrajectories data={data} time={time} onTimeChange={onTimeChange} Panel={Panel} />
  }
  if (data.vehicles.length === 0) {
    return (
      <Panel title="Reconstruction Viewer">
        <div className="flex h-full min-h-48 flex-col items-center justify-center gap-1 text-center">
          <p className="text-sm text-slate-600">No vehicle trajectories in this evidence.</p>
          <p className="max-w-sm text-xs text-slate-400">
            Trajectories come from sensor tracks with positions (x/y). Video and witness findings appear in the
            timeline and contradictions instead.
          </p>
        </div>
      </Panel>
    )
  }

  const { extent_m: sceneE, lane_width_m: L, roads } = data.scene
  const R = 1.5 * sceneE // roads run past the scene edge so the auto view never shows them ending
  const current = data.vehicles.map((v) => ({ v, p: positionAt(v.track, time) }))
  const E = zoom === 'auto'
    ? Math.min(1.5 * sceneE, Math.max(ZOOMS.junction, ...current.map(({ p }) => Math.max(Math.abs(p.x_m), Math.abs(p.y_m)) + 8)))
    : ZOOMS[zoom] ?? sceneE
  const s = E / 60 // scale strokes and text with the view
  const fs = 2.2 * s
  const half = L // two lanes per road -> half-width = one lane width each side
  const showCollision = data.collision && time >= data.collision.t_s - 1e-6

  const isMeasured = (vid, t) => !derived.known || derived.measured[vid].some((m) => Math.abs(m - t) < 0.01)

  const togglePlay = () => {
    if (!playing && time >= data.t_max_s) onTimeChange(data.t_min_s)
    setPlaying(!playing)
  }

  const labels = layoutLabels([
    ...current.map(({ v, p }) => ({
      key: v.vehicle_id, x: p.x_m, y: -p.y_m, color: '#0f172a',
      text: `${v.vehicle_id}${p.speed_kmh != null ? ` · ${p.speed_kmh.toFixed(0)} km/h` : ''}`,
    })),
    ...(showCollision ? [{ key: 'collision', x: data.collision.x_m, y: -data.collision.y_m + 2 * fs, color: '#b91c1c', side: 'left',
      text: `impact t=${data.collision.t_s.toFixed(1)}s` }] : []),
  ], fs)

  const signalNow = Object.entries(derived.signals).map(([appr, phases]) => [appr, signalAt(phases, time)]).filter(([, st]) => st)

  return (
    <Panel title="Reconstruction Viewer"
      actions={
        <div className="flex items-center gap-2 text-xs">
          <span className="text-slate-500">predefined intersection · metres</span>
          <div className="flex overflow-hidden rounded border border-slate-200">
            {Object.keys(ZOOMS).map((z) => (
              <button key={z} onClick={() => setZoom(z)}
                className={`px-2 py-0.5 capitalize ${zoom === z ? 'bg-slate-900 text-white' : 'bg-white text-slate-600'}`}>{z}</button>
            ))}
          </div>
        </div>
      }>
      <svg viewBox={`${-E} ${-E} ${2 * E} ${2 * E}`} className="mx-auto block aspect-square w-full max-w-xl rounded bg-lime-50">
        {/* World coordinates: +y is north, so flip the y axis. */}
        <g transform="scale(1,-1)">
          <rect x={-R} y={-half} width={2 * R} height={2 * half} fill="#cbd5e1" />
          <rect x={-half} y={-R} width={2 * half} height={2 * R} fill="#cbd5e1" />
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

          {showCollision && (
            <g transform={`translate(${data.collision.x_m},${data.collision.y_m})`} opacity={0.85}>
              <circle r={2.2} fill="#fecaca" fillOpacity={0.5} stroke="#dc2626" strokeWidth={0.2} />
              <path d="M-1,-1 L1,1 M-1,1 L1,-1" stroke="#dc2626" strokeWidth={0.2} />
            </g>
          )}

          {current.map(({ v, p }) => {
            const past = v.track.filter((q) => q.t_s < time)
            const pts = [...past, p]
            return (
              <g key={v.vehicle_id}>
                <polyline points={v.track.map((q) => `${q.x_m},${q.y_m}`).join(' ')}
                  fill="none" stroke={v.color} strokeWidth={0.25 * Math.max(s, 0.4)} strokeOpacity={0.15} />
                {pts.slice(1).map((b, i) => {
                  const a = pts[i]
                  // the last segment ends at the vehicle, inside the grid step that ends at nextGridTime
                  const end = b === p ? nextGridTime(v.track, time) : b.t_s
                  const solid = isMeasured(v.vehicle_id, a.t_s) && isMeasured(v.vehicle_id, end)
                  return (
                    <line key={i} x1={a.x_m} y1={a.y_m} x2={b.x_m} y2={b.y_m} stroke={v.color}
                      strokeWidth={0.5 * Math.max(s, 0.4)} strokeOpacity={0.7}
                      strokeDasharray={solid ? undefined : `${1.2 * s} ${0.9 * s}`} />
                  )
                })}
                {derived.known && past.filter((q) => isMeasured(v.vehicle_id, q.t_s)).map((q) => (
                  <circle key={q.t_s} cx={q.x_m} cy={q.y_m} r={0.5 * Math.max(s, 0.4)} fill={v.color} stroke="white" strokeWidth={0.1} />
                ))}
                <g transform={`translate(${p.x_m},${p.y_m}) rotate(${-p.heading_deg})`}>
                  <rect x={-0.9} y={-2.25} width={1.8} height={4.5} rx={0.4} fill={v.color} stroke="#0f172a" strokeWidth={0.15} />
                  <rect x={-0.7} y={0.8} width={1.4} height={0.9} fill="#e2e8f0" />
                </g>
              </g>
            )
          })}

        </g>

        <text x={half + 1 * s} y={-E + 4 * s} fontSize={2.5 * s} fill="#475569">{roads?.ns}</text>
        <text x={-E + 1 * s} y={half + 3 * s} fontSize={2.5 * s} fill="#475569">{roads?.ew}</text>
        <text x={-E + 1 * s} y={E - 2 * s} fontSize={2.5 * s} fill="#475569">N ↑</text>
        {labels.map((l) => (
          <g key={l.key}>
            <line x1={l.ax} y1={l.ay} x2={l.side === 'left' ? l.x + l.w + 0.3 * fs : l.x - 0.3 * fs} y2={l.y - l.h * 0.45}
              stroke="#64748b" strokeWidth={0.12 * Math.max(s, 0.4)} />
            <rect x={l.x - 0.3 * fs} y={l.y - l.h * 0.9} width={l.w + 0.6 * fs} height={l.h} rx={0.3 * fs}
              fill="white" fillOpacity={0.85} />
            <text x={l.x} y={l.y - l.h * 0.2} fontSize={fs} fontWeight="600" fill={l.color}>{l.text}</text>
          </g>
        ))}
      </svg>

      <div className="mt-3 flex items-center gap-3">
        <button onClick={togglePlay} className="w-16 rounded bg-slate-900 px-2 py-1 text-xs font-medium text-white">
          {playing ? 'Pause' : 'Play'}
        </button>
        <input type="range" min={data.t_min_s} max={data.t_max_s} step={0.1} value={time}
          onChange={(e) => { setPlaying(false); onTimeChange(Number(e.target.value)) }} className="flex-1" />
        <span className="w-16 text-right font-mono text-xs">t={time.toFixed(1)}s</span>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-600">
        {data.vehicles.map((v) => (
          <span key={v.vehicle_id} className="flex items-center gap-1">
            <span className="inline-block h-2 w-3 rounded-sm" style={{ background: v.color }} />{v.label}
          </span>
        ))}
        {derived.known && (
          <span className="flex items-center gap-2 text-slate-500">
            <svg width="22" height="6"><line x1="0" y1="3" x2="22" y2="3" stroke="#475569" strokeWidth="2" /></svg>measured
            <svg width="22" height="6"><line x1="0" y1="3" x2="22" y2="3" stroke="#475569" strokeWidth="2" strokeDasharray="4 3" /></svg>interpolated
          </span>
        )}
        {data.collision && <span className="text-red-600">✕ collision at t={data.collision.t_s}s (conf {data.collision.confidence})</span>}
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
    </Panel>
  )
}

// First track time at or after t: the end of the grid segment the vehicle is currently in.
function nextGridTime(track, t) {
  return (track.find((q) => q.t_s >= t) ?? track[track.length - 1]).t_s
}
