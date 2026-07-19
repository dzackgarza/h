// Reconstruct an HTML annotation's flattened quote into clean LaTeX, server-side.
//
// The same positional reconstruction the sidebar client uses, run at intake instead of display:
// fetch the page, render each `<span class="math">` (or LaTeXML `<math>`) with KaTeX so its text
// matches what a reader selected, build the page's rendered text with a segment map back to each
// span's TeX, locate the quote in it, and re-emit the matched range with prose verbatim and math
// as \(TeX\) / $$TeX$$. KaTeX reproduces MathJax's textContent (pylatexenc does not), which is why
// this must run in Node.
//
// Usage: node index.mjs <uri> <exact>   ->  prints the reconstructed quote, or empty when the
// quote spans no math or can't be located (the worker then stores the raw quote unchanged).

import katex from 'katex';
import { parseHTML } from 'linkedom';

const SPACING = /[​‌‍⁠﻿   ]/g;
const strip = s => (s || '').replace(SPACING, '');

function renderText(tex, displayMode) {
  try {
    const html = katex.renderToString(tex, { displayMode, throwOnError: false, output: 'html' });
    const { document } = parseHTML(`<div id="k">${html}</div>`);
    return strip(document.getElementById('k').textContent || '');
  } catch {
    return '';
  }
}

async function main() {
  const [uri, exact] = process.argv.slice(2);
  if (!uri || !exact) return '';
  const html = await fetch(uri).then(r => r.text());
  const { document } = parseHTML(html);

  for (const span of document.querySelectorAll('span.math')) {
    const display = span.classList.contains('display');
    const tex = (span.textContent || '').trim().replace(/^\\[([]/, '').replace(/\\[)\]]$/, '').trim();
    span.setAttribute('data-quote-tex', display ? `$$${tex}$$` : `\\(${tex}\\)`);
    span.textContent = renderText(tex, display);
  }
  for (const math of document.querySelectorAll('math')) {
    const tex =
      math.getAttribute('alttext') ||
      math.querySelector('annotation[encoding="application/x-tex"]')?.textContent ||
      '';
    if (tex) math.setAttribute('data-quote-tex', `\\(${tex.trim()}\\)`);
  }

  const root = document.querySelector('main') || document.body;
  let rendered = '';
  const segments = [];
  const walk = node => {
    for (const child of node.childNodes) {
      if (child.nodeType === 3) {
        const text = strip(child.textContent || '');
        segments.push({ start: rendered.length, end: rendered.length + text.length });
        rendered += text;
      } else if (child.nodeType === 1) {
        const math = child.getAttribute && child.getAttribute('data-quote-tex');
        if (math != null) {
          const text = strip(child.textContent || '');
          segments.push({ start: rendered.length, end: rendered.length + text.length, math });
          rendered += text;
        } else {
          walk(child);
        }
      }
    }
  };
  walk(root);

  const needle = strip(exact);
  const at = rendered.indexOf(needle);
  if (at < 0) return '';
  const end = at + needle.length;
  let out = '';
  let replaced = false;
  for (const seg of segments) {
    if (seg.end <= at || seg.start >= end) continue;
    if (seg.math !== undefined) {
      out += seg.math;
      replaced = true;
    } else {
      out += rendered.slice(Math.max(seg.start, at), Math.min(seg.end, end));
    }
  }
  return replaced ? out : '';
}

main()
  .then(out => process.stdout.write(out || ''))
  .catch(() => process.stdout.write(''));
