type FrameyUnsubscribe = () => void;
type FrameyLogLevel = "debug" | "info" | "warning" | "error";
type FrameyPageMode = "panel" | "full";
type FrameyMountZone = "corner" | "overlay" | "bar";
type FrameyTranslationVars = Readonly<Record<string, string | number>>;

interface FrameyErrorResult {
  error: string;
  code?: string;
}

type FrameyResult<T> = T | FrameyErrorResult;

interface FrameyApiError extends Error {
  code?: string;
}

type FrameyCatalogCompatibilityStatus = "compatible" | "incompatible" | "unverified";
type FrameyDeckyBinaryFormat = "ELF" | "PE" | "Mach-O";
type FrameyDeckyBinaryArchitecture = "aarch64" | "x86_64" | "x86" | "arm" | "unknown";
type FrameyDeckyArchiveArchitecture = "unknown" | "mixed" | "aarch64" | "x86_64" | "other";
type FrameyDeckyArchitectureStatus = "unverified" | "incompatible" | "evidence_only";

interface FrameyDeckyNativeBinary {
  path: string;
  format: FrameyDeckyBinaryFormat;
  architecture: FrameyDeckyBinaryArchitecture;
}

interface FrameyDeckyCatalogSource {
  id: "decky_stable" | "decky_testing";
  label: string;
  url: string;
  format: "decky";
}

interface FrameyDeckyArchiveAssessment {
  state: "port_required";
  status: "incompatible";
  label: string;
  architecture: FrameyDeckyArchiveArchitecture;
  architecture_status: FrameyDeckyArchitectureStatus;
  root_required: boolean;
  native_binaries: FrameyDeckyNativeBinary[];
  native_binary_count: number;
  native_binaries_truncated: boolean;
  target_platform: string;
  runtime_verified: false;
  basis: "static_package_inspection";
  has_frontend: boolean;
  has_backend: boolean;
  flags: string[];
  dependencies: string[];
  hardware_assumptions: string[];
  reasons: string[];
  warnings: string[];
  verified_sha256: true;
  bytes: number;
  files: number;
  not_executed: true;
}

interface FrameyDeckyAssessmentReply {
  id: string;
  format: "decky";
  read_only: true;
  version: string;
  hash: string;
  source: FrameyDeckyCatalogSource;
  artifact: string;

  identity: string;
  version_identity: string;
  checked_at: number;
  downloaded_at: number;
  assessment: FrameyDeckyArchiveAssessment;
}

type FrameyDeckyAssessmentResult = FrameyResult<FrameyDeckyAssessmentReply>;

interface FrameyRuntime {
  readonly framey: string;
  readonly api: number;
  readonly platform: string;
  readonly lang: string;
  readonly plugin: string;
  readonly version: string;
}

interface FrameyEventInfo {
  name: string;
  dropped: number;
  open: boolean;
  tab: string;
}

type FrameyEventHandler<T = unknown> = (payload: T, info: FrameyEventInfo) => void;

interface FrameyEvents {
  on<T = unknown>(name: string, fn: FrameyEventHandler<T>): FrameyUnsubscribe;
  once<T = unknown>(name: string, fn: FrameyEventHandler<T>): FrameyUnsubscribe;
  off<T = unknown>(name: string, fn: FrameyEventHandler<T>): void;
}

interface FrameyNotifyAction {
  id: string;
  label: string;
}

interface FrameyNotifyOptions {
  title?: string;
  body?: string;
  icon?: string;
  duration?: number;
  actions?: FrameyNotifyAction[];
  onaction?: (actionId: string) => void;
  onclose?: (reason: "timeout" | "dismiss" | "action" | "dropped") => void;
}

interface FrameyNotification {
  id: number;
  dismiss(): void;
}

interface FrameyApiI18n {
  add(lang: string, dict: Readonly<Record<string, string>>): number;
  load(lang: string): Promise<boolean>;
}

interface FrameyFetchOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE" | "HEAD";
  headers?: Record<string, string>;
  body?: string;
  max_bytes?: number;
  timeout?: number;
}

interface FrameyFetchResult {
  status: number;
  headers: Record<string, string>;
  text: string | null;
  b64: string | null;
  bytes: number;
  truncated: boolean;
  path: null;
}

interface FrameyUiButtonOptions {
  label: string;

  onclick?: (event: MouseEvent | KeyboardEvent) => void;
  disabled?: boolean;
  primary?: boolean;
  css?: string;
}

interface FrameyUiToggleOptions {
  label: string;
  value?: boolean;
  onchange?: (value: boolean) => void;
  disabled?: boolean;
}

interface FrameyUiSliderOptions {
  label: string;
  min?: number;
  max?: number;
  step?: number;
  value?: number;
  oninput?: (value: number) => void;
  onchange?: (value: number) => void;
  disabled?: boolean;
  format?: (value: number) => string;
}

type FrameyUiListId = string | number;
type FrameyUiListSelection = FrameyUiListId | FrameyUiListId[] | null;

