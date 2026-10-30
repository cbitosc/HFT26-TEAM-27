import CameraTrajectories from './CameraTrajectories'
import Panel from './Panel'

// Module 1 output on the footage itself: detections and image-space tracks over the video frames.
// It shows what the camera saw; the top-down Reconstruction (bottom) shows where vehicles were.
export default function EventAnalysis({ data, time, onTimeChange }) {
  if (!data) {
    return (
      <Panel title="Event Analysis">
        <div className="flex h-full min-h-48 flex-col items-center justify-center gap-1 text-center">
          <p className="text-sm text-slate-600">No video analysis yet.</p>
          <p className="max-w-sm text-xs text-slate-400">
            Upload dashcam or traffic-camera footage and run the analysis: detected vehicles and their tracks are
            drawn over the actual frames here.
          </p>
        </div>
      </Panel>
    )
  }
  return <CameraTrajectories data={data} time={time} onTimeChange={onTimeChange} Panel={Panel} title="Event Analysis" />
}
