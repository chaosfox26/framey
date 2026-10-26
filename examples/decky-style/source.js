(() => {
	const GOALS = ["Practice", "Read", "Plan"];
	const state = {
		goal: GOALS[0],
		count: 0,
		draft: GOALS[0],
		pending: false,
		note: "Loading saved check-ins…",
	};
	let view = null;
	let ready = null;
	let disposed = false;
	let decky;

	const element = (css, text) => {
		const node = Framey.el(css, text);
		node.dataset.fy = api.id;
		return node;
	};

	const paint = () => {
		if (!view || disposed) return;
		view.summary.textContent = state.goal + ": " + state.count + " check-ins";
		view.note.textContent = state.note;
	};

	const apply = snapshot => {
		if (!snapshot || !GOALS.includes(snapshot.goal) || !Number.isInteger(snapshot.count) || snapshot.count < 0) {
			throw new Error("Backend returned an invalid check-in snapshot");
		}
		state.goal = snapshot.goal;
		state.count = snapshot.count;
		paint();
	};

	const run = async (operation, message) => {
		if (disposed || state.pending) return;
		state.pending = true;
		state.note = "Working…";
		paint();
		try {
			const snapshot = await operation();
			if (disposed) return;
			apply(snapshot);
			state.note = message;
		} catch (error) {
			if (!disposed) state.note = "Could not complete: " + error.message;
		} finally {
			state.pending = false;
			paint();
		}
	};

	const render = async box => {
		if (!ready) {
			ready = run(() => decky.call("read_settings"), "Choose a goal, save it, then check in after a session.");
		}
		await ready;
		if (disposed) return;
		state.draft = state.goal;
		view = {
			summary: element("font-size:26px;font-weight:700;margin-top:16px", ""),
			note: element("font-size:18px;min-height:48px;margin-top:14px;overflow-wrap:anywhere", ""),
		};
		const goals = api.ui.list({
			items: GOALS.map(id => ({ id, label: id })),
			selected: state.draft,
			onselect: goal => {
				state.draft = goal;
			},
		});
		goals.style.marginTop = "16px";
		const buttons = api.ui.group(
			api.ui.button({
				label: "Save goal",
				onclick: () => run(() => decky.call("save_goal", state.draft), "Goal saved."),
			}),
			api.ui.button({
				label: "Check in",
				primary: true,
				onclick: () => run(checkIn, "Check-in saved."),
			}),
			api.ui.button({
				label: "Reset count",
				onclick: () => run(() => decky.call("reset_count"), "Count reset."),
			}),
			api.ui.button({
				label: "Refresh",
				onclick: () => run(() => decky.call("read_settings"), "Saved check-ins refreshed."),
			}),
		);
		buttons.style.marginTop = "16px";
		box.append(
			element("font-size:28px;font-weight:700", "Session Check-ins"),
			element(
				"font-size:18px;margin-top:10px",
				"Track completed practice, reading or planning sessions across reloads.",
			),
			view.summary,
			goals,
			buttons,
			view.note,
		);
		paint();
	};

	const api = Framey.register({ render });
	decky = createDeckySourceAdapter(api);
	const checkIn = decky.callable("check_in");
	const onCheckIn = snapshot => {
		if (disposed) return;
		apply(snapshot);
		state.note = "Backend event received: check-in " + snapshot.count + ".";
		paint();
	};
	decky.addEventListener("check_in", onCheckIn);
	api.cleanup(() => {
		disposed = true;
		decky.removeEventListener("check_in", onCheckIn);
		decky.cleanup();
		view = null;
	});
})();
