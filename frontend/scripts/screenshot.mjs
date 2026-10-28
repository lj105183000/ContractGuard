/**
 * 用系统 Edge 给 README 截图（无需下载 Chromium）。
 *
 * 前置：后端已在 http://127.0.0.1:8000 运行（mock 模式即可）。
 * 运行：在 frontend 目录执行 `node scripts/screenshot.mjs`
 */

import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const outputDir = path.resolve(__dirname, "../../docs/images");
const baseUrl = process.env.CONTRACTGUARD_URL || "http://127.0.0.1:8000";

async function main() {
  await mkdir(outputDir, { recursive: true });
  const browser = await chromium.launch({
    channel: "msedge",
    headless: true,
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    locale: "zh-CN",
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => console.error("PAGE ERROR:", error.message));
  page.on("console", (message) => {
    if (message.type() === "error") console.error("CONSOLE:", message.text());
  });

  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(500);
  await page.screenshot({
    path: path.join(outputDir, "01-home.png"),
    fullPage: true,
  });
  console.log("saved 01-home.png");

  await page.waitForFunction(
    () => document.querySelectorAll(".sample-row select option").length > 1,
  );
  await page.selectOption(".sample-row select", "sample_01");
  await page.getByRole("button", { name: "审查示例" }).click();

  await page.waitForSelector(".results-view", { timeout: 40_000 });
  await page.waitForTimeout(700);

  // 结果页会自动打开第一条高风险详情；先关闭，拍清单
  const drawer = page.locator(".drawer");
  if (await drawer.count()) {
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
  }
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: path.join(outputDir, "02-risk-list.png"),
    fullPage: true,
  });
  console.log("saved 02-risk-list.png");

  await page.locator(".finding-card").first().click();
  await page.waitForSelector(".drawer");
  await page.waitForTimeout(400);
  await page.screenshot({
    path: path.join(outputDir, "03-finding-detail.png"),
    fullPage: false,
  });
  console.log("saved 03-finding-detail.png");

  await page.locator(".drawer-scroll").evaluate((node) => {
    node.scrollTop = 620;
  });
  await page.waitForTimeout(300);
  await page.screenshot({
    path: path.join(outputDir, "04-legal-basis.png"),
    fullPage: false,
  });
  console.log("saved 04-legal-basis.png");

  await browser.close();
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
