// Export what the recovery produces for every drag in the corpus.
//
// The quotes a reader is shown are the output of `index.mjs`, and the surface that renders
// them lives in another repository. Rather than have that repository invent plausible
// strings, this writes the real ones: every recorded drag, run through the real extractor
// against the real fixture pages, into `recovered-quotes.json`.
//
// Run with `just _export-recovered-quotes` after changing a page, a drag, or the extractor.

import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';

const run = promisify(execFile);

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PAGES = path.join(HERE, 'pages');
const EXTRACTOR = path.join(HERE, '..', '..', 'h', 'scripts', 'html-normalize', 'index.mjs');
const SELECTIONS = JSON.parse(fs.readFileSync(path.join(HERE, 'html-selections.json'), 'utf8'));
const OUT = path.join(HERE, 'recovered-quotes.json');

const server = http.createServer((request, response) => {
  const file = path.join(PAGES, path.basename(request.url));
  if (!fs.existsSync(file)) {
    response.writeHead(404).end();
    return;
  }
  response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
  response.end(fs.readFileSync(file));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;

const recovered = {};
for (const [name, drag] of Object.entries(SELECTIONS)) {
  // The server is served by this process, so the extractor has to be spawned
  // asynchronously -- a synchronous spawn blocks the event loop and the page never loads.
  const { stdout } = await run(process.execPath, [
    EXTRACTOR,
    `${origin}/${drag.page}`,
    drag.exact,
    drag.prefix ?? '',
    drag.suffix ?? '',
  ]);
  if (!stdout) {
    throw new Error(`${name}: the extractor recovered nothing; it should route to OCR here`);
  }
  recovered[name] = { page: drag.page, quote: stdout };
}
server.close();

fs.writeFileSync(OUT, `${JSON.stringify(recovered, null, 2)}\n`);
process.stdout.write(`exported ${Object.keys(recovered).length} recovered quotes to ${OUT}\n`);
