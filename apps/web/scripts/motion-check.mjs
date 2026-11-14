import { chromium } from "playwright";

// Accept the URL as an argument as well as an env var. Taking only BASE_URL
// meant `node motion-check.mjs <url>` silently checked port 3000 instead —
// whatever happened to be running there — and reported that page's motion.
const BASE = process.argv[2] ?? process.env.BASE_URL ?? "http://127.0.0.1:3000";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto(BASE + "/", { waitUntil: "networkidle", timeout: 60000 });
// The specimen fetches a real structure from RCSB after mount, so give the
// network leg room before judging whether it rotates.
await p.waitForSelector("[data-specimen] canvas", { timeout: 60000 });
await p.waitForTimeout(3000);

// Does the specimen actually rotate? Compare canvas pixels over time.
async function canvasHash() {
  return p.evaluate(() => {
    const c = document.querySelector('[data-specimen] canvas');
    if (!c) return null;
    const d = c.toDataURL("image/png");
    let h = 0;
    for (let i = 0; i < d.length; i += 97) h = (h * 31 + d.charCodeAt(i)) >>> 0;
    return h;
  });
}
const a = await canvasHash();
await p.waitForTimeout(1600);
const c2 = await canvasHash();
console.log(`specimen rotating: ${a !== null && c2 !== null && a !== c2} (${a} -> ${c2})`);

// Does the intro actually run, rather than content just being present?
const p2 = await (await b.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
const positions = [];
await p2.goto(BASE + "/", { waitUntil: "domcontentloaded" });
await p2.waitForSelector("[data-sub]", { timeout: 30000 });
for (let i = 0; i < 6; i++) {
  positions.push(await p2.evaluate(() => {
    const el = document.querySelector("[data-sub]");
    if (!el) return null;
    const s = getComputedStyle(el);
    return `${s.opacity}|${s.transform}`;
  }));
  await p2.waitForTimeout(170);
}
const distinct = new Set(positions.filter(Boolean)).size;
console.log(`intro animates: ${distinct > 1} (${distinct} distinct states)`);

// Scroll set-piece
await p2.evaluate(() => window.scrollTo(0, 0));
const rowBefore = await p2.evaluate(() => getComputedStyle(document.querySelector("[data-num-row]")).opacity);
await p2.evaluate(() => window.scrollTo(0, 1200));
await p2.waitForTimeout(900);
const rowAfter = await p2.evaluate(() => getComputedStyle(document.querySelector("[data-num-row]")).opacity);
console.log(`numbering set-piece animates on scroll: ${rowBefore !== rowAfter} (${rowBefore} -> ${rowAfter})`);

// Reduced motion must not break anything.
const rm = await (await b.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" })).newPage();
const rmErrors = [];
rm.on("pageerror", (e) => rmErrors.push(e.message));
await rm.goto(BASE + "/", { waitUntil: "networkidle", timeout: 60000 });
await rm.waitForTimeout(2500);
const visible = await rm.evaluate(() => {
  const el = document.querySelector("[data-sub]");
  return el ? getComputedStyle(el).opacity : "missing";
});
console.log(`reduced motion: content visible opacity=${visible}, errors=${rmErrors.length}`);
await rm.screenshot({ path: "/private/tmp/claude-501/-Users-ajalaabdullahabiola-Documents-drug-sandbox-hackathon/90edc025-f9e4-44c3-9c0f-13c0d039e16a/scratchpad/shots/08-reduced-motion.png" });
await b.close();
