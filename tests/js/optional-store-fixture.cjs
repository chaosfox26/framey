"use strict";
// Own UI integration fixture: the production optional factory is real; only the
// already-verified runtime delivery boundary is replaced. No vendor code loads.
const fs = require("node:fs");
const path = require("node:path");
const { ROOT } = require("./harness");
const status = Object.freeze({
	installed: true,
	enabled: true,
	requested_enabled: true,
	version: "1.0.0",
	previous_version: null,
	generation: 1,
	phase: "enabled",
	error: null,
});
function install(env, options = {}) {
	const w = env.window,
		K = w.__S.K;
	const source = fs.readFileSync(path.join(ROOT, "decky_runtime/frontend/store.js"), "utf8");
	const factory = w.Function(source + "\nreturn createFrameyDeckyStore;")();
	K.optionalRuntime = {
		invalidate() {},
		store(context) {
			const cap = w.FY_RUNTIME.decky_compat;
			return Promise.resolve(
				factory({
					...context,
					generation: 1,
					current: () => true,
					alive: context.alive,
					importEnabled: options.importEnabled ?? !!(cap && cap.profile === 1 && cap.import === true),
				}),
			);
		},
	};
}
module.exports = { install, status };
