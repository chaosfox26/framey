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

interface FrameyEvents {
  on(name: string, fn: (payload: any, info: FrameyEventInfo) => void): () => void;
  once(name: string, fn: (payload: any, info: FrameyEventInfo) => void): () => void;
  off(name: string, fn: (payload: any, info: FrameyEventInfo) => void): void;
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
  add(lang: string, dict: Record<string, string>): number;
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
  path: string | null;
}

interface FrameyUiButtonOptions {
  label: string;
  onclick?: (event: Event) => void;
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

interface FrameyUiListItem {
  id?: string | number;
  label: string;
  sub?: string;
  disabled?: boolean;
}

interface FrameyUiListOptions {
  items?: FrameyUiListItem[];
  selected?: string | number | Array<string | number> | null;
  onselect?: (value: any) => void;
  multi?: boolean;
  empty?: string;
}

interface FrameyUi {
  button(options: FrameyUiButtonOptions): HTMLElement;
  toggle(options: FrameyUiToggleOptions): HTMLElement;
  slider(options: FrameyUiSliderOptions): HTMLElement;
  list(options: FrameyUiListOptions): HTMLElement;
  group(...children: HTMLElement[]): HTMLElement;
  focus(element: HTMLElement): void;
}

interface FrameyPageContext {
  id: string;
  plugin: string;
  mode: "panel" | "full";
  api: FrameyApi;
  close(): void;
}

interface FrameyPageOptions {
  id: string;
  title?: string;
  mode?: "panel" | "full";
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
  mode: "panel" | "full";
  open: boolean;
}

interface FrameyPages {
  register(options: FrameyPageOptions): FrameyPageHandle | { error: string };
  open(id: string): boolean;
  close(): void;
  list(): FrameyPageInfo[];
}

interface FrameyMountOptions {
  render(element: HTMLElement): void;
  zone?: "corner" | "overlay" | "bar";
}

interface FrameyMountHandle {
  el: HTMLElement;
  unmount(): void;
}

interface FrameyGlobalZones {
  mount(id: string, options: FrameyMountOptions): FrameyMountHandle | { error: string };
}

interface FrameyTarget {
  css(key: string, text: string): Promise<any>;
  js(key: string, code: string, teardown?: string): Promise<any>;
  remove(key: string): Promise<any>;
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
  on(target: EventTarget, type: string, fn: EventListenerOrEventListenerObject, options?: boolean | AddEventListenerOptions): () => void;
  cleanup(fn: () => void): () => void;
  call(method: string, arg?: any): Promise<any>;
  raf(fn: (time: number) => void): number;
  notify(options: FrameyNotifyOptions): FrameyNotification;
  onVisible(fn: (visible: boolean) => void): () => void;
  t(key: string, vars?: Record<string, string | number>): string;
  fetch(url: string, options?: FrameyFetchOptions): Promise<FrameyFetchResult>;
  resource(root: string, path: string, ttl?: number): Promise<string>;
  revoke(url: string): Promise<boolean>;
  log(level: "debug" | "info" | "warning" | "error", msg: string): void;
  readonly ui: FrameyUi;
  readonly pages: FrameyPages;
  readonly global: FrameyGlobalZones;
  target(name: string): FrameyTarget;
  pickFile?(options?: FrameyPickOptions): Promise<FrameyPickResult | null>;
  pickFolder?(options?: FrameyPickOptions): Promise<FrameyPickResult | null>;
}

interface FrameyPlugin {
  id?: string;
  name?: string;
  short?: string;
  render(box: HTMLElement, api: FrameyApi): void;
  dispose?(): void;
}

interface FrameyI18n {
  lang(): string;
  setLang(lang: string): boolean;
  add(plugin: string, lang: string, dict: Record<string, string>): number;
  t(plugin: string, key: string, vars?: Record<string, string | number>): string;
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
  readonly _meta: Record<string, any>;
  register(plugin: FrameyPlugin): FrameyApi;
  call(_plugin: string, method: string, arg?: any): Promise<any>;
  dispose(): void;
  readonly panelOpen: boolean;
  el(css: string, text?: string): HTMLElement;
  button(text: string, css: string, fn: (ev?: Event) => void): HTMLElement;
  toast(text: string, ms?: number): void;
  fail(id: string, error: Error): void;
  listen(name: string, fn: (payload: any, info: FrameyEventInfo) => void): () => void;
  notify(options: FrameyNotifyOptions): FrameyNotification;
  onPanel(fn: (open: boolean) => void): () => void;
  lazy(name: "picker" | "dev"): Promise<void>;
  open(tab: string): void;
  toggle(): void;
  restore(): void;
  redraw(page?: string): void;
}

interface FrameyGlobal {
  readonly plugins: FrameyPlugin[];
  readonly panelOpen: boolean;
  readonly pages: Record<string, any>;
  dev?: { render(box: HTMLElement): Promise<void>; text(): Promise<string> };
  open(tab: string): void;
  toggle(): void;
  redraw(page?: string): void;
  restore(): void;
  panelPages(): any[];
  fail(id: string, error: Error): void;
  i18n: FrameyI18n;
  dispose(id?: string, token?: string): void;
  call(plugin: string, method: string, arg?: any): Promise<any>;
  toast(text: string, ms?: number): void;
  el(css: string, text?: string): HTMLElement;
  button(text: string, css: string, fn: (ev?: Event) => void): HTMLElement;
  lazy(name: "picker" | "dev"): Promise<void>;
  onPanel(fn: (open: boolean) => void): () => void;
  notify(options: FrameyNotifyOptions): FrameyNotification;
  diag(): FrameyDiag;
  _panel(open: boolean): void;
}

declare const Framey: FrameyGlobal;
