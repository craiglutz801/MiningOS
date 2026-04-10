/**
 * Browser-only CSV preview. Parses headers, sample rows, total row count,
 * and guesses column mapping — no server round-trip needed.
 */

function parseCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let inQuote = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (c === '"') {
      if (inQuote && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else {
        inQuote = !inQuote;
      }
      continue;
    }
    if (c === "," && !inQuote) {
      out.push(cur.trim());
      cur = "";
      continue;
    }
    cur += c;
  }
  out.push(cur.trim());
  return out;
}

function splitLines(text: string): string[] {
  const lines: string[] = [];
  let cur = "";
  let inQuote = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') {
      inQuote = !inQuote;
      cur += c;
      continue;
    }
    if ((c === "\n" || c === "\r") && !inQuote) {
      if (c === "\r" && text[i + 1] === "\n") i++;
      if (cur.length || lines.length) lines.push(cur);
      cur = "";
      continue;
    }
    cur += c;
  }
  if (cur.length || lines.length) lines.push(cur);
  return lines.filter((l) => l.length > 0);
}

export interface CsvInspectResult {
  headers: string[];
  sample_rows: Record<string, string>[];
  suggested_mapping: Record<string, string>;
  total_rows: number;
}

export function parseCsvForPreview(text: string, sampleSize = 5): CsvInspectResult {
  const raw = text.replace(/^\ufeff/, "");
  const lines = splitLines(raw);
  if (lines.length === 0) return { headers: [], sample_rows: [], suggested_mapping: {}, total_rows: 0 };

  const headers = parseCsvLine(lines[0]).map((h) => h.replace(/^"|"$/g, "").trim());
  const total_rows = lines.length - 1; // exclude header
  const sample_rows: Record<string, string>[] = [];
  for (let r = 1; r < lines.length && sample_rows.length < sampleSize; r++) {
    const cells = parseCsvLine(lines[r]);
    const row: Record<string, string> = {};
    headers.forEach((h, j) => {
      row[h] = (cells[j] ?? "").replace(/^"|"$/g, "").trim();
    });
    sample_rows.push(row);
  }

  const suggested_mapping = guessColumnMapping(headers);
  return { headers, sample_rows, suggested_mapping, total_rows };
}

const NH = (h: string) => h.trim().toLowerCase().replace(/[_\-]/g, " ");

export function guessColumnMapping(headers: string[]): Record<string, string> {
  const out: Record<string, string> = {};
  const nh = headers.map(NH);

  const find = (pred: (n: string) => boolean): string | undefined => {
    const i = nh.findIndex(pred);
    return i >= 0 ? headers[i] : undefined;
  };

  out.name =
    find((n) => ["mine name", "claim name", "property name", "site name"].some((k) => n.includes(k))) ??
    find((n) => ["name", "target", "mine", "claim", "property", "site", "title"].includes(n)) ??
    find((n) => n.includes("name") && !n.includes("range") && !n.includes("township")) ??
    "";

  out.state =
    find((n) => ["state", "st", "state abbr", "state code", "statecode"].includes(n)) ??
    find((n) => n.startsWith("state")) ??
    "";

  out.plss =
    find((n) => n.includes("plss") || n.includes("location plss") || n === "location" || n.includes("legal desc")) ??
    "";

  out.township =
    find((n) => n.includes("township") || ["twp", "twn", "town"].includes(n)) ?? "";

  out.range =
    find((n) => ["range", "rng", "rge"].includes(n) || (n.startsWith("range") && !n.includes("meridian"))) ?? "";

  out.section =
    find((n) => ["section", "sec", "sect", "sctn"].includes(n)) ?? "";

  out.minerals =
    find((n) => n.includes("mineral") || n.includes("commodity") || n.includes("commodities")) ?? "";

  out.status =
    find((n) => n === "status" || n.endsWith(" status")) ?? "";

  out.report_url =
    find((n) => (n.includes("report") && n.includes("url")) || n === "url" || (n.includes("pdf") && !n.includes("lr2000"))) ?? "";

  out.latitude =
    find((n) => ["latitude", "lat"].includes(n)) ?? "";

  out.longitude =
    find((n) => ["longitude", "lon", "long"].includes(n)) ?? "";

  return out;
}
