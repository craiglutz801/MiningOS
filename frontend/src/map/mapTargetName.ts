/** Map UI only: keep hover labels and popups readable. Target detail pages use full `name`. */
const MAP_TARGET_NAME_MAX = 40;

export function truncateMapTargetName(name: string): string {
  if (name.length <= MAP_TARGET_NAME_MAX) return name;
  return `${name.slice(0, MAP_TARGET_NAME_MAX)}...`;
}
