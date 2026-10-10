export type WorkingOnLabel = string

declare module 'claude-code' {
  interface PluginState {
    'working-on': { label: WorkingOnLabel }
  }
}
