export default function Panel({ title, actions, children }) {
  return (
    <section className="flex h-full flex-col rounded-lg border border-slate-200 bg-white shadow-sm">
      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
        <h2 className="text-sm font-semibold text-slate-700">{title}</h2>
        {actions}
      </div>
      <div className="flex-1 p-4">{children}</div>
    </section>
  )
}

export const SOURCE_STYLES = {
  video: 'bg-sky-100 text-sky-800',
  witness: 'bg-amber-100 text-amber-800',
  sensor: 'bg-emerald-100 text-emerald-800',
}

export function SourceBadge({ type }) {
  return <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase ${SOURCE_STYLES[type] ?? 'bg-slate-100'}`}>{type}</span>
}

// Observation vs inference (problem statement requirement 5).
const BASIS = {
  measured: ['bg-emerald-50 text-emerald-700 ring-emerald-200', 'Read directly from a sensor or log'],
  reported: ['bg-amber-50 text-amber-700 ring-amber-200', "A witness's statement"],
  derived: ['bg-sky-50 text-sky-700 ring-sky-200', 'Computed by fixed rules (tracking, calibration, fusion)'],
  inferred: ['bg-violet-50 text-violet-700 ring-violet-200', "An AI model's reading: verify"],
}

export function BasisBadge({ basis }) {
  if (!basis || !BASIS[basis]) return null
  const [cls, title] = BASIS[basis]
  return <span title={title} className={`rounded px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset ${cls}`}>{basis}</span>
}
