"""Reusable ArcGIS REST client with retries, Object ID batching, and item resolution."""

from __future__ import annotations

import random
import time
from typing import Any

import requests

from mining_os.active_mine_intel.matcher.logging_setup import get_logger
from mining_os.active_mine_intel.matcher.models import SourceSchemaError, SourceUnavailableError
from mining_os.active_mine_intel.matcher.source_registry import LAYER_PROFILES

log = get_logger("mcm.arcgis")

USER_AGENT = "MineClaimMatcher/0.1 (research tool; local use)"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
ARCGIS_SHARING_ROOT = "https://www.arcgis.com/sharing/rest/content/items"


def resolve_field(
    available_fields: list[dict],
    candidates: list[str],
    alias_keywords: list[str] | None = None,
) -> str | None:
    """Find the actual field name matching any candidate name or alias keyword.

    Comparison is case-insensitive and ignores underscores/spaces.
    """

    def norm(value: str) -> str:
        return value.lower().replace("_", "").replace(" ", "")

    normalized_candidates = [norm(c) for c in candidates]
    # Pass 1: exact normalized name match.
    for f in available_fields:
        name = f.get("name") or ""
        if norm(name) in normalized_candidates:
            return name
    # Pass 2: exact normalized alias match.
    for f in available_fields:
        alias = f.get("alias") or ""
        if alias and norm(alias) in normalized_candidates:
            return f.get("name")
    # Pass 3: keyword containment in name or alias.
    if alias_keywords:
        keys = [norm(k) for k in alias_keywords]
        for f in available_fields:
            haystack = norm((f.get("name") or "") + (f.get("alias") or ""))
            if any(k in haystack for k in keys):
                return f.get("name")
    return None


