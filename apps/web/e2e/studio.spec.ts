import { expect, test as base } from "@playwright/test";

const project = "/projects/prj_demo_reel_01";
const reel = "How Black Holes Bend Time";
const lesson = "How Transformers & AI Attention Work";

const test = base.extend({
  page: async ({ page, request }, use) => {
    const consoleErrors: string[] = [];
    const pageErrors: string[] = [];
    page.on("console", message => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("pageerror", error => pageErrors.push(error.stack ?? error.message));
    const status = await request.get("/api/backend-status");
    expect(status.status()).toBe(200);
    expect(await status.json(), "Demo journeys require an offline API").toMatchObject({ online: false });
    await use(page);
    expect({ consoleErrors, pageErrors }).toEqual({ consoleErrors: [], pageErrors: [] });
  },
});

test("Library filters, duplicates, renames and deletes a project", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Phase 0 Interactive Demo Mode")).toBeVisible();
  for (const metric of ["Total Projects", "Active Colab GPUs", "Est. Colab Budget", "HOTL QA Review Queue"]) {
    await expect(page.getByText(metric, { exact: true })).toBeVisible();
  }
  await expect(page.getByRole("link", { name: /1-Minute Viral Reel/ })).toHaveAttribute("href", "/create?preset=reel_60s");
  const titles = page.getByRole("heading", { level: 3 });
  const search = page.getByRole("searchbox", { name: "Search projects" });
  await search.fill("transformers");
  await expect(titles).toHaveText([lesson]);
  await search.fill("unknown-title");
  await expect(page.getByText("No matching projects.")).toBeVisible();
  await search.fill("");
  const statuses = page.getByRole("group", { name: "Project status" });
  const expected: Record<string, string[]> = {
    Planned: [], Generating: [reel], Ready: [lesson], "Needs Review": [reel], All: [reel, lesson],
  };
  for (const [status, projects] of Object.entries(expected)) {
    const filter = statuses.getByRole("button", { name: status, exact: true });
    await filter.click();
    await expect(filter).toHaveAttribute("aria-pressed", "true");
    await expect(titles).toHaveText(projects);
  }

  await search.fill(reel);
  await page.getByRole("button", { name: "Duplicate" }).click();
  await search.fill("(copy)");
  await expect(titles).toHaveText([reel + " (copy)"]);
  await page.getByRole("button", { name: "Rename" }).click();
  await page.getByRole("textbox", { name: "Project title" }).fill("Gravity study");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await search.fill("Gravity study");
  await expect(titles).toHaveText(["Gravity study"]);
  const remove = page.getByRole("button", { name: "Delete", exact: true });
  const dialog = page.getByRole("dialog", { name: "Delete project?" });
  await remove.click();
  await expect(dialog).toContainText("Gravity study");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(titles).toHaveText(["Gravity study"]);
  await remove.click();
  await dialog.getByRole("button", { name: "Delete project" }).click();
  await expect(titles).toHaveCount(0);
  await page.reload();
  await expect(titles).toHaveText([reel, lesson]);
});

