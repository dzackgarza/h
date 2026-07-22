// Reconstruct an HTML annotation's captured quote into clean LaTeX, server-side.
//
// The client sends the `textContent` of the range the reader dragged over. On a page whose
// mathematics is typeset in the browser that text is whatever the renderer built; on a page
// that ships MathML or KaTeX markup it is the visible glyphs *and* the hidden copy sitting
// beside them. None of it is in the page source we fetch, and none of it is what the reader
// wants stored.
//
// So the quote is located on prose alone. Every formula in the page is one opaque unit that
// matches whatever stands in its place in the capture, and is re-emitted as the TeX its
// author wrote ($TeX$ inline / $$TeX$$ display) -- copy-pastable into a normal LaTeX doc.
// Prose is identical in the source and in every renderer's output, so it is the only thing
// that can be matched, and matching it is enough to find both ends of the selection.
//
// Usage: node index.mjs <uri> <exact>   ->  prints the reconstructed quote, or empty when the
// selection cannot be located in the page (the caller then falls back to OCR of the rendered
// region). A hard failure (fetch/parse) exits non-zero so the caller raises rather than storing
// raw. A located quote containing no math is returned unchanged.

import { parseHTML } from 'linkedom';

import { strip } from './spacing.mjs';

const TEX = 'annotation[encoding="application/x-tex"]';

// Prose that resynchronises the match after a formula. Long enough that a run this length
// is unlikely to repeat in the gap, short enough to exist between two adjacent formulas.
const SYNC = 8;

// Mark every formula in the page with the TeX its author wrote, on the outermost element
// that carries the whole formula -- for KaTeX that is the wrapper holding both the hidden
// MathML and the visible glyph copy, so the walk below takes the pair as one unit and the
// copy cannot survive into the quote.
function markFormulas(document) {
  const mark = (element, tex, display) => {
    const clean = (tex || '').trim();
    if (clean) element.setAttribute('data-quote-tex', display ? `$$${clean}$$` : `$${clean}$`);
  };

  // Pandoc (the source MathJax typesets in the browser): the TeX is the span's own text.
  for (const span of document.querySelectorAll('span.math')) {
    const tex = (span.textContent || '').trim().replace(/^\\[([]/, '').replace(/\\[)\]]$/, '').trim();
    mark(span, tex, span.classList.contains('display'));
  }
  // KaTeX rendered ahead of time: TeX in the hidden MathML's annotation.
  for (const katex of document.querySelectorAll('span.katex')) {
    const parent = katex.parentElement;
    mark(
      katex,
      katex.querySelector(TEX)?.textContent,
      Boolean(parent && parent.classList && parent.classList.contains('katex-display')),
    );
  }
  // MathML, with LaTeXML's `alttext` or a TeX annotation child.
  for (const math of document.querySelectorAll('math')) {
    mark(
      math,
      math.getAttribute('alttext') || math.querySelector(TEX)?.textContent,
      math.getAttribute('display') === 'block',
    );
  }
}

// Delimited TeX written straight into the page's text, longest opener first. This is how
// Stack Exchange, MathOverflow and every other MathJax site ships its mathematics: no
// markup at all, just the author's TeX between delimiters, typeset in the reader's browser.
const DELIMITERS = [
  { open: '$$', close: '$$', display: true, dollar: true },
  { open: '\\[', close: '\\]', display: true, dollar: false },
  { open: '\\(', close: '\\)', display: false, dollar: false },
  { open: '$', close: '$', display: false, dollar: true },
];

// Tags whose text a reader never reads as mathematics -- MathJax's own `skipTags`.
const SKIP = new Set(['SCRIPT', 'STYLE', 'CODE', 'PRE', 'KBD', 'SAMP', 'TEXTAREA']);

// Whether `$..$` means mathematics on this page. Only a page that says so in its MathJax
// config gets dollars read as delimiters; on any other page a price is a price.
function dollarsAreMath(document) {
  const loadsMathJax = [...document.querySelectorAll('script')].some(script =>
    /mathjax/i.test(script.getAttribute('src') || ''),
  );
  if (!loadsMathJax) return false;
  const config = [...document.querySelectorAll('script')]
    .map(script => script.textContent || '')
    .join('\n');
  return /inlineMath[^\]]*\$/.test(config) || /displayMath[^\]]*\$/.test(config);
}

// Split a text run into prose and the formulas written into it.
function splitDelimited(text, dollars) {
  const pieces = [];
  let prose = '';
  let i = 0;
  const escaped = at => at > 0 && text[at - 1] === '\\';
  while (i < text.length) {
    const delimiter = DELIMITERS.find(
      candidate =>
        (dollars || !candidate.dollar) &&
        text.startsWith(candidate.open, i) &&
        !(candidate.dollar && escaped(i)),
    );
    let end = -1;
    if (delimiter) {
      end = text.indexOf(delimiter.close, i + delimiter.open.length);
      while (end > 0 && delimiter.dollar && escaped(end)) {
        end = text.indexOf(delimiter.close, end + 1);
      }
    }
    const tex = end < 0 ? '' : text.slice(i + delimiter.open.length, end).trim();
    if (!tex) {
      prose += text[i];
      i += 1;
      continue;
    }
    if (prose) pieces.push({ text: prose });
    prose = '';
    const source = text.slice(i, end + delimiter.close.length);
    pieces.push({ tex: delimiter.display ? `$$${tex}$$` : `$${tex}$`, source });
    i = end + delimiter.close.length;
  }
  if (prose) pieces.push({ text: prose });
  return pieces;
}

// The page as an alternating sequence of prose runs and formulas, in reading order.
function segmentsOf(root, dollars) {
  const segments = [];
  const walk = (node, inSkipped) => {
    for (const child of node.childNodes) {
      if (child.nodeType === 3) {
        const text = strip(child.textContent || '');
        if (!text) continue;
        if (inSkipped) segments.push({ text });
        else segments.push(...splitDelimited(text, dollars));
      } else if (child.nodeType === 1) {
        // The client turns each `<br>` into a space before capturing (its
        // `renderedTextFromRange`), because block tags carry whitespace in the source and
        // `<br>` does not. Match that or every quote spanning a line break falls apart.
        if (child.tagName === 'BR') {
          segments.push({ text: ' ' });
          continue;
        }
        const tex = child.getAttribute && child.getAttribute('data-quote-tex');
        if (tex != null) segments.push({ tex, source: strip(child.textContent || '') });
        else walk(child, inSkipped || SKIP.has(child.tagName));
      }
    }
  };
  walk(root, false);
  return segments;
}

// Read the page from segment `index` (entering its prose `offset` characters in) against
// the capture from `pos`, and report the quote that reading produces plus how much prose it
// accounted for. Null when the page does not read that way from there.
function readFrom(segments, index, offset, needle, pos) {
  const out = [];
  let matched = 0;
  for (let k = index; k < segments.length; k += 1) {
    const segment = segments[k];
    if (segment.tex !== undefined) {
      out.push(segment.tex);
      // The capture holds the renderer's version of this formula, whose length we cannot
      // know: skip to wherever the next prose resumes in it. No next prose, or none left in
      // the capture, means the selection ended inside this formula.
      const next = segments.slice(k + 1).find(candidate => candidate.text !== undefined);
      if (!next) return { text: out.join(''), matched };
      const at = needle.indexOf(next.text.slice(0, SYNC), pos);
      if (at < 0) return { text: out.join(''), matched };
      pos = at;
      continue;
    }
    const text = k === index ? segment.text.slice(offset) : segment.text;
    const rest = needle.slice(pos);
    if (rest.length <= text.length) {
      // The selection ends inside this run.
      if (!text.startsWith(rest)) return null;
      out.push(rest);
      return { text: out.join(''), matched: matched + rest.length };
    }
    if (!rest.startsWith(text)) return null;
    out.push(text);
    matched += text.length;
    pos += text.length;
  }
  return { text: out.join(''), matched };
}

// Every way the capture could begin: at a prose run the page shares with it, or inside the
// formula that precedes one (whose rendering is the capture's opening text, and which the
// reader therefore selected).
function* candidates(segments, needle) {
  for (let i = 0; i < segments.length; i += 1) {
    const { text } = segments[i];
    if (text === undefined) continue;
    // The capture begins part-way into this run (or at its start).
    for (let at = text.indexOf(needle[0]); at >= 0; at = text.indexOf(needle[0], at + 1)) {
      const run = text.slice(at);
      if (needle.startsWith(run) || run.startsWith(needle)) {
        yield { index: i, offset: at, pos: 0, lead: '' };
      }
    }
    // The capture begins inside the formula before this run, whose rendering is its opening
    // text -- so the reader selected that formula and gets all of it.
    const before = segments[i - 1];
    if (!before || before.tex === undefined) continue;
    const sync = text.slice(0, SYNC);
    for (let at = needle.indexOf(sync); at > 0; at = needle.indexOf(sync, at + 1)) {
      yield { index: i, offset: 0, pos: at, lead: before.tex };
    }
  }
}

