// Drives the real UI end to end and captures each screen, so the design can be
// reviewed rather than assumed. Waits on actual UI state rather than fixed
// timeouts, because the science APIs take as long as they take.
import { chromium } from "playwright";

const OUT =
  "/private/tmp/claude-501/-Users-ajalaabdullahabiola-Documents-drug-sandbox-hackathon/90edc025-f9e4-44c3-9c0f-13c0d039e16a/scratchpad/shots";
// Dev mode compiles each route on first visit, so give navigation room.
const BASE = process.env.BASE_URL ?? "http://127.0.0.1:3000";
const EMAIL = `ui-${Date.now()}@example.org`;
const PASSWORD = "ribosome velvet cartography";

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 } });
const page = await ctx.newPage();
page.setDefaultTimeout(120000);
page.setDefaultNavigationTimeout(120000);
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("console", (m) => {
  if (m.type() === "error") errors.push("console: " + m.text().slice(0, 140));
});

async function shot(name, settle = 700) {
  await page.waitForTimeout(settle);
  await page.screenshot({ path: `${OUT}/${name}.png` });
  console.log("  shot", name);
}

async function click(text) {
  const el = page.locator(`button:has-text("${text}")`).first();
  await el.waitFor({ state: "visible", timeout: 60000 });
  await el.click();
}

// --- register ----------------------------------------------------------
await page.goto(`${BASE}/register`, { waitUntil: "networkidle" });
await page.fill('input[name="name"]', "Ada Okafor");
await page.fill('input[name="email"]', EMAIL);
await page.fill('input[name="password"]', PASSWORD);
await page.click('button[type="submit"]');
await page.waitForURL("**/projects", { timeout: 30000 });
await shot("10-projects-empty", 1200);

// --- create a project from the one-click example -----------------------
await shot("11-example-offer");
await click("Walk me through this example");
await page.waitForSelector("text=Which protein is making this infection resistant?", {
  timeout: 30000,
});
await shot("12-workflow-target", 1500);

// --- pick the protein --------------------------------------------------
await click("KPC carbapenemase");
await page.waitForSelector('button:has-text("Use this one")', { timeout: 90000 });
await shot("13-target-results");
await click("Use this one");
// The add fans out to UniProt, RCSB and PubMed, so wait for the result.
await page.waitForSelector("text=protein in this project", { timeout: 120000 });
await shot("14-target-added", 1200);

// --- where to attack ---------------------------------------------------
await click("Next: choose where to attack");
await page.waitForSelector("text=Where on that protein should we attack?", {
  timeout: 30000,
});
// The attack site is pre-filled and checked on arrival, so wait for the
// confirmation rather than typing anything.
await page.waitForSelector("text=What you have selected", { timeout: 90000 });
await shot("15-epitope-prefilled", 3500);
await shot("16-epitope-checked", 500);
await click("Use this site");
await page.waitForSelector("text=Design the proteins", { timeout: 60000 });
await shot("17-design-step", 1500);

// --- price and run -----------------------------------------------------
await click("Check the price");
await page.waitForSelector("text=This run will cost", { timeout: 90000 });
await shot("18-estimate", 1000);
await click("and start");
await page.waitForSelector("text=What came back", { timeout: 90000 });
await shot("19-run-progress", 2500);

// Test-mode runs finish in seconds; wait for the ranking to appear.
await page
  .waitForSelector('h2:has-text("designed proteins, best first")', { timeout: 300000 })
  .then(() => console.log("  ranking appeared"))
  .catch(() => console.log("  !! ranking did NOT appear in time"));
await shot("20-results-ranked", 3000);
await page.waitForTimeout(6000);
await shot("21-results-detail");

// Scrolled further down the results, where the explanation lives.
await page.evaluate(() => window.scrollTo(0, 900));
await shot("23-results-explanation", 1500);

// --- mobile ------------------------------------------------------------
const mobileCtx = await browser.newContext({
  viewport: { width: 390, height: 844 },
  storageState: await ctx.storageState(),
});
const mobile = await mobileCtx.newPage();
await mobile.goto(page.url(), { waitUntil: "networkidle" });
await mobile.waitForTimeout(6000);
await mobile.screenshot({ path: `${OUT}/22-workflow-mobile.png` });
console.log("  shot 22-workflow-mobile");

console.log(
  errors.length
    ? "\nERRORS:\n" + [...new Set(errors)].slice(0, 8).join("\n")
    : "\nno JS errors",
);
await browser.close();
