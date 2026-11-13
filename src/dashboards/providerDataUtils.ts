/**
 * providerDataUtils.js — Pure utilities for BET-3 DataProvider multi-table IPC decoding.
 *
 * This module has NO React, NO api.js, and NO import.meta.env dependencies so it
 * can be imported directly in Node.js unit tests (node:test / npm run test:dash).
 */

import * as arrow from 'apache-arrow'
import { descaleDecimalTable } from '../lib/arrowDecimal.js'

/**
 * Decode a multi-table Arrow IPC frame produced by the backend's
 * ``tables_to_multi_ipc_stream`` (board_data.py).
 *
 * Frame format:
 *   4 bytes big-endian: count N
 *   N frames, each:
 *     4 bytes big-endian: name_len
 *     name bytes (UTF-8)
 *     4 bytes big-endian: ipc_len
 *     ipc bytes (Arrow IPC stream for one table)
 *
 * @returns result_name → Arrow Table
 */
export function decodeMultiTableIPC(buffer: ArrayBuffer): Record<string, arrow.Table> {
  const view = new DataView(buffer)
  let offset = 0

  const n = view.getUint32(offset, false) // big-endian count
  offset += 4

  const tables: Record<string, arrow.Table> = {}

  for (let i = 0; i < n; i++) {
    const nameLen = view.getUint32(offset, false)
    offset += 4

    const nameBytes = new Uint8Array(buffer, offset, nameLen)
    const name = new TextDecoder().decode(nameBytes)
    offset += nameLen

    const ipcLen = view.getUint32(offset, false)
    offset += 4

    const ipcBytes = new Uint8Array(buffer, offset, ipcLen)
    offset += ipcLen

    try {
      const table = descaleDecimalTable(arrow.tableFromIPC(ipcBytes))
      tables[name] = table
    } catch (e) {
      console.warn(`[decodeMultiTableIPC] Failed to decode table "${name}":`, e?.message)
      // Produce an empty table on decode failure — best-effort
      tables[name] = new arrow.Table()
    }
  }

  return tables
}

// ── Board provider → widget table mapping (used by useBoardProviderTables) ──
// Pure (no React / api.ts) so it is unit-testable under node --test.

export const MAX_PROVIDERS = 8

/** Pad the board's providers to exactly MAX_PROVIDERS slots (null = unused). */
export function providerSlotsFor(boardProviders: any[]): any[] {
  const slots = []
  for (let i = 0; i < MAX_PROVIDERS; i++) {
    slots.push(boardProviders[i] ?? null)
  }
  return slots
}

/**
 * Map each provider-bound widget id to its result table. A widget whose
 * provider is beyond the 8 slots (or undeclared) gets no entry; a declared
 * provider without that result maps to null.
 */
export function buildWidgetProviderTableMap(
  widgets: any[],
  boardProviders: any[],
  slotResults: Array<{ tables: Record<string, any> }>,
): Record<string, any> {
  const providerResults = {}
  boardProviders.forEach((provider, i) => {
    if (i < MAX_PROVIDERS) {
      providerResults[provider.id] = slotResults[i]
    }
  })
  const map = {}
  for (const w of widgets ?? []) {
    if (w?.source?.provider && w?.source?.result) {
      const pResult = providerResults[w.source.provider]
      if (pResult) {
        map[w.id] = pResult.tables[w.source.result] ?? null
      }
    }
  }
  return map
}

/** Column names of a provider result table (Arrow schema), [] when absent. */
export function providerTableColumns(table: any): string[] {
  const fields = table?.schema?.fields
  return Array.isArray(fields) ? fields.map(f => f.name) : []
}