interface FrameyUiListItem {
  id?: FrameyUiListId;
  label: string;
  sub?: string;
  disabled?: boolean;
}

interface FrameyUiListOptions {
  items?: FrameyUiListItem[];
  selected?: FrameyUiListSelection;
  onselect?: (value: FrameyUiListSelection) => void;
  multi?: boolean;
  empty?: string;
}

interface FrameyUiSingleListOptions extends Omit<
  FrameyUiListOptions,
  "selected" | "onselect" | "multi"
> {
  selected?: FrameyUiListId | null;
  onselect?: (value: FrameyUiListId | null) => void;
  multi?: false;
}

interface FrameyUiMultiListOptions extends Omit<FrameyUiListOptions, "onselect" | "multi"> {
  onselect?: (value: FrameyUiListId[]) => void;
  multi: true;
}

interface FrameyUiControl extends HTMLDivElement {
  disabled: boolean;
}

interface FrameyUiToggle extends FrameyUiControl {
  value: boolean;
}

interface FrameyUiSlider extends FrameyUiControl {
  value: number;
}

interface FrameyUiList extends HTMLDivElement {
  readonly selected: FrameyUiListSelection;
  setItems(items: FrameyUiListItem[]): void;
}

interface FrameyUiSingleList extends FrameyUiList {
  readonly selected: FrameyUiListId | null;
}

interface FrameyUiMultiList extends FrameyUiList {
  readonly selected: FrameyUiListId[];
}

interface FrameyUi {
  button(options: FrameyUiButtonOptions): FrameyUiControl;
  toggle(options: FrameyUiToggleOptions): FrameyUiToggle;
  slider(options: FrameyUiSliderOptions): FrameyUiSlider;
  list(options: FrameyUiMultiListOptions): FrameyUiMultiList;
  list(options?: FrameyUiSingleListOptions): FrameyUiSingleList;
  list(options: FrameyUiListOptions): FrameyUiList;
  group(...children: (Node | string)[]): HTMLDivElement;
  focus(element: HTMLElement | null | undefined): void;
}

interface FrameyPageContext {
  id: string;
  plugin: string;
  mode: FrameyPageMode;
  api: FrameyApi;
  close(): void;
}

interface FrameyPageOptions {
  id: string;
  title?: string;
  mode?: FrameyPageMode;

  render(box: HTMLElement, ctx: FrameyPageContext): void;
  onclose?: () => void;
}

interface FrameyPageHandle {
  open(): boolean;
  close(): boolean;
  remove(): boolean;
}

interface FrameyPageInfo {
  id: string;
  key: string;
  title: string;
  mode: FrameyPageMode;
  open: boolean;
}

interface FrameyPages {
  register(options: FrameyPageOptions): FrameyResult<FrameyPageHandle>;
  open(id: string): boolean;
  close(): void;
  list(): FrameyPageInfo[];
}

interface FrameyMountOptions {
  render(element: HTMLElement): void;
  zone?: FrameyMountZone;
}

interface FrameyMountHandle {
  el: HTMLDivElement;
  unmount(): void;
}

interface FrameyGlobalZones {
  mount(id: string, options: FrameyMountOptions): FrameyResult<FrameyMountHandle>;
}

interface FrameyTargetSuccess {
  ok: true;
}

interface FrameyTarget {
  css(key: string, text: string): Promise<FrameyResult<FrameyTargetSuccess>>;
  js(key: string, code: string, teardown?: string): Promise<FrameyResult<FrameyTargetSuccess>>;
  remove(key?: string): Promise<FrameyResult<FrameyTargetSuccess>>;
}

interface FrameyPickOptions {
  root?: string;
  path?: string;
  exts?: string[];
  title?: string;
}

interface FrameyPickResult {
  root: string;
  path: string;
  name?: string;
}

interface FrameyPickFileResult extends FrameyPickResult {
  name: string;
}

interface FrameyPickFolderResult extends FrameyPickResult {
  name?: never;
}

interface FrameyApi {
  readonly id: string;
  readonly runtime: FrameyRuntime;
  readonly visible: boolean;
  readonly lang: string;
  readonly events: FrameyEvents;
  readonly i18n: FrameyApiI18n;
  timeout(fn: () => void, ms: number): number;
  interval(fn: () => void, ms: number): number;
  clear(handle: number): void;
  on<K extends keyof HTMLElementEventMap>(
    target: HTMLElement,
    type: K,
    fn: (event: HTMLElementEventMap[K]) => void,
    options?: boolean | AddEventListenerOptions,
  ): FrameyUnsubscribe;
  on<K extends keyof DocumentEventMap>(
    target: Document,
    type: K,
    fn: (event: DocumentEventMap[K]) => void,
    options?: boolean | AddEventListenerOptions,
  ): FrameyUnsubscribe;
  on<K extends keyof WindowEventMap>(
    target: Window,
    type: K,
    fn: (event: WindowEventMap[K]) => void,
    options?: boolean | AddEventListenerOptions,
  ): FrameyUnsubscribe;
  on(
    target: EventTarget,
    type: string,
    fn: EventListenerOrEventListenerObject,
    options?: boolean | AddEventListenerOptions,
  ): FrameyUnsubscribe;

