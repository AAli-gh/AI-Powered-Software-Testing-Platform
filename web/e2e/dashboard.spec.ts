import { expect, test } from "@playwright/test";

// No test doubles: every request goes to the real API, every job runs in the real worker.
// The sandbox may be unavailable (the compose stack has no Docker access); the test then
// checks the honest outcome instead of test results.

test("register a project, analyse it, run its tests and follow the results", async ({ page }) => {
  const name = `e2e-discount-${Date.now()}`;
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  // Content-Security-Policy violations are reported as console errors.
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });

  await page.goto("/projects");
  await page.getByLabel("Name").fill(name);
  await page.getByLabel("Directory").fill("discount_service");
  await page.getByRole("button", { name: "Register" }).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();

  // Analyse: the run page refreshes by itself until the worker has finished.
  await page.getByRole("button", { name: "Analyse now" }).click();
  await expect(page.getByRole("heading", { name: "Analysis run" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Summary" })).toBeVisible();
  await expect(page.getByText("Lines of code")).toBeVisible();
  await expect(
    page.getByText("Primary language").locator("xpath=following-sibling::dd[1]"),
  ).toHaveText("Python");

  await page.getByRole("tab", { name: "Tests" }).click();
  await page.getByRole("button", { name: "Run tests" }).click();
  await expect(page.getByRole("heading", { name: "Test execution" })).toBeVisible();
  const outcome = page.locator("header .badge").first();
  await expect(outcome).toHaveText(/^(passed|sandbox unavailable)$/);
  if ((await outcome.textContent()) === "passed") {
    await expect(page.locator(".tile-value").first()).toHaveText("2/2");
    await page.getByRole("tab", { name: "Coverage", exact: true }).click();
    await expect(page.getByText("Total coverage")).toBeVisible();
  }

  // Back to the project: the trend and the overview include the new execution.
  await page.getByRole("link", { name }).click();
  await expect(
    page.getByText("The last 1 execution(s), oldest first; select one to open it"),
  ).toBeVisible();
  await page.getByRole("link", { name: "Overview" }).click();
  await expect(page.getByRole("link", { name, exact: true }).first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("the API's refusals are shown as they are", async ({ page }) => {
  await page.goto("/projects");
  await page.getByLabel("Name").fill(`e2e-outside-${Date.now()}`);
  await page.getByLabel("Directory").fill("../../etc");
  await page.getByRole("button", { name: "Register" }).click();
  await expect(page.getByRole("alert")).toContainText("invalid source");

  await page.goto(`/projects/00000000-0000-4000-8000-000000000000`);
  await expect(page.getByText("Not available")).toBeVisible();
  await page.goto("/no/such/page");
  await expect(page.getByText("Page not found")).toBeVisible();
});
