import { useSearchParams } from "react-router-dom";
import { TargetMap } from "../components/map/TargetMap";

export function MapPage() {
  const [searchParams] = useSearchParams();
  const selectedAreaId = searchParams.get("areaId");

  return <TargetMap selectedAreaId={selectedAreaId} />;
}
