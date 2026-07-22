// Zero-width and non-breaking space characters that differ between a page's rendered
// text and a selection captured from it. Both normalize scripts strip them before
// matching a quote against page text; ocr.mjs passes SPACING.source into the browser
// context (a page.evaluate callback cannot close over Node imports).

export const SPACING = /[​‌‍⁠﻿   ]/g;

export const strip = s => (s || '').replace(SPACING, '');
