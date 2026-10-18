import { Badge } from "./ui";
import { titleCase } from "../lib/format";
import { STATE_LABEL, isDeviceState, toneFor } from "../lib/states";

/** One component for every state in the product: devices, jobs, executions and steps. */
export function StateBadge({ state }: { state: string }) {
  const label = isDeviceState(state) ? STATE_LABEL[state] : titleCase(state);
  return <Badge tone={toneFor(state)}>{label}</Badge>;
}
