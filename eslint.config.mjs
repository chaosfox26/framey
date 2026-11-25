import js from "@eslint/js";
import globals from "globals";

export default [
	{
		files: ["*.mjs"],
		languageOptions: { sourceType: "module", globals: globals.node },
		rules: js.configs.recommended.rules,
	},
	{
		files: ["*.js", "compat/*.js", "decky_runtime/**/*.js", "examples/*/*.js"],
		languageOptions: {
			ecmaVersion: 2022,
			sourceType: "script",
			globals: {
				...globals.browser,
				__FY: "readonly",
				__fyt: "readonly",
				__trusted: "readonly",
				Framey: "readonly",
				FY_ICON_URL: "readonly",
			},
		},
		rules: {
			...js.configs.recommended.rules,
			"no-useless-return": "error",
			"no-unused-vars": ["error", { args: "none", caughtErrors: "none" }],
			"no-empty": ["error", { allowEmptyCatch: true }],
		},
	},
	{
		files: ["inject.js"],
		languageOptions: {
			globals: { __FY: "off", __trusted: "off" },
		},
		rules: {
			"no-unused-vars": ["error", { varsIgnorePattern: "^__FY$", args: "none", caughtErrors: "none" }],
		},
	},
	{
		files: ["compat/*.js", "decky_runtime/**/*.js", "examples/decky-style/main.js"],
		languageOptions: { globals: { module: "readonly" } },
	},
	{
		files: ["decky_runtime/client.js"],
		languageOptions: {
			globals: {
				createFrameyDeckyAssets: "readonly",
				createFrameyDeckyHost: "readonly",
				createFrameyDeckyMenus: "readonly",
				createFrameyDeckyModules: "readonly",
				createFrameyDeckyNavigation: "readonly",
				createFrameyDeckySidebar: "readonly",
				createFrameyDeckyStore: "readonly",
				createFrameyViewRuntime: "readonly",
			},
		},
	},
	{
		files: ["decky_runtime/frontend/store.js"],
		rules: {
			"no-unused-vars": ["error", { varsIgnorePattern: "^createFrameyDeckyStore$", args: "none", caughtErrors: "none" }],
		},
	},
	{
		files: ["examples/decky-style/source.js"],
		languageOptions: { globals: { createDeckySourceAdapter: "readonly" } },
	},
];
