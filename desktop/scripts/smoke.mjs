import path from "node:path";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { _electron as electron } from "playwright";

const here = path.dirname(fileURLToPath(import.meta.url));
const desktopDir = path.resolve(here, "..");
const packagedExe = process.env.CONTRACTGUARD_SMOKE_EXE;
const outDir = path.join(desktopDir, ".smoke", packagedExe ? "packaged" : "dev");
await mkdir(outDir, { recursive: true });

const launchOptions = packagedExe
  ? { executablePath: packagedExe }
  : { args: ["."], cwd: desktopDir };
const app = await electron.launch({
  ...launchOptions,
  env: {
    ...process.env,
    CONTRACTGUARD_BACKEND_PORT: packagedExe ? "8126" : "8123",
    LLM_MODE: "mock",
    HITL_ENABLED: "0",
    WORKER_ENABLED: "0",
    PYTHONUTF8: "1",
  },
});

try {
  const page = await app.firstWindow();
  page.on("console", (message) =>
    console.log(`[renderer:${message.type()}] ${message.text()}`),
  );
  page.on("pageerror", (error) => console.error(`[pageerror] ${error.message}`));

  await page.waitForSelector(".app-header", { timeout: 90000 });
  console.log(`title: ${await page.title()}`);
  console.log(
    `local history button: ${await page
      .getByRole("button", { name: "本地历史" })
      .innerText()}`,
  );
  console.log(
    `server history button: ${await page
      .getByRole("button", { name: "任务历史" })
      .count()}`,
  );
  await page.screenshot({ path: path.join(outDir, "01-home.png"), fullPage: true });

  await page.waitForFunction(
    () => document.querySelectorAll(".sample-row select option").length > 1,
    null,
    { timeout: 30000 },
  );
  await page.selectOption(".sample-row select", { index: 1 });
  await page.click(".sample-row .btn-secondary");
  await page.waitForSelector(".results-view", { timeout: 120000 });
  await page.screenshot({ path: path.join(outDir, "02-results.png"), fullPage: true });

  await page.getByRole("button", { name: "本地历史" }).click();
  await page.waitForSelector(".history-item", { timeout: 15000 });
  await page.waitForTimeout(400);
  console.log(`history items: ${await page.locator(".history-item").count()}`);
  await page.screenshot({ path: path.join(outDir, "03-history.png") });

  await page.locator(".history-item .history-open").first().click();
  await page.waitForSelector(".results-view", { timeout: 15000 });
  console.log(`readonly badge: ${await page.locator(".history-snapshot-badge").count()}`);
  console.log(`readonly banner: ${await page.locator(".history-banner").innerText()}`);
  console.log(`export disabled: ${await page.locator(".export-buttons .btn-primary").isDisabled()}`);
  await page.screenshot({
    path: path.join(outDir, "04-history-readonly.png"),
  });
} finally {
  await app.close();
}
