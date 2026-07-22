// Record what a reader's drag over each fixture page actually captures.
//
// The client does not send what a page *looks* like: `TextQuoteSelector.exact` is the
// `textContent` of the selected range (see the client's `renderedTextFromRange`), so it
// carries whatever the DOM holds at selection time -- including nodes the reader cannot
// see, and including whatever the page's own JavaScript put there. On a MathJax page that
// is MathJax's output, which only exists after the page has run; on a KaTeX or LaTeXML
// page it is the visible glyphs *and* the hidden MathML and TeX annotation beside them.
//
// Hand-writing those strings would be inventing the input. This records them from a real
// browser rendering the committed fixtures, exactly as the client would compute them, and
// commits the result so the suite needs no browser and no network.
//
// Run with `just _record-html-selections` after changing a fixture page or a drag below.

import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { chromium } from 'playwright-core';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PAGES = path.join(HERE, 'pages');
const OUT = path.join(HERE, 'html-selections.json');

// Each drag is named for the shape a reader produced, not for the page it lives on: `from`
// is where the drag started and `to` where it ended (the same element for a drag inside
// one paragraph). Selectors are CSS, resolved in the page.
const DRAGS = [
  // ar5iv / LaTeXML: MathML with the authored TeX in an `annotation` child.
  { name: 'ar5iv_prose_and_inline_math', page: 'ar5iv-enriques.html', from: '[id="S1.p1.2"]' },
  { name: 'ar5iv_paragraph_of_definitions', page: 'ar5iv-enriques.html', from: '[id="S1.p2.12"]' },
  { name: 'ar5iv_theorem_statement', page: 'ar5iv-enriques.html', from: '[id="S1.Thmtheorem1.p1.7"]' },
  {
    name: 'ar5iv_drag_through_a_commutative_diagram',
    page: 'ar5iv-enriques.html',
    from: '[id="S2.SS1.p1.21"]',
    to: '[id="S2.E1"]',
  },
  // MathJax: the source holds `\(..\)`; what the reader selects is MathJax's output.
  { name: 'mathjax_prose_only', page: 'mathjax-category-theory.html', from: 'article > p:nth-of-type(1)' },
  { name: 'mathjax_inline_math', page: 'mathjax-category-theory.html', from: 'article > p:nth-of-type(8)' },
  { name: 'mathjax_custom_macro', page: 'mathjax-category-theory.html', from: 'article > p:nth-of-type(10)' },
  { name: 'mathjax_display_math', page: 'mathjax-category-theory.html', from: 'article > p:nth-of-type(21)' },
  // KaTeX rendered server-side: visible spans beside hidden MathML and TeX.
  { name: 'katex_inline_math', page: 'katex-docusaurus.html', from: 'article p', index: 4 },
  { name: 'katex_display_math', page: 'katex-docusaurus.html', from: '.katex-display' },
  { name: 'katex_prose_only', page: 'katex-docusaurus.html', from: 'article p', index: 0 },
  // A page with no mathematics at all.
  { name: 'plain_prose', page: 'plain-about.html', from: 'p' },
];

function serve() {
  const server = http.createServer((request, response) => {
    const file = path.join(PAGES, path.basename(request.url));
    if (!fs.existsSync(file)) {
      response.writeHead(404).end();
      return;
    }
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    response.end(fs.readFileSync(file));
  });
  return new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(server)));
}

async function capture(page, { from, to, index = 0 }) {
  return page.evaluate(({ from, to, index }) => {
    const first = document.querySelectorAll(from)[index];
    const last = to ? document.querySelector(to) : first;
    if (!first || !last) throw new Error(`no element for ${from} / ${to}`);
    const range = document.createRange();
    range.setStartBefore(first);
    range.setEndAfter(last);
    // The client's `renderedTextFromRange`, verbatim: clone the range, turn each `<br>`
    // into a space, take `textContent`.
    const container = document.createElement('div');
    container.appendChild(range.cloneContents());
    for (const br of [...container.querySelectorAll('br')]) {
      br.replaceWith(document.createTextNode(' '));
    }
    return container.textContent || '';
  }, { from, to, index });
}

const server = await serve();
const origin = `http://127.0.0.1:${server.address().port}`;
const executablePath = process.env.H_CHROMIUM_PATH;
if (!executablePath) throw new Error('H_CHROMIUM_PATH is not set');
const browser = await chromium.launch({ executablePath, headless: true });
const recorded = {};
try {
  for (const drag of DRAGS) {
    const page = await browser.newPage();
    await page.goto(`${origin}/${drag.page}`, { waitUntil: 'networkidle' });
    // MathJax typesets after load; without this the recording would be the `\(..\)`
    // source, which is not what any reader can select.
    await page.waitForFunction(
      () => !window.MathJax || document.querySelector('mjx-container') !== null,
      undefined,
      { timeout: 30000 },
    );
    recorded[drag.name] = { page: drag.page, exact: await capture(page, drag) };
    await page.close();
  }
} finally {
  await browser.close();
  server.close();
}

fs.writeFileSync(OUT, `${JSON.stringify(recorded, null, 2)}\n`);
process.stdout.write(`recorded ${Object.keys(recorded).length} drags into ${OUT}\n`);
