/**
 * tabBarConfig.ts — the board-level tab-bar style a spec carries.
 *
 * The editor's Tabs panel writes `spec.tab_bar` (snake_case, like every other
 * persisted spec key); a few boards written by older tools carry `spec.tabBar`.
 * The renderer used to read only `tabBar`, so a tab-bar style saved from the
 * editor showed in the editor preview and vanished on the live board.
 */
export function tabBarConfig(spec: { tab_bar?: any; tabBar?: any } | null | undefined): any {
  return spec?.tab_bar ?? spec?.tabBar
}
