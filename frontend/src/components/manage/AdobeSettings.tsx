import { MetadataSettings } from "./MetadataSettings"
import { TasTracker } from "./TasTracker"
import { EventCalendarSettings } from "./EventCalendarSettings"

export function AdobeSettings({ active }: { active: boolean }) {
  if (!active) return null

  return (
    <div className="space-y-6">
      <MetadataSettings active={active} />
      <div className="grid gap-6 lg:grid-cols-2">
        <TasTracker active={active} />
        <EventCalendarSettings active={active} />
      </div>
    </div>
  )
}
