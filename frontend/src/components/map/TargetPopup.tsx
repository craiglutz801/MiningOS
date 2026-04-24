import { Link } from "react-router-dom";
import type { MapTarget } from "../../map/useTargetsLayer";
import { getTargetStatusLabel } from "../../map/targetStyles";

interface TargetPopupProps {
  target: MapTarget;
}

export function TargetPopup({ target }: TargetPopupProps) {
  const statusLabel = getTargetStatusLabel(target.priority);
  const minerals = target.minerals.length > 0 ? target.minerals.join(", ") : "—";

  return (
    <div style={{ fontFamily: "inherit", minWidth: 180 }}>
      <div style={{ fontWeight: 600, color: "#0f172a", marginBottom: 4, fontSize: 14 }}>
        {target.name}
      </div>
      <div style={{ color: "#475569", fontSize: 13 }}>{minerals}</div>
      {target.plss && (
        <div style={{ color: "#64748b", fontSize: 12, marginTop: 2 }}>{target.plss}</div>
      )}
      <div style={{ color: "#64748b", fontSize: 12, marginTop: 4 }}>
        Claim: {target.status} &middot; {statusLabel}
      </div>
      {target.claimType && (
        <div style={{ color: "#64748b", fontSize: 12 }}>Type: {target.claimType}</div>
      )}
      <Link
        to={`/areas?areaId=${target.id}`}
        style={{
          display: "inline-block",
          marginTop: 8,
          color: "#2563eb",
          fontWeight: 500,
          fontSize: 13,
          textDecoration: "none",
        }}
      >
        View target →
      </Link>
    </div>
  );
}
