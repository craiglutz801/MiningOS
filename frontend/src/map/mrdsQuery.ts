/**
 * USGS MRDS “compact” layer on ArcGIS Online (replaces flaky mrdata WMS for the map).
 * @see https://mrdata.usgs.gov/mrds/
 */

const MRDS_QUERY_URL =
  "https://services.arcgis.com/v01gqwM5QqNysAAi/arcgis/rest/services/Mineral_Resources_Data_System_MRDS_Compact_Version/FeatureServer/0/query";

export interface MrdsSiteProps {
  SITE_NAME?: string;
  DEV_STAT?: string;
  CODE_LIST?: string;
  Grade?: string;
  URL?: string;
  DEP_ID?: string;
}

export type MrdsFeature = GeoJSON.Feature<GeoJSON.Point, MrdsSiteProps>;

export async function fetchMrdsInBounds(
  west: number,
  south: number,
  east: number,
  north: number,
  signal?: AbortSignal,
): Promise<GeoJSON.FeatureCollection> {
  const params = new URLSearchParams({
    f: "geojson",
    where: "1=1",
    geometry: `${west},${south},${east},${north}`,
    geometryType: "esriGeometryEnvelope",
    inSR: "4326",
    spatialRel: "esriSpatialRelIntersects",
    outFields: "SITE_NAME,DEV_STAT,CODE_LIST,Grade,URL,DEP_ID",
    returnGeometry: "true",
    outSR: "4326",
    resultRecordCount: "2000",
  });

  const res = await fetch(`${MRDS_QUERY_URL}?${params.toString()}`, { signal });
  if (!res.ok) {
    throw new Error(`MRDS request failed (${res.status})`);
  }
  const data = (await res.json()) as GeoJSON.FeatureCollection;
  if (!data || data.type !== "FeatureCollection" || !Array.isArray(data.features)) {
    throw new Error("Invalid MRDS response");
  }
  return data;
}
