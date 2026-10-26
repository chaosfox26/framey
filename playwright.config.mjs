import { defineConfig } from "@playwright/test";

export default defineConfig({
	testDir: "./tests/browser",
	forbidOnly: true,
	timeout: 20000,
	workers: 1,
	retries: 0,
	reporter: "list",
	outputDir: "test-results",
	use: {
		browserName: "chromium",
		headless: true,
		trace: "off",
		screenshot: "only-on-failure",
	},
});
