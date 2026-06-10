# Dashboard Tabs + Filter/Variable Input Overhaul — Implementation Guide

Two features, two largely independent tracks that can be built in parallel:

- **Track T — Dashboard Tabs:** multiple tabbed pages inside one dashboard, customizable tab styling, per-tab layout, variables shared across all tabs.
- **Track F — Filter Input Overhaul:** better dropdown/combobox UI, real query-backed autocomplete, select-all / exclude ("all but selected") semantics, date presets, deep customization.

Decisions already made:

| Decision | Choice |
|---|---|
| Tab model | **Option A** — tabs are sections inside one board spec (NOT references to other boards). One board, one spec, one version history. |
| Variables | All variables shared across tabs (they already live above the widget tree). |
| Layout | Per-tab layout overrides, shallow-merged over `spec.layout`. |
| Tab styling | Structured tokens (variant/colors/size) + sanitized `custom_css` escape hatch. |
| Exclude mode → SQL | Explicit `pick: "mode"` / `pick: "values"` param bindings; **no** automatic SQL rewriting. |

---

## Architecture context (current state)

- A dashboard is a `boards` row; the whole definition lives in the `config` jsonb column. **No DB migration is needed for any of this** — all changes are additive fields inside the spec JSON.
- Canonical spec: `backend/app/dashboards/spec.py` — Pydantic models (`DashboardSpec`, `Widget`, `Variable`, `WidgetPos`), `validate_spec()`, `spec_to_html()` (static embed renderer), `spec_json_schema()` (LLM grounding).
- Viewer: `src/dashboards/SpecRenderer.jsx` — wraps everything in `VariableProvider`, partitions drawer widgets (`drawer: true` / `drawer_group`) out of the grid, renders the rest via `GridCanvas`.
- Variables: `src/dashboards/VariableStore.jsx` — flat `{name: value}` context; filter widgets write, data widgets read via `useResolvedParams`. URL sync lives in `src/pages/DashboardViewPage.jsx`.
- Filters: `src/dashboards/widgets/FilterWidget.jsx` — subtypes `select | multiselect | daterange | text`. Already has a searchable combobox + windowed `VirtualList`. Options fetched **once** from `options_query_id` (in `FilterWidgetLoader` inside SpecRenderer) via `runArrowQueryById` (`src/lib/wasmRuntime.js`).
- Style helpers: `src/dashboards/widgetHtml.js` — `styleToCss()`, `backgroundToCss()` (the sanitized CSS path; reuse, don't bypass).
- Known bug to fix in Track F: dropdowns are absolutely positioned inside the widget card, and `SpecRenderer.jsx` wraps each grid cell in `overflow-hidden` (~line 459), so open dropdowns are clipped.
- Accessible tablist reference: `src/components/pricing/PricingCalculator.jsx`.
- Versioning/envs: boards snapshot atomically into `resource_versions`; a tabbed dashboard pins as one unit — nothing to do here, just don't split tabs into separate resources.

---

# Track T — Dashboard Tabs

## T1. Spec model (`backend/app/dashboards/spec.py`)

```python
class Tab(BaseModel):
    id: str = Field(min_length=1)        # stable, e.g. "t1"
    label: str = Field(min_length=1)
    icon: str | None = None
    layout: dict[str, Any] = Field(default_factory=dict)   # shallow-merged over spec.layout
    style: dict[str, Any] = Field(default_factory=dict)    # per-tab overrides of tab_bar tokens
    background: dict[str, Any] | None = None                # per-tab canvas bg (backgroundToCss)

class DashboardSpec(BaseModel):
    ...
    tabs: list[Tab] = Field(default_factory=list)   # empty => no tabs, behaves exactly as today
    tab_bar: dict[str, Any] = Field(default_factory=dict)

class Widget(BaseModel):
    ...
    tab_id: str | None = None   # None while tabs exist => first tab
```

`tab_bar` shape (documented in the module docstring — it doubles as LLM-facing docs):

```jsonc
{
  "variant": "underline",      // underline | pills | segmented | cards | buttons
  "position": "top",           // top | left
  "align": "start",            // start | center | end | stretch
  "size": "md",                // sm | md | lg
  "bg": "...", "text": "...", "active_text": "...",
  "accent": "...", "border": "...",
  "radius": 8, "gap": 4,
  "custom_css": "..."          // sanitized via the styleToCss pipeline
}
```

**Per-tab layout semantics:** effective layout for a tab = `{**spec.layout, **tab.layout}`. Any of `cols`, `row_height`, `cols_md`, `cols_sm`, `compaction`, `margin`/`margin_x`, `container_padding`/`padding_x/y` can be overridden. `widget.pos` is interpreted within its tab's grid.

**`validate_spec()` additions** (mirror the existing variable-ref rules):
- Duplicate tab ids → hard error.
- `widget.tab_id` set but not declared in `spec.tabs` → hard error (same severity as undeclared `{ref}` vars, step 6).
- Drawer widgets (`drawer: true`) ignore `tab_id` — drawers stay **global** across tabs (consistent with shared variables; the Filters drawer applies everywhere).
- `spec.tabs` non-empty: widgets with `tab_id is None` implicitly belong to the first tab (no error).

**`spec_to_html()` (embed/static):** graceful degradation — render tabs as stacked sections, each preceded by `<h3>{tab.label}</h3>`. No interactive tab bar in embeds (no JS).

`spec_json_schema()` picks the new fields up automatically; update the module docstring so the LLM authoring pipeline knows about tabs.

## T2. TabBar component (`src/dashboards/TabBar.jsx` — new)

- `role="tablist"` / `role="tab"` / `aria-selected`, arrow-key navigation (crib from `PricingCalculator.jsx`).
- Props: `tabs`, `activeTabId`, `onChange`, `tabBar` (style config).
- Implements variants (`underline` first; `pills`/`segmented` next; `cards`/`buttons`/`position: left` can land in polish phase).
- Add `tabStyleToCss()` to `src/dashboards/widgetHtml.js` alongside `styleToCss` — ALL user-supplied colors/CSS flow through the existing sanitized path. Per-tab `tab.style` overrides bar tokens for that tab.
- **Hide the bar entirely when `tabs.length <= 1`.**

## T3. SpecRenderer changes (`src/dashboards/SpecRenderer.jsx`)

1. Extend the existing partition memo (currently splits `drawer` widgets from grid widgets) to also bucket grid widgets by `tab_id`:
   ```js
   // { tabBuckets: Map<tabId, Widget[]>, drawerGroups, hasTabs }
   // widget.tab_id ?? firstTabId; if spec.tabs is empty, single implicit bucket = today's behavior
   ```
2. Accept new props: `activeTabId`, `onTabChange` (controlled from the page; falls back to internal state when not provided — e.g. embeds).
3. Per-tab layout: compute `cols / rowHeight / colsByBp / gap / padding / compaction` from the **merged** layout of the active tab. `buildLayouts` runs per tab (memoize per tab id).
4. **Mounting strategy: lazy + keep-alive.** Render only tabs that have been activated at least once; keep previously-visited tabs mounted but `display:none`. First paint fires only the first tab's queries; revisits are instant; widget-local state survives. `VariableProvider` stays at the very top — shared variables are unaffected.
5. Per-tab `background` via `backgroundToCss(tab.background) ?? backgroundToCss(spec.background)`.
6. Drawers/`SlideOver` remain global (reachable from any tab).

## T4. View page (`src/pages/DashboardViewPage.jsx`)

- Active tab ↔ `_tab` URL search param. **Underscore prefix is deliberate**: plain param names are owned by variable URL sync; `_tab` can never collide with a user variable.
- Invalid/missing `_tab` → first tab. Tab switches use `setSearchParams` (replace, don't push, to avoid history spam — match whatever the variable sync does).

## T5. Editor (`src/pages/EditorPage.jsx`) — biggest chunk

- Tab strip in edit mode: add / inline-rename / drag-reorder / delete. Deleting a tab with widgets prompts: move to another tab or delete widgets.
- Canvas shows the active tab's widgets; **new widgets get the active tab's `tab_id`**.
- Move widget between tabs via widget context menu ("Move to tab →"). Drag-onto-tab is a later nicety.
- Inspector panels:
  - **Tab bar panel:** variant picker (live preview), color tokens, size/align, custom CSS field.
  - **Per-tab panel:** label, icon, style overrides, background, layout overrides — layout fields default to "inherit from dashboard" with explicit override state per field.

## T6. Tests

- Backend (`backend/.../test_spec*.py` pattern): duplicate tab ids; undeclared `tab_id` hard error; tab-less spec validates and renders unchanged; `spec_to_html` stacked-sections output.
- E2E (alongside `e2e/dashboard-variables.spec.js`):
  - Filter set on tab 1 → switch tab → tab 2 chart reflects the shared variable.
  - `?_tab=<id>&region=alpha` deep link lands on the right tab with the variable applied.
  - Two tabs with different `cols` render different grids.

## T phases

1. **T-P1 (viewer):** spec models + validation, `TabBar.jsx` (underline variant), SpecRenderer partition + per-tab layout + lazy mounting, `_tab` URL sync, backend tests + e2e. Tabbed dashboards fully viewable (authorable via raw spec / LLM).
2. **T-P2 (editor):** tab strip CRUD, widget assignment/move, inspector panels.
3. **T-P3 (polish):** remaining variants, `position: left`, icons, embed stacked-sections fallback.

---

# Track F — Filter / Variable Input Overhaul

## F1. Input primitives (`src/dashboards/inputs/` — new) + clipping bugfix

Extract from `FilterWidget.jsx`:

- **`Popover.jsx`** — dropdown in a **React portal** (fixes the `overflow-hidden` clipping bug), anchored to trigger rect, flips above when near viewport bottom, closes on outside-click / Escape, traps focus sensibly.
- **`Combobox.jsx`** — trigger + search input + the existing `VirtualList` (move it here), keyboard nav (Up/Down/Enter/Home/End/Escape, type-ahead), `role="combobox"`/`listbox`/`option` ARIA.
- **`OptionList.jsx`** — row renderer: plain / radio / checkbox modes, search-match highlighting, loading + empty + "type to search" states.

`SelectFilter` / `MultiSelectFilter` become thin wrappers. Restyle with the existing design tokens (`bg-surface`, `border-border`, `brand-teal` focus rings) but tighter: consistent heights, chips, subtle motion.

**Portal + custom CSS caveat:** the portal escapes the widget's DOM subtree, so per-widget `style`/`custom_css` must reach the dropdown via CSS variables set on the trigger and read by the portal content (pass a style payload through context/props, not DOM inheritance).

## F2. Multiselect power: select all / clear / invert / exclude

**UI (in `MultiSelectFilter`):**
- Header row in the dropdown: **Select all** (acts on the currently *filtered* list), **Clear**, **Invert**.
- Include/Exclude segmented toggle: "is any of" / "is not any of" (shown when `props.exclude_toggle !== false`).
- Selected options pinned to the top of the list.
- Trigger shows removable chips with `+N more` overflow (configurable: `chips | count | summary`).

**Variable value shape (the data-model change):**

```jsonc
["a", "b"]                                  // legacy plain array — still valid, means include
{ "mode": "include", "values": ["a", "b"] }
{ "mode": "exclude", "values": ["a", "b"] } // "all but those selected"
{ "mode": "all" }                            // explicit no-constraint
```

**`VariableStore.jsx`:**
- `resolveParams` stays pass-through for `{ref}`; add exported helpers `isExclude(v)`, `valuesOf(v)`, `modeOf(v)` (returns `'all' | 'include' | 'exclude'`, treating plain arrays as include, `undefined`/empty as all).
- Extend ref binding with `pick`: `{ref: "region", pick: "values"}` → the values array; `{ref: "region", pick: "mode"}` → the mode string. Update spec.py step-6 validation to accept the `pick` key.
- **Query contract (no SQL rewriting):** query authors write e.g.
  ```sql
  WHERE (:region_mode = 'all'
     OR (:region_mode = 'include' AND region IN (SELECT unnest(:region_values)))
     OR (:region_mode = 'exclude' AND region NOT IN (SELECT unnest(:region_values))))
  ```
  Explicit, predictable, dialect-agnostic from our side.

**URL encoding** (in `DashboardViewPage.jsx` sync layer): `?region=a,b` include · `?region=!a,b` exclude · absent = all. Escape literal leading `!` in values. Round-trip unit tested.

## F3. Query-backed autocomplete

- `props.options_mode: "static" | "search"` (default `static` = today's one-shot fetch in `FilterWidgetLoader`).
- `search` mode: re-run the options query, debounced ~250ms, with the search text bound via `options_params: {search: {input: true}}` (the `{input: true}` marker means "bind the live search box text"). Loading spinner row; "showing first N" footer when capped.
- **Label cache:** selected values not present in the current result page (e.g. seeded from a deep link) still need labels — keep a small `{value → label}` cache per widget, populated from every fetch; fall back to raw value.
- Move/extend `FilterWidgetLoader` (currently in `SpecRenderer.jsx`) into the inputs layer so both modes share one loader.

## F4. Daterange presets + new subtypes

- **Daterange:** popover calendar (two months, click-range selection) + preset rail: Today, Yesterday, Last 7/30/90 days, MTD, QTD, YTD, Custom (`props.presets` to configure). Store relative presets as `{preset: "last_30d"}` so saved dashboards stay relative; resolve to concrete `{from, to}` at param-resolution time (single `resolvePreset()` helper, unit tested, injectable clock).
- **New subtypes** (extend the `subtype` Literal in `spec.py` AND `FilterWidget` dispatch):
  - `number_range` — min/max inputs (slider later), value `{min, max}`.
  - `toggle` — boolean or two-option segmented control.
  - `radio`, `checkbox_list` — inline (non-dropdown) variants for short option lists.

## F5. Customization surface (all on `widget.props`, validated in `spec.py`, editable in the editor inspector)

| Group | Props |
|---|---|
| Content | `label`, `placeholder`, `help_text`, `all_label`, `value_col`, `label_col`, `sort` (`asc\|desc\|none`), per-option icons |
| Behavior | `searchable`, `clearable`, `select_all`, `exclude_toggle`, `max_selected`, `default_mode`, `debounce_ms` |
| Appearance | `variant` (`dropdown\|pills\|radio\|checkbox_list\|segmented`), `size` (`sm\|md\|lg`), `display` (`chips\|count\|summary`), existing per-widget `style`/`custom_css` (extended to the portal via CSS vars, see F1) |

`Variable` model gains optional `label: str` and `description: str` (shown in the filters drawer and editor instead of raw names).

## F6. Spec/backend changes summary (`backend/app/dashboards/spec.py`)

- Extend `subtype` Literal; document new props + structured multiselect value in the module docstring (LLM grounding).
- Validate structured `Variable.default` shape for multiselect; accept `pick` in param-ref validation.
- `_filter_tag()` in `spec_to_html` emits the new attributes that make sense statically; unknown subtypes degrade to a labeled placeholder.

## F7. Tests

- Unit: `resolveParams` + `pick` + helpers; URL encode/decode round-trip incl. `!` escaping; `resolvePreset` date math.
- Component: combobox keyboard nav; select-all acts on filtered subset; invert; portal positioning/flip; label cache.
- E2E (extend `e2e/dashboard-variables.spec.js`): exclude-mode deep link drives a query; search-mode autocomplete fetches as you type; preset daterange round-trips through the URL.

## F phases

1. **F-P1:** primitives + portal bugfix + restyle (no spec changes, immediate visible win).
2. **F-P2:** select all / clear / invert, exclude mode, structured value + URL encoding + `pick` bindings, chips.
3. **F-P3:** query-backed autocomplete + label cache.
4. **F-P4:** daterange presets, new subtypes, editor inspector panels for all new props.

---

# Combined ordering recommendation

| Step | Work | Why first |
|---|---|---|
| 1 | **F-P1** | Small, fixes a live bug, zero spec risk |
| 2 | **T-P1** | Unlocks the headline feature in the viewer |
| 3 | **F-P2** | Data-model change — land before more things depend on multiselect values |
| 4 | **T-P2** | Editor tabs |
| 5 | **F-P3 → F-P4 → T-P3** | Independent polish, any order |

## Invariants to preserve throughout

1. **Backward compatibility is total:** a spec with no `tabs`, plain-array multiselect values, and no new props must render byte-for-byte like today. No spec `version` bump; everything is additive.
2. All user-supplied style strings flow through `styleToCss`/`backgroundToCss` (and the new `tabStyleToCss`) — never interpolate raw user CSS/colors into the DOM.
3. Variables remain dashboard-global. Tabs and drawers are render partitions, not scopes.
4. URL namespace: plain params belong to variables; reserved params are underscore-prefixed (`_tab`).
5. Boards version as one unit — never split tabs into separate resources.
