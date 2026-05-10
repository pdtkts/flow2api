import { AdobeConfigurations } from "./AdobeConfigurations"
import { TasTracker } from "./TasTracker"

export function AdobeSettings({ active }: { active: boolean }) {
  if (!active) return null

  return (
    <div className="space-y-6">
      <AdobeConfigurations active={active} />
      <div className="grid gap-6 lg:grid-cols-2">
        <TasTracker active={active} />
      </div>
    </div>
  )
}
