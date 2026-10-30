import { useEffect, useRef, useState } from 'react'
import Panel from './Panel'

// Linear interpolation of a vehicle's track at time t.
function positionAt(track, t) {
  if (t <= track[0].t_s) return track[0]
  for (let i = 1; i < track.length; i++) {
    const a = track[i - 1]
    const b = track[i]
    if (t <= b.t_s) {
      const k = (t - a.t_s) / (b.t_s - a.t_s)
      return {
        t_s: t,
        x_m: a.x_m + (b.x_m - a.x_m) * k,
        y_m: a.y_m + (b.y_m - a.y_m) * k,
        heading_deg: a.heading_deg + (b.heading_deg - a.heading_deg) * k,
        speed_kmh: a.speed_kmh != null && b.speed_kmh != null ? a.speed_kmh + (b.speed_kmh - a.speed_kmh) * k : null,
      }
    }
  }
  return track[track.length - 1]
}

export default function ReconstructionViewer({ data, time, onTimeChange }) {
  const [playing, setPlaying] = useState(false)
  const timeRef = useRef(time)
  useEffect(() => { timeRef.current = time }, [time])

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

  if (!data) return <Panel title="Reconstruction Viewer">Loading…</Panel>

  const { extent_m: E, lane_width_m: L, roads } = data.scene
  const half = L // two lanes per road -> half-width = one lane width each side
  const showCollision = data.collision && time >= data.collision.t_s

  const togglePlay = () => {
    if (!playing && time >= data.t_max_s) onTimeChange(data.t_min_s)
    setPlaying(!playing)
  }

  return (
    <Panel title="Reconstruction Viewer"
      actions={<span className="text-xs text-slate-500">predefined intersection · metres</span>}>
      <svg viewBox={`${-E} ${-E} ${2 * E} ${2 * E}`} className="mx-auto block aspect-square w-full max-w-xl rounded bg-lime-50">
        {/* World coordinates: +y is north, so flip the y axis. */}
        <g transform="scale(1,-1)">
          <rect x={-E} y={-half} width={2 * E} height={2 * half} fill="#cbd5e1" />
          <rect x={-half} y={-E} width={2 * half} height={2 * E} fill="#cbd5e1" />
          <line x1={-E} y1={0} x2={-half} y2={0} stroke="white" strokeWidth={0.2} strokeDasharray="2 2" />
          <line x1={half} y1={0} x2={E} y2={0} stroke="white" strokeWidth={0.2} strokeDasharray="2 2" />
          <line x1={0} y1={-E} x2={0} y2={-half} stroke="white" strokeWidth={0.2} strokeDasharray="2 2" />
          <line x1={0} y1={half} x2={0} y2={E} stroke="white" strokeWidth={0.2} strokeDasharray="2 2" />

          {data.vehicles.map((v) => {
            const past = v.track.filter((p) => p.t_s <= time)
            const p = positionAt(v.track, time)
            return (
              <g key={v.vehicle_id}>
                <polyline points={[...past, p].map((q) => `${q.x_m},${q.y_m}`).join(' ')}
                  fill="none" stroke={v.color} strokeWidth={0.4} strokeOpacity={0.5} />
                <g transform={`translate(${p.x_m},${p.y_m}) rotate(${-p.heading_deg})`}>
                  <rect x={-0.9} y={-2.25} width={1.8} height={4.5} rx={0.4} fill={v.color} stroke="#0f172a" strokeWidth={0.15} />
                  <rect x={-0.7} y={0.8} width={1.4} height={0.9} fill="#e2e8f0" />
                </g>
              </g>
            )
          })}

          {showCollision && (
            <g transform={`translate(${data.collision.x_m},${data.collision.y_m})`}>
              <circle r={3} fill="none" stroke="#dc2626" strokeWidth={0.4} />
              <path d="M-2,-2 L2,2 M-2,2 L2,-2" stroke="#dc2626" strokeWidth={0.4} />
            </g>
          )}
        </g>

        <text x={half + 1} y={-E + 4} fontSize={2.5} fill="#475569">{roads?.ns}</text>
        <text x={-E + 1} y={-half - 1} fontSize={2.5} fill="#475569">{roads?.ew}</text>
        <text x={-E + 1} y={E - 2} fontSize={2.5} fill="#475569">N ↑</text>
        {data.vehicles.map((v) => {
          const p = positionAt(v.track, time)
          return (
            <text key={v.vehicle_id} x={p.x_m + 2.5} y={-p.y_m - 2.5} fontSize={2.2} fontWeight="600" fill="#0f172a">
              {v.vehicle_id}{p.speed_kmh != null && ` · ${p.speed_kmh.toFixed(0)} km/h`}
            </text>
          )
        })}
      </svg>

      <div className="mt-3 flex items-center gap-3">
        <button onClick={togglePlay} className="w-16 rounded bg-slate-900 px-2 py-1 text-xs font-medium text-white">
          {playing ? 'Pause' : 'Play'}
        </button>
        <input type="range" min={data.t_min_s} max={data.t_max_s} step={0.1} value={time}
          onChange={(e) => { setPlaying(false); onTimeChange(Number(e.target.value)) }} className="flex-1" />
        <span className="w-16 text-right font-mono text-xs">t={time.toFixed(1)}s</span>
      </div>
      <div className="mt-2 flex flex-wrap gap-3 text-xs text-slate-600">
        {data.vehicles.map((v) => (
          <span key={v.vehicle_id} className="flex items-center gap-1">
            <span className="inline-block h-2 w-3 rounded-sm" style={{ background: v.color }} />{v.label}
          </span>
        ))}
        {data.collision && <span className="text-red-600">✕ collision at t={data.collision.t_s}s (conf {data.collision.confidence})</span>}
      </div>
    </Panel>
  )
}
