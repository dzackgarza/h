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
  // Stack Exchange: the source carries the author's TeX as plain `$..$` text with no math
  // markup at all, and MathJax typesets it in the browser. This is where a reader of
  // mathematics spends their day.
  {
    name: 'stackexchange_question_with_inline_math',
    page: 'mathjax-stackexchange.html',
    from: '.s-prose p',
    index: 0,
  },
  {
    name: 'stackexchange_answer_across_paragraphs',
    page: 'mathjax-stackexchange.html',
    from: '.js-post-body:nth-of-type(1) p',
    to: 'blockquote',
  },
  {
    name: 'stackexchange_only_an_inline_formula',
    page: 'mathjax-stackexchange.html',
    from: '.MathJax',
    index: 1,
  },
  {
    name: 'mathoverflow_paragraph_around_a_displayed_formula',
    page: 'mathjax-mathoverflow.html',
    from: '.s-prose p',
    index: 1,
  },
  {
    name: 'mathoverflow_only_a_displayed_formula',
    page: 'mathjax-mathoverflow.html',
    from: '.MathJax_Display',
    index: 0,
  },
  {
    name: 'mathoverflow_only_an_inline_formula',
    page: 'mathjax-mathoverflow.html',
    from: '.MathJax',
    index: 3,
  },
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
    const rendered = source => {
      const container = document.createElement('div');
      container.appendChild(source.cloneContents());
      for (const br of [...container.querySelectorAll('br')]) {
        br.replaceWith(document.createTextNode(' '));
      }
      return container.textContent || '';
    };

    // ...and the 32 characters of context either side that it sends with the quote, taken
    // the same way (the client's `TextQuoteAnchor.fromRange`).
    const CONTEXT = 32;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let offset = 0;
    let start = null;
    let end = null;
    const marks = [];
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      marks.push({ node, start: offset, end: offset + (node.nodeValue || '').length });
      offset += (node.nodeValue || '').length;
    }
    const positionOf = (container, containerOffset, atEnd) => {
      const before = document.createRange();
      before.setStart(document.body, 0);
      if (atEnd) before.setEnd(container, containerOffset);
      else before.setEnd(container, containerOffset);
      return before.toString().length;
    };
    start = positionOf(range.startContainer, range.startOffset, false);
    end = start + range.toString().length;
    const at = position => {
      const mark = marks.find(m => m.end >= position) || marks[marks.length - 1];
      const clamped = Math.max(mark.start, Math.min(position, mark.end));
      return { node: mark.node, offset: clamped - mark.start };
    };
    const context = (fromPosition, toPosition) => {
      if (toPosition <= fromPosition) return '';
      const head = at(fromPosition);
      const tail = at(toPosition);
      const contextRange = document.createRange();
      contextRange.setStart(head.node, head.offset);
      contextRange.setEnd(tail.node, tail.offset);
      return rendered(contextRange);
    };

    return {
      exact: rendered(range),
      prefix: context(Math.max(0, start - CONTEXT), start),
      suffix: context(end, end + CONTEXT),
    };
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
    // MathJax typesets after load; without this the recording would be the source TeX,
    // which is not what any reader can select. v3 emits `mjx-container`, v2 (which Stack
    // Exchange still runs) emits `.MathJax`.
    await page.waitForFunction(
      () =>
        !document.querySelector('script[src*="athJax" i], script[src*="athjax" i]') ||
        document.querySelector('mjx-container, .MathJax, .MathJax_Display') !== null,
      undefined,
      { timeout: 60000 },
    );
    recorded[drag.name] = { page: drag.page, ...(await capture(page, drag)) };
    await page.close();
  }
} finally {
  await browser.close();
  server.close();
}

fs.writeFileSync(OUT, `${JSON.stringify(recorded, null, 2)}\n`);
process.stdout.write(`recorded ${Object.keys(recorded).length} drags into ${OUT}\n`);