  cleanup(fn: () => void): FrameyUnsubscribe;

  call<T = unknown>(method: string, arg?: unknown, timeoutMs?: number): Promise<FrameyResult<T>>;
  raf(fn: (time: number) => void): number;
  notify(options: FrameyNotifyOptions): FrameyNotification;
  onVisible(fn: (visible: boolean) => void): FrameyUnsubscribe;
  t(key: string, vars?: FrameyTranslationVars): string;
  fetch(url: string, options?: FrameyFetchOptions): Promise<FrameyFetchResult>;
  resource(root: "data" | "runtime", path: string, ttl?: number): Promise<string>;
  revoke(url: string): Promise<boolean>;
  log(level: FrameyLogLevel, msg: string): void;
  readonly ui: FrameyUi;
  readonly pages: FrameyPages;
  readonly global: FrameyGlobalZones;
  target(name: string): FrameyTarget;

  pickFile?(options?: FrameyPickOptions): Promise<FrameyPickFileResult | null>;
  pickFolder?(options?: FrameyPickOptions): Promise<FrameyPickFolderResult | null>;
}

interface FrameyPlugin {
  id?: string;
  name?: string;
  short?: string;

  render(box: HTMLElement, api: FrameyApi): void;
  dispose?(): void;
}

interface FrameyRegisteredPlugin extends FrameyPlugin {
  readonly id: string;
  readonly name: string;
  readonly short: string;
  readonly meta: Readonly<Record<string, unknown>>;
  readonly dead?: true;
}

interface FrameyI18n {
  lang(): string;
  setLang(lang: string): boolean;
  add(plugin: string, lang: string, dict: Readonly<Record<string, string>>): number;
  t(plugin: string, key: string, vars?: FrameyTranslationVars): string;
}

interface FrameyDiagPlugin {
  id: string;
  timers: number;
  intervals: number;
  listeners: number;
  subscriptions: number;
  pages: number;
  globals: number;
  targets: number;
  notifications: number;
  nodes: number;
}

interface FrameyDiag {
  plugins: FrameyDiagPlugin[];
  calls: number;
  notifications: number;
  subs: number;
  heap: number | null;
  untrusted_clicks: number;
}

interface FrameyBinding {
  readonly _meta: Readonly<Record<string, unknown>>;
  register(plugin: FrameyPlugin): FrameyApi;
  call<T = unknown>(
    _plugin: string,
    method: string,
    arg?: unknown,
    timeoutMs?: number,
  ): Promise<FrameyResult<T>>;
  dispose(): void;
  readonly panelOpen: boolean;
  el(css: string, text?: string): HTMLDivElement;
  button(text: string, css: string, fn: (ev: MouseEvent | KeyboardEvent) => void): HTMLDivElement;
  toast(text: string, ms?: number): void;
  fail(id: string, error: Error): void;
  listen<T = unknown>(name: string, fn: FrameyEventHandler<T>): FrameyUnsubscribe;
  notify(options: FrameyNotifyOptions): FrameyNotification;
  onPanel(fn: (open: boolean) => void): FrameyUnsubscribe;
  lazy(name: "picker" | "dev"): Promise<void>;
  open(tab?: string): void;
  toggle(): void;
  restore(): void;
  redraw(page?: string): void;
}

interface FrameyBuiltinPage {
  render(box: HTMLElement): void | PromiseLike<void>;
}

interface FrameyPanelPage {
  id: string;
  plugin: string;
  name: string;
  short: string;
  leave(): void;
  render(box: HTMLElement): void;
}

interface FrameyGlobal {
  readonly plugins: readonly Readonly<FrameyRegisteredPlugin>[];
  readonly panelOpen: boolean;
  readonly pages: Readonly<Partial<Record<string, FrameyBuiltinPage>>>;
  dev?: { render(box: HTMLElement): Promise<void>; text(): Promise<string> };
  open(tab?: string): void;
  toggle(): void;
  redraw(page?: string): void;
  restore(): void;
  panelPages(): FrameyPanelPage[];
  fail(id: string, error: Error): void;
  readonly i18n: FrameyI18n;
  dispose(id?: string, token?: string): void;
  call<T = unknown>(plugin: string, method: string, arg?: unknown): Promise<FrameyResult<T>>;
  toast(text: string, ms?: number): void;
  el(css: string, text?: string): HTMLDivElement;
  button(text: string, css: string, fn: (ev: MouseEvent | KeyboardEvent) => void): HTMLDivElement;
  lazy(name: "picker" | "dev"): Promise<void>;
  onPanel(fn: (open: boolean) => void): FrameyUnsubscribe;
  notify(options: FrameyNotifyOptions): FrameyNotification;
  diag(): FrameyDiag;
  _panel(open: boolean): void;
}

interface Window {
  readonly Framey: FrameyGlobal;
}

declare const Framey: FrameyBinding;
