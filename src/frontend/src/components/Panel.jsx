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
