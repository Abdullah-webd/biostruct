import { chromium } from "playwright";
const b = await chromium.launch();
const p = await b.newPage();
const errs = [];
p.on("pageerror", e => errs.push("PAGEERROR: " + e.message.slice(0, 200)));
p.on("console", m => { if (m.type() === "error") errs.push("CONSOLE: " + m.text().slice(0, 200)); });
p.on("requestfailed", r => errs.push("REQFAIL: " + r.url().slice(0, 120) + " " + (r.failure()?.errorText ?? "")));
await p.goto("http://127.0.0.1:3001/register", { waitUntil: "load", timeout: 180000 });
// Poll for interactivity rather than guessing a wait.
let interactive = false;
for (let i = 0; i < 30; i++) {
  await p.waitForTimeout(2000);
  interactive = await p.evaluate(() => {
    const f = document.querySelector("form");
    if (!f) return false;
    return Object.keys(f).some(k => k.startsWith("__react")) ||
           Boolean(document.querySelector("[data-reveal]")) === false;
  });
  const hasFiber = await p.evaluate(() => {
    const f = document.querySelector("form");
    return f ? Object.keys(f).filter(k => k.startsWith("__react")).join(",") : "no form";
  });
  if (hasFiber && hasFiber !== "no form" && hasFiber.length) { console.log(`hydrated after ~${(i+1)*2}s: ${hasFiber}`); interactive = true; break; }
}
if (!interactive) console.log("never hydrated within 60s");
console.log("errors:", [...new Set(errs)].slice(0, 6));
await b.close();