class ArcGISClient:
    def __init__(
        self,
        connect_timeout: float = 10.0,
        read_timeout: float = 120.0,
        max_retries: int = 4,
        session: requests.Session | None = None,
    ) -> None:
        self.timeout = (connect_timeout, read_timeout)
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    # ------------------------------------------------------------------ HTTP

    def get_json(self, url: str, params: dict | None = None, method: str = "get") -> dict:
        """Fetch JSON with retries. Use method="post" for long query payloads
        (e.g. objectIds batches) that would exceed URL length limits as GET."""
        params = dict(params or {})
        params.setdefault("f", "json")
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                if method == "post":
                    response = self.session.post(url, data=params, timeout=self.timeout)
                else:
                    response = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                # DNS failures will not recover between retries; fail fast so
                # item discovery does not stall on unreachable reference hosts.
                if "Failed to resolve" in str(exc) or "Name or service not known" in str(exc):
                    raise SourceUnavailableError(f"DNS resolution failed for {url}") from exc
                last_error = exc
                self._sleep(attempt, None)
                continue
            if response.status_code in RETRYABLE_STATUS:
                last_error = SourceUnavailableError(
                    f"HTTP {response.status_code} from {url}"
                )
                self._sleep(attempt, response.headers.get("Retry-After"))
                continue
            if response.status_code != 200:
                raise SourceUnavailableError(
                    f"HTTP {response.status_code} from {url}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise SourceSchemaError(f"Non-JSON response from {url}") from exc
            self._raise_on_arcgis_error(payload, url)
            return payload
        raise SourceUnavailableError(
            f"Failed to fetch {url} after {self.max_retries + 1} attempts: {last_error}"
        )

    def _sleep(self, attempt: int, retry_after: str | None) -> None:
        if retry_after:
            try:
                time.sleep(min(float(retry_after), 60.0))
                return
            except ValueError:
                pass
        time.sleep(min(2**attempt + random.uniform(0, 1), 30.0))

    @staticmethod
    def _raise_on_arcgis_error(payload: Any, url: str) -> None:
        if isinstance(payload, dict) and "error" in payload:
            error = payload["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            code = error.get("code") if isinstance(error, dict) else "?"
            raise SourceUnavailableError(
                f"ArcGIS error payload from {url}: code={code} message={message}"
            )

    # ----------------------------------------------------------------- Layers

    def get_layer_metadata(self, layer_url: str) -> dict:
        return self.get_json(layer_url)

    def get_object_ids(self, layer_url: str, where: str) -> list[int]:
        payload = self.get_json(
            f"{layer_url}/query",
            {"where": where, "returnIdsOnly": "true"},
        )
        ids = payload.get("objectIds") or []
        log.info("Layer %s: %d object IDs for where=%r", layer_url, len(ids), where)
        return list(ids)

    def fetch_features_geojson(
        self,
        layer_url: str,
        where: str = "1=1",
        out_fields: str = "*",
        batch_size: int = 500,
    ) -> dict:
        """Fetch all features via Object ID batching; returns a GeoJSON FeatureCollection.

        Never assumes a single request returns everything (services cap at ~2,000
        records). Features are de-duplicated by object ID.
        """
        object_ids = self.get_object_ids(layer_url, where)
        if not object_ids:
            return {"type": "FeatureCollection", "features": []}

        id_field = self._object_id_field(layer_url, where)
        features: list[dict] = []
        seen: set[Any] = set()
        for start in range(0, len(object_ids), batch_size):
            batch = object_ids[start : start + batch_size]
            payload = self.get_json(
                f"{layer_url}/query",
                {
                    "objectIds": ",".join(str(i) for i in batch),
                    "outFields": out_fields,
                    "returnGeometry": "true",
                    "outSR": "4326",
                    "f": "geojson",
                },
                method="post",
            )
            batch_features = payload.get("features", [])
            for feature in batch_features:
                props = feature.get("properties") or {}
                oid = feature.get("id")
                if oid is None and id_field:
                    oid = props.get(id_field)
                key = oid if oid is not None else id(feature)
                if key in seen:
                    continue
                seen.add(key)
                features.append(feature)
            log.info(
                "Fetched batch %d-%d of %d from %s",
                start + 1,
                start + len(batch),
                len(object_ids),
                layer_url,
            )
        return {"type": "FeatureCollection", "features": features}

    def _object_id_field(self, layer_url: str, where: str) -> str | None:
        try:
            payload = self.get_json(
                f"{layer_url}/query", {"where": where, "returnIdsOnly": "true"}
            )
            return payload.get("objectIdFieldName")
        except Exception:  # noqa: BLE001 - best effort only
            return None

    # ------------------------------------------------------- Item resolution

    def resolve_item_layers(self, item_id: str, max_depth: int = 5) -> list[dict]:
        """Recursively resolve an ArcGIS item into candidate feature-layer descriptors.

        Returns a list of dicts: {"url", "title", "fields", "geometry_type"}.
        """
        candidates: list[dict] = []
        visited: set[str] = set()
        self._resolve_item(item_id, candidates, visited, depth=0, max_depth=max_depth)
        # De-duplicate by URL.
        unique: dict[str, dict] = {}
        for candidate in candidates:
            unique.setdefault(candidate["url"], candidate)
        return list(unique.values())

    def _resolve_item(
        self,
        item_id: str,
        candidates: list[dict],
        visited: set[str],
        depth: int,
        max_depth: int,
    ) -> None:
        if depth > max_depth or item_id in visited:
            return
        visited.add(item_id)
        try:
            meta = self.get_json(f"{ARCGIS_SHARING_ROOT}/{item_id}")
        except (SourceUnavailableError, SourceSchemaError) as exc:
            log.warning("Could not fetch item metadata %s: %s", item_id, exc)
            return
        title = meta.get("title") or ""
        url = meta.get("url") or ""
        if url:
            self._collect_service_layers(url, title, candidates)
        try:
            data = self.get_json(f"{ARCGIS_SHARING_ROOT}/{item_id}/data")
        except (SourceUnavailableError, SourceSchemaError):
            data = None
        if data:
            self._walk_json(data, candidates, visited, depth, max_depth, title)

    def _walk_json(
        self,
        node: Any,
        candidates: list[dict],
        visited: set[str],
        depth: int,
        max_depth: int,
        context_title: str,
    ) -> None:
        if isinstance(node, dict):
            url = node.get("url")
            title = node.get("title") or node.get("name") or context_title
            if isinstance(url, str) and (
                "FeatureServer" in url or "MapServer" in url
            ):
                self._collect_service_layers(url, title, candidates)
            ref_item = node.get("itemId")
            if isinstance(ref_item, str) and len(ref_item) >= 16:
                self._resolve_item(ref_item, candidates, visited, depth + 1, max_depth)
            for value in node.values():
                self._walk_json(value, candidates, visited, depth, max_depth, title)
        elif isinstance(node, list):
            for value in node:
                self._walk_json(
                    value, candidates, visited, depth, max_depth, context_title
                )

    def _collect_service_layers(
        self, url: str, title: str, candidates: list[dict]
    ) -> None:
        url = url.rstrip("/")
        tail = url.rsplit("/", 1)[-1]
        try:
            if tail.isdigit():
                meta = self.get_layer_metadata(url)
                candidates.append(self._layer_descriptor(url, title, meta))
                return
            service_meta = self.get_json(url)
            for layer in service_meta.get("layers", []) or []:
                layer_id = layer.get("id")
                if layer_id is None:
                    continue
                layer_url = f"{url}/{layer_id}"
                try:
                    meta = self.get_layer_metadata(layer_url)
                except (SourceUnavailableError, SourceSchemaError):
                    continue
                candidates.append(
                    self._layer_descriptor(
                        layer_url, layer.get("name") or title, meta
                    )
                )
        except (SourceUnavailableError, SourceSchemaError) as exc:
            log.warning("Could not inspect service %s: %s", url, exc)

    @staticmethod
    def _layer_descriptor(url: str, title: str, meta: dict) -> dict:
        return {
            "url": url,
            "title": meta.get("name") or title or "",
            "fields": meta.get("fields") or [],
            "geometry_type": meta.get("geometryType") or "",
        }

    def select_layers(self, candidates: list[dict], profile: str) -> dict:
        """Select ALL layers scoring above the profile threshold.

        Needed for sources like Nevada production, which split data across
        several per-commodity layers within one item.
        """
        spec = LAYER_PROFILES[profile]
        scored = self._score_candidates(candidates, spec)
        threshold = spec.get("min_selection_score", 1)
        max_layers = spec.get("max_layers", 10)
        selected = [
            {
                "url": cand["url"],
                "title": cand.get("title"),
                "selection_score": score,
                "reasons": reasons,
                "fields": cand.get("fields", []),
                "geometry_type": cand.get("geometry_type", ""),
            }
            for score, cand, reasons in scored
            if score >= threshold
        ][:max_layers]
        if not selected:
            raise SourceSchemaError(
                f"No candidate layer scored >= {threshold} for profile {profile!r} "
                f"({len(candidates)} candidates)"
            )
        return {
            "selected": selected,
            "candidate_count": len(candidates),
            "threshold": threshold,
        }

    def select_best_layer(self, candidates: list[dict], profile: str) -> dict:
        """Score candidate layers against a selection profile; return diagnostics."""
        spec = LAYER_PROFILES[profile]
        if not candidates:
            raise SourceSchemaError(
                f"No candidate layers found for profile {profile!r}"
            )
        scored = self._score_candidates(candidates, spec)
        best_score, best, reasons = scored[0]
        if best_score <= 0:
            raise SourceSchemaError(
                f"No candidate layer scored positively for profile {profile!r} "
                f"({len(candidates)} candidates)"
            )
        return {
            "selected_url": best["url"],
            "selected_title": best.get("title"),
            "selection_score": best_score,
            "candidate_count": len(candidates),
            "reasons": reasons,
            "fields": best.get("fields", []),
            "geometry_type": best.get("geometry_type", ""),
        }

    @staticmethod
    def _score_candidates(
        candidates: list[dict], spec: dict
    ) -> list[tuple[float, dict, list[str]]]:
        scored: list[tuple[float, dict, list[str]]] = []
        for candidate in candidates:
            score = 0.0
            reasons: list[str] = []
            title = (candidate.get("title") or "").lower()
            field_text = " ".join(
                ((f.get("name") or "") + " " + (f.get("alias") or "")).lower()
                for f in candidate.get("fields", [])
            )
            for keyword in spec["title_keywords"]:
                if keyword in title:
                    score += 15
                    reasons.append(f"title contains {keyword!r}")
            for keyword in spec["field_keywords"]:
                if keyword in field_text:
                    score += 8
                    reasons.append(f"has field matching {keyword!r}")
            if candidate.get("geometry_type") in spec["preferred_geometry"]:
                score += 10
                reasons.append("preferred geometry type")
            for keyword in spec["penalty_keywords"]:
                if keyword in title:
                    score -= 25
                    reasons.append(f"title penalty {keyword!r}")
            scored.append((score, candidate, reasons))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored
