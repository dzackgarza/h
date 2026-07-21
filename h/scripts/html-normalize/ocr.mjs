// Render an HTML text selection in headless Chromium and emit its PNG as base64.

import { chromium } from 'playwright-core';

async function selectionRect(page, exact) {
  return page.evaluate(needleRaw => {
    const spacing = /[​‌‍⁠﻿   ]/g;
    const clean = value => (value || '').replace(spacing, '');
    const nodes = [];
    let rendered = '';
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent || ['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(parent.tagName)) {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = clean(node.nodeValue);
      nodes.push({ node, start: rendered.length, end: rendered.length + text.length });
      rendered += text;
    }

    const needle = clean(needleRaw);
    const start = rendered.indexOf(needle);
    if (start < 0) return null;
    const end = start + needle.length;
    const first = nodes.find(segment => segment.end > start);
    const last = nodes.findLast(segment => segment.start < end);
    if (!first || !last) return null;

    const toOriginalOffset = (text, strippedOffset) => {
      if (strippedOffset === 0) return 0;
      let seen = 0;
      for (let offset = 0; offset < text.length; offset += 1) {
        if (!spacing.test(text[offset])) seen += 1;
        spacing.lastIndex = 0;
        if (seen === strippedOffset) return offset + 1;
      }
      return text.length;
    };

    const range = document.createRange();
    range.setStart(
      first.node,
      toOriginalOffset(first.node.nodeValue || '', start - first.start),
    );
    range.setEnd(last.node, toOriginalOffset(last.node.nodeValue || '', end - last.start));
    const rects = [...range.getClientRects()].filter(rect => rect.width && rect.height);
    if (!rects.length) return null;
    const paddingX = 2;
    const paddingY = 1;
    const left = Math.min(...rects.map(rect => rect.left)) + window.scrollX;
    const top = Math.min(...rects.map(rect => rect.top)) + window.scrollY;
    const right = Math.max(...rects.map(rect => rect.right)) + window.scrollX;
    const bottom = Math.max(...rects.map(rect => rect.bottom)) + window.scrollY;
    return {
      x: Math.max(0, left - paddingX),
      y: Math.max(0, top - paddingY),
      width: right - left + 2 * paddingX,
      height: bottom - top + 2 * paddingY,
    };
  }, exact);
}

async function main() {
  const [uri, exact, timeoutRaw] = process.argv.slice(2);
  if (!uri || !exact) throw new Error('usage: ocr.mjs <uri> <exact> [timeout-ms]');
  const timeout = Number(timeoutRaw || 30000);
  const browser = await chromium.launch({ executablePath: '/bin/chromium', headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
      deviceScaleFactor: 2,
    });
    await page.goto(uri, { waitUntil: 'domcontentloaded', timeout });
    const clip = await selectionRect(page, exact);
    if (!clip) throw new Error('rendered HTML selection could not be located');
    const png = await page.screenshot({ clip, animations: 'disabled' });
    return png.toString('base64');
  } finally {
    await browser.close();
  }
}

main()
  .then(output => process.stdout.write(output))
  .catch(error => {
    process.stderr.write(String(error?.stack || error));
    process.exitCode = 1;
  });
