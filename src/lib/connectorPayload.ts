/**
 * connectorPayload.ts — build the `config` body for POST/PUT /connectors.
 *
 * PUT /connectors/{id} MERGES the submitted config over the stored one, so an
 * omitted key is "leave unchanged". A field the user emptied while editing must
 * therefore be sent as an explicit `null` (which the server treats as "clear");
 * dropping it like a blank create-field would make the old value come back on
 * the next load.
 */

type Config = Record<string, unknown>

const isBlank = (v: unknown) => v === undefined || v === null || v === ''

/**
 * @param config        current form values
 * @param initialConfig the stored config the edit form was seeded from (edit only)
 * @param isEditing     true for PUT (edit), false for POST (create)
 */
export function connectorConfigPayload(config: Config, initialConfig: Config | null | undefined, isEditing: boolean): Config {
  const out: Config = Object.fromEntries(Object.entries(config).filter(([, v]) => !isBlank(v)))
  if (isEditing) {
    for (const [key, was] of Object.entries(initialConfig ?? {})) {
      if (!isBlank(was) && isBlank(config[key])) out[key] = null
    }
  }
  return out
}
