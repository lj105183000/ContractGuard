import path from "node:path";
import { mkdir, readFile, readdir, stat } from "node:fs/promises";
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
  const historyBefore = await page.evaluate(async () => {
    const bridge = window.contractguardDesktop;
    if (!bridge?.history?.list) return 0;
    try {
      return (await bridge.history.list()).length;
    } catch {
      return 0;
    }
  });
  console.log(`history items before review: ${historyBefore}`);
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

  // 桌面端在审查完成后延迟 400ms 落盘；CI 的 userData 是全新的，
  // 必须先等到 history 里出现记录，再打开面板，否则会读到空列表。
  await page.waitForFunction(
    async (before) => {
      const bridge = window.contractguardDesktop;
      if (!bridge?.history?.list) return false;
      try {
        const items = await bridge.history.list();
        return Array.isArray(items) && items.length > before;
      } catch {
        return false;
      }
    },
    historyBefore,
    { timeout: 30000 },
  );
  console.log("history save: ok");

  // Ask Electron for the exact userData path instead of guessing a directory
  // under %APPDATA%. Then pick the newest JSON file; the wait above has
  // already confirmed that a history entry was persisted.
  const userDataDir = await app.evaluate(({ app }) => app.getPath("userData"));
  const historyDir = path.join(userDataDir, "history");
  const historyNames = (await readdir(historyDir)).filter((name) =>
    name.endsWith(".json"),
  );
  if (!historyNames.length) throw new Error("history file not found");
  const historyFiles = await Promise.all(
    historyNames.map(async (name) => {
      const file = path.join(historyDir, name);
      return { file, mtimeMs: (await stat(file)).mtimeMs };
    }),
  );
  historyFiles.sort((left, right) => right.mtimeMs - left.mtimeMs);
  const rawHistory = await readFile(historyFiles[0].file, "utf8");
  const encryptedAtRest =
    rawHistory.includes('"encrypted":true') && !rawHistory.includes("full_text");
  console.log(`history at rest: ${encryptedAtRest ? "encrypted" : "plaintext"}`);
  if (
    process.env.CONTRACTGUARD_REQUIRE_HISTORY_ENCRYPTION === "1" &&
    !encryptedAtRest
  ) {
    throw new Error("history snapshot is plaintext at rest");
  }

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