test("The vertical reel preset creates a project with the selected director", async ({ page }) => {
  await page.goto("/create?preset=reel_60s");
  await expect(page.getByRole("button", { name: /9:16/ })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByLabel("Duration Preset")).toHaveValue("reel_60s");
  const brain = page.getByLabel("AI Director Brain");
  await expect(brain).toHaveValue("gemini_auto");
  await brain.selectOption("chatgpt_bridge");
  await expect(page.getByRole("button", { name: "Copy Director Brief" })).toBeVisible();
  await brain.selectOption("gemini_auto");
  await expect(page.getByRole("button", { name: "Copy Director Brief" })).toHaveCount(0);
  await brain.selectOption("colab_ai");
  await expect(brain).toHaveValue("colab_ai");
  await page.getByLabel("Video Title").fill("An ocean in motion");
  await page.getByLabel("Topic & Learning Goal").fill("Explain the twice-daily tides.");
  await page.getByRole("button", { name: "Create Storyboard" }).click();
  await expect(page).toHaveURL(/\/projects\/prj_demo_[\w-]+\/plan$/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("An ocean in motion");
});

test("Plan board reviews scenes, converts a clip and opens the JSON drawer", async ({ page }) => {
  await page.goto(project + "/plan");
  await expect(page.getByText(/Global Visual Style Bible/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Model QA Analytics" })).toBeVisible();
  for (const model of ["ltx25_fp8", "wan22_i2v", "qwen_image"]) {
    await expect(page.getByRole("heading", { name: model })).toBeVisible();
  }
  await expect(page.getByRole("heading", { name: /Scene Storyboard.*4 Scenes/i })).toBeVisible();
  const approve = page.getByRole("button", { name: "Approve & Render Clip" });
  await approve.click();
  await expect(approve).toHaveCount(0);
  const convert = page.getByRole("button", { name: "Convert to Diagram" });
  await expect(convert).toHaveCount(2);
  await convert.first().click();
  await expect(convert).toHaveCount(1);
  const summary = page.getByText("Import ChatGPT JSON", { exact: true });
  await summary.click();
  await expect(page.getByLabel("Director brief")).toHaveValue(/\S/);
  await expect(page.getByLabel("Storyboard JSON")).toBeVisible();
  await summary.click();
  await expect(page.getByLabel("Storyboard JSON")).toBeHidden();
});

test("Editor applies scene edits, splits at the playhead, and undoes and redoes the split", async ({ page }) => {
  await page.goto(project + "/editor");
  await expect(page.getByRole("button", { name: "Play video" })).toBeAttached();
  await page.getByRole("button", { name: /^#02/ }).click();
  const panel = page.getByRole("tabpanel", { name: "Scene", exact: true });
  await expect(panel).toContainText("scn_002");
  const narration = "Gravity changes the rate at which clocks tick.";
  await panel.getByLabel("Scene narration").fill(narration);
  await panel.getByRole("button", { name: "Apply Scene Edit" }).click();
  await expect(page.getByRole("button", { name: /^#02/ })).toContainText(narration);

  const clips = page.getByTestId(/^timeline-clip-/);
  await expect(clips).toHaveCount(4);
  await page.getByRole("button", { name: "+ Add Diagram Scene" }).click();
  await expect(clips).toHaveCount(5);
  await page.getByRole("button", { name: "+ Add AI Scene" }).click();
  await expect(clips).toHaveCount(6);
  const ruler = page.getByTestId("timeline-ruler");
  const bounds = await ruler.boundingBox();
  if (!bounds) throw new Error("Timeline ruler has no layout");
  await ruler.click({ position: { x: bounds.width * 0.08, y: bounds.height / 2 } });
  await page.getByRole("button", { name: "Split at Playhead" }).click();
  const rightHalf = page.getByTestId("timeline-clip-scn_001_s2");
  await expect(rightHalf).toBeVisible();
  await expect(clips).toHaveCount(7);
  await page.getByRole("button", { name: /^Undo/ }).click();
  await expect(rightHalf).toHaveCount(0);
  await expect(clips).toHaveCount(6);
  await page.getByRole("button", { name: "Redo (1)", exact: true }).click();
  await expect(rightHalf).toBeVisible();
  await expect(clips).toHaveCount(7);
});

test("Editor manages music, caption overlays, revision history and the Copilot review", async ({ page }) => {
  await page.goto(project + "/editor");
  await page.getByRole("button", { name: /^#01/ }).click();
  await page.getByRole("tab", { name: "Audio & Music" }).click();
  const audio = page.getByRole("tabpanel", { name: "Audio & Music" });
  await audio.getByRole("button", { name: "Ambient pulse" }).click();
  await expect(audio).toContainText("Current track · ambient pulse");
  const remove = audio.getByRole("button", { name: "Remove Music" });
  await remove.click();
  await expect(remove).toBeDisabled();
  await page.getByRole("tab", { name: "Captions & Overlays" }).click();
  const captions = page.getByRole("tabpanel", { name: "Captions & Overlays" });
  await captions.getByLabel("Overlay text").fill("Earth orbit");
  await captions.getByRole("button", { name: "Add Overlay" }).click();
  const overlay = captions.getByRole("listitem").filter({ hasText: "Earth orbit" });
  await expect(overlay).toBeVisible();
  await overlay.getByRole("button", { name: "Delete overlay Earth orbit" }).click();
  await expect(overlay).toHaveCount(0);
  await page.getByRole("tab", { name: "Scene", exact: true }).click();
  await expect(page.getByLabel("Scene narration")).toBeVisible();
  await page.getByRole("button", { name: "Revision History" }).click();
  const history = page.getByRole("dialog", { name: "Revision History" });
  await expect(history.getByRole("listitem").first()).toContainText("Revision");
  await history.getByRole("button", { name: "Close history" }).click();
  await expect(history).toHaveCount(0);
  await page.getByRole("button", { name: "Review Whole Draft" }).click();
  const review = page.getByRole("region", { name: "Draft review" });
  for (const name of ["Pacing", "Hook retention", "Diagram density"]) {
    await expect(review.getByRole("heading", { name, exact: true })).toBeVisible();
  }
});

for (const resolution of ["Native 1080p MP4", "4K Master (FlashVSR+)"]) {
  test("Export center previews the offline render action for " + resolution, async ({ page }) => {
    await page.goto(project + "/exports");
    await expect(page.getByRole("heading", { name: "Export & Laptop Download Center" })).toBeVisible();
    await expect(page.getByText("Your first export will appear here when queued.")).toBeVisible();
    await page.getByRole("button", { name: "Render " + resolution }).click();
    await expect(page.getByRole("status").filter({ hasText: "Start FastAPI and a render worker" })).toBeVisible();
  });
}

test("Workers shows queue and vault telemetry and offers the three processing profiles", async ({ page }) => {
  await page.goto("/workers");
  await expect(page.getByRole("heading", { name: "System Health & Telemetry" })).toBeVisible();
  await expect(page.getByText(/Demo metrics.*offline/)).toBeVisible();
  for (const name of ["Job queue", "Dead-letter queue", "Vault sync · local", "Snapshot coverage"]) {
    await expect(page.getByRole("heading", { name })).toBeVisible();
  }
  const profile = page.getByLabel("Model Profile");
  for (const value of ["ltx25_pro", "media_local", "render_local"]) {
    await profile.selectOption(value);
    await expect(profile).toHaveValue(value);
  }
});

test("Settings copies the Antigravity MCP configuration to the clipboard", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Studio Settings & Integrations" })).toBeVisible();
  await page.getByRole("button", { name: "Copy MCP Config" }).click();
  await expect(page.getByText("MCP config copied.")).toBeVisible();
  const config = JSON.parse(await page.evaluate(() => navigator.clipboard.readText()));
  expect(config.mcpServers["kinetiq-studio"]).toMatchObject({
    url: expect.stringMatching(/\/mcp$/),
    headers: { Authorization: "Bearer <YOUR_MCP_ACCESS_TOKEN>" },
  });
});
