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

// --- create a project --------------------------------------------------
await click("New project");
await page.waitForSelector('button:has-text("Create project")', { timeout: 15000 });
const fields = await page.$$("form input");
await fields[0].fill("KPC-2 nanobody binders");
await fields[1].fill("Carbapenem-resistant Klebsiella pneumoniae");
await fields[2].fill("Klebsiella pneumoniae");
await fields[3].fill("25");
await shot("11-project-create");
await click("Create project");
await page.waitForSelector("text=Find a biological target", { timeout: 30000 });
await shot("12-workflow-target", 1500);

// --- target ------------------------------------------------------------
await click("Search");
await page.waitForSelector('button:has-text("Add to project")', { timeout: 90000 });
await shot("13-target-results");
await click("Add to project");
// The add fans out to UniProt, RCSB and PubMed, so wait for the result.
await page.waitForSelector("text=target in this project", { timeout: 120000 });
await shot("14-target-added", 1200);

// --- epitope -----------------------------------------------------------
await click("Continue");
await page.waitForSelector("text=Choose the epitope", { timeout: 30000 });
await shot("15-epitope-empty", 3500);
const residueInput = page.locator('input[placeholder*="70"]').first();
await residueInput.waitFor({ timeout: 15000 });
await residueInput.fill("70, 73, 130, 166");
await click("Check the mapping");
await page.waitForSelector("text=sent to the model", { timeout: 60000 });
await shot("16-epitope-checked", 1500);
await click("Save epitope");
await page.waitForSelector("text=Generate candidates", { timeout: 60000 });
await shot("17-design-step", 1500);

// --- estimate and run --------------------------------------------------
await click("Get an estimate");
await page.waitForSelector("text=this run will cost", { timeout: 90000 });
await shot("18-estimate", 1000);
await click("Confirm");
await page.waitForSelector("text=Candidates", { timeout: 90000 });
await shot("19-run-progress", 2500);

// Test-mode runs finish in seconds; wait for the ranking to appear.
await page
  .waitForSelector("text=ranked", { timeout: 180000 })
  .catch(() => console.log("  (ranking did not appear in time)"));
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
