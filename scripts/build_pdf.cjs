// Print the pandoc-rendered commentary HTML to an A4 PDF and enforce the page limit.
// CommonJS so a globally installed Playwright resolves via NODE_PATH.
const { chromium } = require("playwright");
const { readFileSync } = require("node:fs");
const { pathToFileURL } = require("node:url");
const path = require("node:path");

(async () => {
  const [, , htmlPath, pdfPath, maxPages = "2"] = process.argv;
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto(pathToFileURL(path.resolve(htmlPath)).href, { waitUntil: "load" });
  await page.pdf({ path: pdfPath, format: "A4", preferCSSPageSize: true, printBackground: true });
  await browser.close();
  const pages = (readFileSync(pdfPath, "latin1").match(/\/Type\s*\/Page(?!s)/g) || []).length;
  console.log(`${pdfPath}: ${pages} page(s)`);
  if (pages > Number(maxPages)) {
    console.error(`exceeds ${maxPages}-page limit`);
    process.exit(1);
  }
})();
