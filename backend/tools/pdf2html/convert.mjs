#!/usr/bin/env node
/**
 * PDF → HTML (Apache Tika via pdf2html npm).
 * Usage: node convert.mjs /path/to/file.pdf
 * Writes HTML to stdout.
 */
import pdf2html from "pdf2html";

const input = process.argv[2];
if (!input) {
  console.error("usage: node convert.mjs <pdf-path>");
  process.exit(1);
}

const html = await pdf2html.html(input, {
  maxBuffer: 1024 * 1024 * 100,
});
process.stdout.write(html);
