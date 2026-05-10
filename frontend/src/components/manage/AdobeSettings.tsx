import { MetadataSettings } from "./MetadataSettings"
import { TaskTrackerSettings } from "./TaskTrackerSettings"
import { EventCalendarSettings } from "./EventCalendarSettings"

export function AdobeSettings({ active }: { active: boolean }) {
  if (!active) return null

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <MetadataSettings active={active} />
      <TaskTrackerSettings active={active} />
      <EventCalendarSettings active={active} />
    </div>
  )
}
