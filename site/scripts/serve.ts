// Serves the static export (out/) the way a static host does: directory index files, gzip for text,
// and a 404 page. Used by the Playwright tests, the bundle measurement, and the Lighthouse run.
//
//   node scripts/serve.ts [port]        (default 4173)

import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer, type Server } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createGzip } from "node:zlib";

const OUT = fileURLToPath(new URL("../out/", import.meta.url));

const TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
  ".ico": "image/x-icon",
  ".png": "image/png",
  ".map": "application/json; charset=utf-8",
};
const COMPRESSIBLE = new Set([".html", ".js", ".css", ".json", ".txt", ".svg", ".map"]);

function resolve(urlPath: string): string | null {
  const decoded = decodeURIComponent(urlPath.split("?")[0] ?? "/");
  const file = path.normalize(path.join(OUT, decoded));
  if (!file.startsWith(OUT)) return null;
  if (existsSync(file) && statSync(file).isFile()) return file;
  const index = path.join(file, "index.html");
  if (existsSync(index)) return index;
  return null;
}

export function serve(port: number): Promise<Server> {
  const server = createServer((req, res) => {
    const found = resolve(req.url ?? "/");
    const file = found ?? path.join(OUT, "404.html");
    const status = found ? 200 : 404;
    const ext = path.extname(file);
    const headers: Record<string, string> = {
      "content-type": TYPES[ext] ?? "application/octet-stream",
      "cache-control": "no-cache",
    };
    // The router sends HEAD before prefetching a page (to follow redirects); it gets headers only.
    if (req.method === "HEAD") {
      headers["content-length"] = String(statSync(file).size);
      res.writeHead(status, headers);
      res.end();
      return;
    }
    const gzip = COMPRESSIBLE.has(ext) && /\bgzip\b/.test(String(req.headers["accept-encoding"]));
    if (gzip) headers["content-encoding"] = "gzip";
    res.writeHead(status, headers);
    const stream = createReadStream(file);
    (gzip ? stream.pipe(createGzip()) : stream).pipe(res);
  });
  return new Promise((ok) => server.listen(port, "127.0.0.1", () => ok(server)));
}

if (import.meta.main) {
  if (!existsSync(OUT)) {
    console.error("out/ does not exist; run npm run build first");
    process.exit(1);
  }
  const port = Number(process.argv[2] ?? 4173);
  await serve(port);
  console.log(`serving out/ on http://127.0.0.1:${port}`);
}
