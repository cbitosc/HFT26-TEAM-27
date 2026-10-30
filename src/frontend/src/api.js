// Thin wrapper over the FastAPI endpoints. Shapes are defined in
// src/backend/app/schemas.py (the integration contract).
async function request(path, options) {
  const res = await fetch(`/api${path}`, options)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `${res.status} ${res.statusText}`)
  }
  return res.status === 204 ? null : res.json()
}

export const api = {
  listEvidence: () => request('/evidence'),
  uploadEvidence: (file) => {
    const form = new FormData()
    form.append('file', file)
    return request('/evidence', { method: 'POST', body: form })
  },
  deleteEvidence: (id) => request(`/evidence/${id}`, { method: 'DELETE' }),
  runAnalysis: () => request('/analysis/run', { method: 'POST' }),
  startJob: () => request('/analysis/jobs', { method: 'POST' }),
  currentJob: () => request('/analysis/jobs/current'),
  latestRun: () => request('/analysis/latest'),
  observations: () => request('/observations'),
  timeline: () => request('/timeline'),
  conflicts: () => request('/conflicts'),
  reconstruction: () => request('/reconstruction'),
}

// Thumbnail URL for a `frame:<n>` source ref of an uploaded video, or null.
export function frameUrl(observation, width = 320) {
  const m = /^frame:(\d+)$/.exec(observation?.source_ref?.locator ?? '')
  if (!m || observation.source_type !== 'video' || observation.source_ref.evidence_id.startsWith('ev_fx_')) return null
  return `/api/evidence/${observation.source_ref.evidence_id}/frames/${m[1]}?width=${width}`
}
