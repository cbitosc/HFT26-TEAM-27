import { frameUrl } from '../api'
import { BasisBadge, SourceBadge } from './Panel'

// The evidence behind an event or conflict: source, where it came from, what it says,
// and the actual video frame when there is one.
export default function ObservationList({ ids, observationsById }) {
  return (
    <ul className="space-y-2">
      {ids.map((id) => {
        const o = observationsById[id]
        if (!o) return <li key={id} className="text-xs text-slate-400"><code>{id}</code> not in current results</li>
        const thumb = frameUrl(o)
        const what = [o.vehicle_description, o.action.replaceAll('_', ' ')].filter(Boolean).join(' · ')
        const extra = Object.entries(o.attributes)
          .filter(([k, v]) => !['camera', 'label', 'summary'].includes(k) && typeof v !== 'object')
          .map(([k, v]) => `${k.replaceAll('_', ' ')}: ${v}`)
        return (
          <li key={id} className="flex min-w-0 gap-2 text-xs">
            {thumb && (
              <a href={thumb.replace(/width=\d+/, 'width=1280')} target="_blank" rel="noreferrer" className="shrink-0">
                <img src={thumb} alt={`frame ${o.source_ref.locator}`} loading="lazy"
                  className="h-16 w-28 rounded border border-slate-200 object-cover" />
              </a>
            )}
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-1.5">
                <SourceBadge type={o.source_type} />
                <BasisBadge basis={o.basis} />
                <span className="font-medium text-slate-700">{what}</span>
                {o.t_rel_s != null && <span className="text-slate-400">t={o.t_rel_s}s</span>}
                <span className="text-slate-400">conf {Math.round(o.confidence * 100)}%</span>
              </div>
              <div className="truncate text-slate-400" title={`${o.source_ref.file} @ ${o.source_ref.locator}`}>
                {o.evidence_ref ? <><span className="font-mono text-slate-500">{o.evidence_ref}</span> · {o.source_ref.file}</> : <>{o.source_ref.file} @ {o.source_ref.locator}</>}
              </div>
              {o.raw_text && <p className="italic text-slate-600">“{o.raw_text}”</p>}
              {extra.length > 0 && <p className="text-slate-500">{extra.join(' · ')}</p>}
            </div>
          </li>
        )
      })}
    </ul>
  )
}
