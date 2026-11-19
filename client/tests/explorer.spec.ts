import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { PRESETS } from "../lib/presets";

const fixturesRoot = new URL("./fixtures/", import.meta.url);

for (const preset of PRESETS) {
  test(`renders synthetic ${preset.id} through the real proxy`, async ({ page, request }) => {
    const errors: string[] = [];

    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto("/");
    await page.getByRole("button", { name: preset.label }).click();
    await expect(page.getByLabel("Your clinical-trial question")).toHaveValue(preset.request.query);
    await page.getByRole("button", { name: "Submit question" }).click();
    const fixture = JSON.parse(
      readFileSync(new URL(`responses/${preset.id}.json`, fixturesRoot), "utf8"),
    );

    await expect(page.getByRole("heading", { name: fixture.visualization.title })).toBeVisible();
    const forwarded = await (await request.get("http://127.0.0.1:9010/last-request")).json();

    expect(forwarded).toEqual({ ...preset.request, include_citations: false });

    if (preset.id === "network_recruiting") {
      await expect(page.locator(".network canvas").first()).toBeVisible();
      await page.getByText("View relationships and weights", { exact: true }).click();
      await expect(page.locator("tbody tr")).toHaveCount(fixture.visualization.data.edges.length);
      const weightedEdge = page
        .getByRole("row")
        .filter({ hasText: "Synthetic Research Institute" })
        .filter({ hasText: "Synthetic Drug A" });

      await expect(weightedEdge.getByRole("cell").last()).toHaveText("2");
    } else {
      await expect(page.locator(".recharts-surface").first()).toBeVisible();
      await page.getByText("View chart data", { exact: true }).click();

      if (preset.id === "comparison_overlap")
        await expect(
          page
            .locator("tbody tr")
            .filter({ hasText: "Phase 1" })
            .filter({ hasText: "Synthetic Drug B" })
            .getByRole("cell")
            .last(),
        ).toHaveText("0");

      if (preset.id === "annual_trends")
        await expect(page.locator("tbody")).not.toContainText("-01-01");

      if (preset.id === "enrollment_recruiting")
        await expect(page.locator("tbody")).toContainText("5000+");
    }

    await page.locator(".metadata summary").first().click();
    await expect(page.locator(".metadata")).toContainText("Synthetic Research Institute");
    expect(errors).toEqual([]);
  });
}

test("keyboard, loading, independent history and mobile layout", async ({ page, request }) => {
  let submissions = 0;

  page.on("request", (r) => {
    if (r.url().endsWith("/api/visualize")) submissions += 1;
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const input = page.getByLabel("Your clinical-trial question");

  await input.fill("__slow");
  await input.press("Enter");
  await expect(page.getByRole("status")).toBeVisible();
  await expect(page.getByRole("button", { name: "Request in progress" })).toBeDisabled();
  await page.keyboard.press("Enter");
  await expect(input).toHaveValue("");
  expect(submissions).toBe(1);
  await expect(input).toBeFocused();
  await input.fill("Second independent question");
  await input.press("Shift+Enter");
  await expect(input).toHaveValue("Second independent question\n");
  await input.press("Enter");
  await expect(page.locator(".result-card")).toHaveCount(2);
  expect(submissions).toBe(2);
  const forwarded = await (await request.get("http://127.0.0.1:9010/last-request")).json();

  expect(forwarded).toEqual({ query: "Second independent question", include_citations: false });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.reload();
  await expect(page.locator(".result-card")).toHaveCount(0);
});

for (const code of [
  "unsupported_query",
  "ambiguous_query",
  "incomplete_retrieval",
  "request_timeout",
  "upstream_unavailable",
  "citation_limit_exceeded",
]) {
  test(`readable ${code} error preserves composer`, async ({ page }) => {
    await page.goto("/");
    const question = `__error:${code}`;

    await page.getByLabel("Your clinical-trial question").fill(question);
    await page.getByRole("button", { name: "Submit question" }).click();
    await expect(page.locator(".error-panel").last()).toBeVisible();
    await expect(page.getByLabel("Your clinical-trial question")).toHaveValue(question);

    if (code === "citation_limit_exceeded")
      await expect(page.locator(".error-panel").last()).toContainText("turn off source citations");
    await expect(page.locator(".result-card")).toHaveCount(0);
  });
}

test("empty, unknown response and disconnected backend are safe", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Your clinical-trial question").fill("__empty");
  await page.getByRole("button", { name: "Submit question" }).click();
  await expect(page.getByText("No chartable data", { exact: false })).toBeVisible();

  for (const query of ["__malformed", "__disconnect"]) {
    await page.getByLabel("Your clinical-trial question").fill(query);
    await page.getByRole("button", { name: "Submit question" }).click();
    await expect(page.locator(".error-panel").last()).toBeVisible();
    await expect(page.getByLabel("Your clinical-trial question")).toHaveValue(query);
  }
});

test("citations expose exact values and canonical source links", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Phase distribution" }).click();
  await page.getByLabel("Include source citations").check();
  await page.getByRole("button", { name: "Submit question" }).click();
  await page.getByText("Sources · 2 datum-to-study references", { exact: true }).click();
  await page.getByText("Phase 2 · 2 supporting studies", { exact: true }).click();
  const study = page.locator(".study").first();

  await study.locator("summary").click();
  await expect(study.locator("a")).toHaveAttribute(
    "href",
    /^https:\/\/clinicaltrials.gov\/study\/NCT\d{8}$/,
  );
  await expect(study).toContainText("/protocolSection/designModule/phases/0");
  await expect(study).toContainText('"PHASE2"');
  await expect(study).not.toContainText("/protocolSection/identificationModule/nctId");
  await expect(study.locator(".source-field .badge")).toHaveCount(0);
  await expect(study.locator("summary")).toContainText("1 source field");
});