// The reading that accounts for the most of what the reader captured. A wrong start reads a
// few characters and then stops making sense of the capture; the right one carries it to the
// end, so the amount of prose accounted for is what tells them apart.
function locate(segments, needle) {
  let best = null;
  for (const { index, offset, pos, lead } of candidates(segments, needle)) {
    const read = readFrom(segments, index, offset, needle, pos);
    if (read && (!best || read.matched > best.matched)) {
      best = { text: lead + read.text, matched: read.matched };
    }
  }
  if (best) return best.text;

  // A drag over nothing but a formula carries no prose to anchor it. On a page that ships
  // its mathematics already rendered, the capture is that formula's own text and says which
  // one it is.
  const formula = segments.find(
    segment => segment.tex !== undefined && segment.source && segment.source.includes(needle),
  );
  return formula ? formula.tex : '';
}

// Where the page reads on from the prose the client captured just before the selection.
// A reader who highlights nothing but a formula gives us no prose of their own, but the
// client sends the 32 characters either side, and on every page those are the author's own
// words -- so the formula is the one the page has right after them.
function locateByContext(segments, prefix, suffix) {
  const lead = strip(prefix).slice(-SYNC);
  if (!lead.trim()) return '';

  const matches = [];
  for (let i = 0; i < segments.length; i += 1) {
    const { text } = segments[i];
    if (text === undefined || !text.endsWith(lead)) continue;
    const formulas = formulasAfter(segments, i);
    if (formulas) matches.push({ index: i, formulas });
  }
  if (matches.length === 1) return matches[0].formulas;

  // That prose occurs in more than one place, so the words the client saw after the
  // selection decide which. They are not always the page's -- what follows a formula in the
  // capture is often the renderer's own doing -- and when they settle nothing, OCR of the
  // region is a better answer than a guess at which formula the reader meant.
  const follows = strip(suffix).slice(0, SYNC).trim();
  const agreeing = follows
    ? matches.filter(match => followedBy(segments, match.index, follows))
    : [];
  return agreeing.length === 1 ? agreeing[0].formulas : '';
}

// The formulas the page has immediately after segment `index`, as one quote.
function formulasAfter(segments, index) {
  const out = [];
  for (let k = index + 1; k < segments.length; k += 1) {
    const segment = segments[k];
    if (segment.tex !== undefined) {
      out.push(segment.tex);
      continue;
    }
    // Whitespace between two displayed formulas is part of the selection; the first real
    // word after them is where the reader let go.
    if (segment.text.trim() === '') continue;
    break;
  }
  return out.join('');
}

function followedBy(segments, index, follows) {
  for (let k = index + 1; k < segments.length; k += 1) {
    const { text } = segments[k];
    if (text === undefined || text.trim() === '') continue;
    return text.trim().startsWith(follows) || follows.startsWith(text.trim());
  }
  return false;
}

async function main() {
  const [uri, exact, prefix = '', suffix = ''] = process.argv.slice(2);
  // Missing arguments are a caller bug: exit non-zero rather than emit the empty
  // string, which is the legitimate "selection not found -> OCR fallback" signal.
  if (!uri || !exact) throw new Error('usage: index.mjs <uri> <exact> [prefix] [suffix]');
  const html = await fetch(uri).then(r => r.text());
  const { document } = parseHTML(html);

  markFormulas(document);
  const root = document.querySelector('main') || document.body;
  const segments = segmentsOf(root, dollarsAreMath(document));
  return locate(segments, strip(exact)) || locateByContext(segments, prefix, suffix);
}

// Exit 0 with the reconstructed quote (empty = selection not found, so the caller falls back
// to OCR). Exit 1 with the reason on stderr for a hard failure (page fetch, parse) so the caller
// raises rather than storing a raw result.
main()
  .then(out => process.stdout.write(out || ''))
  .catch(err => {
    process.stderr.write(String((err && err.stack) || err));
    process.exitCode = 1;
  });
