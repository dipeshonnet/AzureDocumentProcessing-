import { ShieldCheck } from "lucide-react";

export function DecisionSupportBanner() {
  return (
    <div className="decision-support-banner" role="note">
      <ShieldCheck size={18} aria-hidden="true" />
      <strong>Decision support only</strong>
      <span>AI summaries and rubric scores must be verified by an authorized human reviewer.</span>
    </div>
  );
}
