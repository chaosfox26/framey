# Testing

Run these commands from the repository root on Linux as an ordinary user. The
Python tests use `unittest` and the standard library. Browser tests use Node.js
and jsdom; there is no application build step or Python package installation.

## Reproduce the CI environment

The fixture workflow pins Python 3.14.8, Node.js 26.12.1 and jsdom 30.1.2. Its npm
lockfile records transitive dependency versions and integrity hashes. GitHub
Actions are pinned to commit hashes. Hosted runner images can still change;
the workflow prints the selected OS, architecture and tool versions.

Install only the browser test dependency into a temporary directory:

```bash
export FRAMEY_JSDOM="$(mktemp -d)"
cp .github/ci/package.json .github/ci/package-lock.json "$FRAMEY_JSDOM/"
npm ci --prefix "$FRAMEY_JSDOM" --ignore-scripts --no-audit --no-fund
export FRAMEY_PYTHON="$(command -v python3)"
```

`FRAMEY_JSDOM` names the directory containing `node_modules`, rather than the
jsdom package itself. An existing jsdom 30.1.2 installation can be used by setting
this variable to its parent directory. Remove the temporary directory when done.

Run the Python suite, including local subprocess, filesystem, socket, inotify and
mocked loader integration tests:

```bash
timeout --signal=TERM --kill-after=10s 12m \
  python3 -I -m unittest discover -s tests -p 'test_fy_*.py' -v
```

Run every browser test serially; these scripts supply their own test runners:

```bash
failed=0
for test_file in tests/js/*.test.js; do
  printf '\nRunning %s\n' "$test_file"
  if timeout --signal=TERM --kill-after=10s 120s node "$test_file"; then
    :
  else
    status=$?
    printf 'FAILED %s (exit %s)\n' "$test_file" "$status"
    failed=1
  fi
done
exit "$failed"
```

For a focused rerun, use `python3 -I -m unittest discover -s tests -p
test_fy_proto.py -v` or `node tests/js/api.test.js`, with the same environment.
Python discovery also invokes the generated-expression browser test, so that
specific test runs again in the complete browser pass.

The discovery patterns include every `tests/test_fy_*.py` and
`tests/js/*.test.js` file, including new catalog assessment, source adapter,
generated example, and plugin tooling checks. A clean checkout needs the
`compat/`, `examples/decky-style/`, `tools/`, `types/`, `catalog/` and test fixture
directories. Do not copy only the root runtime files when reproducing CI.
`test_fy_decky_example.py` compares the committed example bundle with its shared
adapter and source, so editing either without regenerating `main.js` fails CI.
Optional TypeScript compilation is skipped when neither `FRAMEY_TSC` nor `tsc`
is available; the locked jsdom dependency does not install that compiler.

## Assessment fixtures and network boundaries

After dependency setup, the scoped catalog and archive assessment tests use small
synthetic feed records and locally generated archives. They do not need a live
public feed, a complete public plugin list, remote images, or author identities.
Browsing and version selection tests assert that no archive assessment occurs;
assessment tests supply a selected archive through a fixture fetcher and check its
identity, metadata, and architecture evidence without running its code.

CI also runs this bounded check:

```bash
timeout --signal=TERM --kill-after=10s 120s \
  python3 -I .github/ci/offline_assessment.py
```

This launcher discovers all `test_fy_decky_*.py` files and installs a Python audit
hook before importing them. Its self-check verifies that external connection,
DNS lookup and datagram-send attempts raise before reaching the network.
Literal loopback connections and local Unix sockets remain available for the
controlled transport tests; loopback reverse lookup returns a fixed local answer
without querying DNS. This catches accidental use of the live service in those
trusted Python tests. The guard is
limited to audited operations in that interpreter; it does not isolate child
processes, native code, or the browser test process. It is not a sandbox for
untrusted plugin code. Actions setup and `npm ci` still require network access,
and the complete workflow does not run inside a disconnected network namespace.

The assessment fixtures establish how the supplied metadata and archive bytes are
handled. They do not establish the current contents or availability of a public
store, coverage of its full catalog, successful execution of an original Decky
bundle, or compatibility with Decky's React UI, SDK, Steam, or physical hardware.
An ARM64 header is evidence about that binary; it does not establish those other
properties. Source-port adapter and example tests cover the deliberately supported
native Framey contracts described in [Decky compatibility](decky-compatibility.md).

## What these tests establish

The files named `test_fy_integration_*.py` use temporary installations and mocked
network/loader interfaces from `tests/fixtures/j/jharness.py`. Privileged-update
tests replace sudo with a local fixture program. They generate disposable local
signing keys with `ssh-keygen`; they do not connect over SSH. The workflow checks
that this executable is available so its absence does not silently skip signature
tests. Linux subprocess tests exercise actual local process boundaries and resource
limits, but do not control device hardware or require root.

Browser tests emulate a page in jsdom with canned backend replies. The generated
loader-expression fixture defaults to `tests/fixtures/j/legacy_plugin.js`, a small
synthetic compatibility probe. `FRAMEY_FAN_MAIN` can override that file for a
separate compatibility check against an independently supplied plugin. Passing
the default fixture does not establish compatibility with a real fan plugin,
Steam's embedded browser, or any physical device.

Real device verification is separate: in a controlled test installation, check
the Steam integration, lifecycle, input, display scaling, and any
hardware-dependent plugin behavior. A green
fixture workflow does not establish those results. Never point the fixture suite
at a live installation, and do not add privileged or hardware actions to CI.

## CI coverage and failure handling

`.github/workflows/fixture-tests.yml` requests Ubuntu 24.04 runners on x64 and
native ARM64 and checks the actual machine architecture. GitHub lists
`ubuntu-24.04-arm` among its [standard hosted runner
labels](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
The two jobs run independently, so a failure on one architecture does not cancel
the other. These are Linux fixture checks, not device integration checks.

Each job has a 25-minute limit, the complete Python pass has a 12-minute limit,
and the offline assessment pass and each browser script have a 120-second limit
followed by up to ten seconds for termination.
The browser pass still runs after a Python failure and reports all failing script
names. A timeout, signal, missing dependency, out-of-memory kill or nonzero test
exit fails the check; do not interpret an incomplete run as a pass. Keep timeout
or resource failures visible and investigate their logs before changing a limit.
Report skipped tests explicitly when recording local verification.

The workflow uses read-only repository permissions, disables persisted checkout
credentials, and requires no repository secrets. It performs no deployment or
release operation. Adding this workflow does not mean it has run: only an actual
Actions result establishes its hosted-runner status.

When updating the tools or jsdom, change the explicit versions, update the npm
lockfile, verify Action tags against the official `actions` repositories, and run
both test commands. Keep the lockfile's registry URLs and integrity hashes; do
not copy a machine-specific dependency path into the repository.