test("proxy preserves backend bytes, status and no-store policy", async ({ request }) => {
  const response = await request.post("/api/visualize", { data: PRESETS[0].request });

  expect(response.status()).toBe(200);
  expect(await response.body()).toEqual(
    readFileSync(new URL("responses/phase_distribution.json", fixturesRoot)),
  );
  expect(response.headers()["cache-control"]).toBe("no-store");
  const failure = await request.post("/api/visualize", {
    data: { query: "__error:request_timeout" },
  });

  expect(failure.status()).toBe(504);
  expect(await failure.json()).toEqual({
    error: { code: "request_timeout", message: "Request failed: request_timeout." },
  });
});

test("absence kinds, comparison zeros and network edge citations", async ({ page }) => {
  const fixtures = JSON.parse(readFileSync(new URL("citations.json", fixturesRoot), "utf8"));

  for (const key of ["missing", "comparison", "network"]) {
    await page.goto("/");
    await page.route("**/api/visualize", (route) => route.fulfill({ json: fixtures[key] }));
    await page.getByLabel("Your clinical-trial question").fill(`Synthetic ${key} evidence`);
    await page.getByLabel("Include source citations").check();
    await page.getByRole("button", { name: "Submit question" }).click();
    await page.locator(".sources > summary").click();

    if (key === "missing") {
      await page.locator(".datum > summary").first().click();

      for (const summary of await page.locator(".study > summary").all()) await summary.click();
      await expect(page.locator(".sources")).toContainText("Field absent");

      for (const kind of ["object_keys", "null", "empty", "invalid"])
        await expect(page.locator(".sources")).toContainText(kind);
    } else if (key === "comparison") {
      await page.getByText("Phase 1 · Drug B · 0 supporting studies", { exact: true }).click();
      await expect(page.locator(".sources")).toContainText("No contributing studies");
    } else {
      await page.getByText("Sponsor A → Drug A · 2 supporting studies", { exact: true }).click();
      await expect(page.locator(".sources .study")).toHaveCount(2);
      await expect(page.getByText("Graph node evidence links", { exact: true })).toBeVisible();
    }

    await page.unroute("**/api/visualize");
  }
});

test("editable filters validate year order and send only populated fields", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page.locator(".filter-panel > summary").click();
  await page.getByLabel("Condition", { exact: true }).fill(" asthma ");
  await page.getByLabel("Drug / intervention", { exact: true }).fill("aspirin");
  await page.getByLabel("Country", { exact: true }).fill("France");
  await page.getByLabel("Sponsor / collaborator", { exact: true }).fill("Acme");
  await page.getByLabel("Status", { exact: true }).selectOption("SUSPENDED");
  await page.getByLabel("Study start: from year").fill("2025");
  await page.getByLabel("Study start: through year").fill("2020");
  await page.getByLabel("Your clinical-trial question").fill("Show trial phases");
  await page.getByRole("button", { name: "Submit question" }).click();
  await expect(page.locator(".form-error")).toContainText("Start year must not exceed end year");
  await expect(page.locator(".exchange")).toHaveCount(0);
  await page.getByLabel("Study start: from year").fill("2015");
  await page.getByRole("button", { name: "Submit question" }).click();
  await expect(page.locator(".result-card")).toHaveCount(1);
  expect(await (await request.get("http://127.0.0.1:9010/last-request")).json()).toEqual({
    query: "Show trial phases",
    condition: "asthma",
    drug_name: "aspirin",
    country: "France",
    sponsor: "Acme",
    trial_status: "SUSPENDED",
    start_year: 2015,
    end_year: 2020,
    include_citations: false,
  });
  await page.getByRole("button", { name: "Clear filters" }).click();
  await page.getByLabel("Your clinical-trial question").fill("Show trial phases again");
  await page.getByRole("button", { name: "Submit question" }).click();
  await expect(page.locator(".result-card")).toHaveCount(2);
  expect(await (await request.get("http://127.0.0.1:9010/last-request")).json()).toEqual({
    query: "Show trial phases again",
    include_citations: false,
  });
});
