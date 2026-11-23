import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const siteDirectory = process.argv[2] ?? "_site";
const indexPath = path.join(siteDirectory, "index.html");
const cssPath = path.join(siteDirectory, "styles.css");
const graphPath = path.join(siteDirectory, "graph.json");

let index = await readFile(indexPath, "utf8");
const replacements = [
  [
    '<title>OpenWiki visualizer</title>',
    `<title>Sempropos documentation</title>
<meta name="description" content="Practical documentation for Sempropos, the offline-first natural-language shell assistant." />`,
  ],
  [
    /<svg class="lc-logo-full"[\s\S]*?<\/svg>\s*<div class="divider"><\/div>\s*<div class="title">OpenWiki<small id="wiki-name">wiki visualizer<\/small><\/div>/,
    '<div class="sempropos-mark" aria-hidden="true">&gt;_</div><div class="title">Sempropos<small id="wiki-name">documentation</small></div>',
  ],
  [
    /<svg class="lc-logo-mark"[\s\S]*?<\/svg>\s*<div>Select a page to read it, or explore the graph.<\/div>/,
    '<div class="empty-mark" aria-hidden="true">&gt;_</div><div>Select a page to read it, or explore the graph.</div>',
  ],
];

for (const [search, replacement] of replacements) {
  const updated = index.replace(search, replacement);
  if (updated === index) {
    throw new Error(`OpenWiki export did not contain an expected branding marker: ${search}`);
  }
  index = updated;
}

const brandingStyles = `

/* Sempropos branding layered on the OpenWiki visualizer export. */
.sempropos-mark,
.empty-mark {
  display: grid;
  place-items: center;
  color: var(--lc-lime);
  background: color-mix(in srgb, var(--lc-lime) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--lc-lime) 35%, transparent);
  border-radius: 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-weight: 800;
  letter-spacing: -0.08em;
}
.sempropos-mark {
  width: 34px;
  height: 28px;
  font-size: 13px;
}
.empty-mark {
  width: 52px;
  height: 44px;
  margin: 0 auto 16px;
  font-size: 18px;
}
`;

let css = await readFile(cssPath, "utf8");
if (!css.includes("Sempropos branding layered")) {
  css += brandingStyles;
}

const graph = JSON.parse(await readFile(graphPath, "utf8"));
if (graph.root !== "openwiki") {
  throw new Error(`Expected an OpenWiki graph root, received: ${graph.root}`);
}
graph.root = "Sempropos documentation";

await Promise.all([
  writeFile(indexPath, index),
  writeFile(cssPath, css),
  writeFile(graphPath, `${JSON.stringify(graph, null, 2)}\n`),
  writeFile(path.join(siteDirectory, "CNAME"), "sempropos.dearanayji.rocks\n"),
]);
