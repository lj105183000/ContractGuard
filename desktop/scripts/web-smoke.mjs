import { chromium } from "playwright";

const baseUrl = process.env.CONTRACTGUARD_WEB_URL || "http://127.0.0.1:8000";

const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  page.on("dialog", (dialog) => dialog.accept());
  page.on("pageerror", (error) => console.error(`[pageerror] ${error.message}`));

  await page.goto(baseUrl);
  await page.waitForSelector(".app-header", { timeout: 30000 });
  await page.waitForFunction(
    () => document.querySelectorAll(".sample-row select option").length > 1,
    null,
    { timeout: 30000 },
  );

  await page.selectOption(".sample-row select", { index: 1 });
  await page.click(".sample-row .btn-secondary");
  await page.waitForSelector(".results-view", { timeout: 120000 });
  console.log("review: results visible");

  await page.getByRole("button", { name: "任务历史" }).click();
  await page.waitForSelector(".history-item", { timeout: 15000 });
  const before = await page.locator(".history-item").count();
  console.log(`history items before delete: ${before}`);

  await page.locator(".history-delete").first().click();
  await page.waitForFunction(
    () => document.querySelectorAll(".history-item").length === 0,
    null,
    { timeout: 15000 },
  );

  const response = await page.request.get(`${baseUrl}/api/reviews`);
  const jobs = await response.json();
  if (!Array.isArray(jobs) || jobs.length !== 0) {
    throw new Error(`server jobs after delete: ${JSON.stringify(jobs)}`);
  }
  console.log("server jobs after delete: 0");
  console.log("web smoke passed");
} finally {
  await browser.close();
}
