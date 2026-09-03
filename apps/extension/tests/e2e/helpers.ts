import { chromium, type BrowserContext, type Worker } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const extensionPath = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "dist");

export const FIXTURES = "http://127.0.0.1:4173";

export async function launchWithExtension(): Promise<{
  context: BrowserContext;
  serviceWorker: Worker;
}> {
  const context = await chromium.launchPersistentContext(
    mkdtempSync(join(tmpdir(), "fa-ext-")),
    {
      channel: "chromium",
      args: [
        `--disable-extensions-except=${extensionPath}`,
        `--load-extension=${extensionPath}`,
      ],
    },
  );
  let [serviceWorker] = context.serviceWorkers();
  if (!serviceWorker) serviceWorker = await context.waitForEvent("serviceworker");
  return { context, serviceWorker };
}

/** Drive attach+observe through the background worker's test hook, standing
 * in for the side panel's user gesture. Returns the SessionState. */
export async function attachAndObserve(serviceWorker: Worker, url: string) {
  return serviceWorker.evaluate(async (targetUrl: string) => {
    const tabs = await chrome.tabs.query({});
    const tab = tabs.find((t) => t.url === targetUrl);
    if (!tab?.id || !tab.url) throw new Error(`no tab for ${targetUrl}`);
    await globalThis.__formAgentTest.attachToTab(tab.id, tab.url);
    return globalThis.__formAgentTest.observe();
  }, url);
}

export async function observeAgain(serviceWorker: Worker) {
  return serviceWorker.evaluate(() => globalThis.__formAgentTest.observe());
}
