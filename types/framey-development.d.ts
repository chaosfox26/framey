interface FrameyBarControls {
  readonly button: HTMLDivElement | null;
  set(on: unknown): void;
  dispose(): void;
  badge?: (count: unknown) => void;
}

interface Window {
  FYBar?: FrameyBarControls;
}
