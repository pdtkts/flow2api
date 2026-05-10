import { MetadataSettings } from "./MetadataSettings"
import { MarketSettings } from "./MarketSettings"
import { CloningSettings } from "./CloningSettings"
import { TaskTrackerSettings } from "./TaskTrackerSettings"
import { EventCalendarSettings } from "./EventCalendarSettings"

export function AdobeSettings({ active }: { active: boolean }) {
  if (!active) return null

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <MetadataSettings active={active} />
      <MarketSettings active={active} />
      <CloningSettings active={active} />
      <TaskTrackerSettings active={active} />
      <EventCalendarSettings active={active} />
    </div>
  )
}
