import { useSyncExternalStore } from "react";

/** Minimal observable store so audio classes can drive React without a state library. */
export class Store<T extends object> {
  private listeners = new Set<() => void>();
  constructor(private state: T) {}

  get = (): T => this.state;

  set(patch: Partial<T>): void {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((l) => l());
  }

  subscribe = (l: () => void): (() => void) => {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  };
}

export function useStore<T extends object>(store: Store<T>): T {
  return useSyncExternalStore(store.subscribe, store.get);
}
