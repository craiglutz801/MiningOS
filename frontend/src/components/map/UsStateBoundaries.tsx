import { useEffect, useState } from "react";
import { GeoJSON } from "react-leaflet";
import type { GeoJsonObject } from "geojson";
import { MAP_PANES } from "../../map/panes";

const GEO_URL = "/geo/us-states.json";

/** Bold outline + dark halo so state lines read clearly on satellite imagery. */
const STYLE_HALO = {
  color: "#0f172a",
  weight: 5,
  opacity: 0.92,
  fillOpacity: 0,
  lineCap: "round" as const,
  lineJoin: "round" as const,
};

const STYLE_LINE = {
  color: "#fef08a",
  weight: 2.5,
  opacity: 1,
  fillOpacity: 0,
  lineCap: "round" as const,
  lineJoin: "round" as const,
};

export function UsStateBoundaries() {
  const [data, setData] = useState<GeoJsonObject | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(GEO_URL)
      .then((r) => {
        if (!r.ok) throw new Error(String(r.status));
        return r.json();
      })
      .then((json: GeoJsonObject) => {
        if (!cancelled) setData(json);
      })
      .catch(() => {
        if (!cancelled) setData(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!data) return null;

  return (
    <>
      <GeoJSON
        key="state-halo"
        data={data}
        pane={MAP_PANES.stateOutlines}
        style={() => STYLE_HALO}
        interactive={false}
      />
      <GeoJSON
        key="state-line"
        data={data}
        pane={MAP_PANES.stateOutlines}
        style={() => STYLE_LINE}
        interactive={false}
      />
    </>
  );
}
