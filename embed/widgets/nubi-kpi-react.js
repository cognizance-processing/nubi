/**
 * nubi-kpi-react.js — <nubi-kpi-react> web component.
 *
 * This is the dogfood proof that the React-to-custom-element wrapper works:
 * it takes KpiWidget (a real SPA React component) and ships it as a standalone
 * web component using defineNubiElement from embed/react-wc.js.
 *
 * ATTRIBUTES
 * ----------
 * spec       JSON. A full widget spec object (type, encoding, props, etc.)
 *            See KpiWidget.jsx for shape details. Takes priority over
 *            individual shorthand attributes below.
 * data       JSON. An array of row objects to use as the data source instead
 *            of querying a backend. Shape: [{col: value, ...}, ...].
 * title      Display label override (sets widget.props.label if spec absent).
 * get-token  Name of a function on `window` returning Promise<string>|string.
 * base-url   Backend base URL. Defaults to http://localhost:8000.
 *
 * JS PROPERTIES
 * -------------
 * getToken   Function | string. Takes priority over get-token attribute.
 *
 * EVENTS
 * ------
 * nubi:select   — fired when the KPI card is clicked.
 *               detail: { id, rowIndex: 0, row: {} }
 *
 * CSS CUSTOM PROPERTIES
 * ---------------------
 * All --nubi-* tokens defined in embed/theme.js are supported.
 * Set them on the element or a parent to theme the component.
 *
 * EXAMPLE
 * -------
 *   <nubi-kpi-react
 *     spec='{"query_id":"revenue_total","encoding":{"value":"revenue"},"props":{"label":"Revenue","format":"currency"}}'
 *     get-token="myGetToken"
 *     base-url="https://api.example.com"
 *   ></nubi-kpi-react>
 */

import { createElement, useState, useEffect, useCallback } from 'react'
import { defineNubiElement } from '../react-wc.js'
import { emitSelect } from '../events.js'
import KpiWidget from '../../src/dashboards/widgets/KpiWidget.jsx'

// ---------------------------------------------------------------------------
// KpiWrapper — bridges web-component props to KpiWidget's spec-based API
// ---------------------------------------------------------------------------

/**
 * Internal wrapper that adapts flat web-component props into the spec object
 * KpiWidget expects, and wires up the click-to-select interaction.
 *
 * @param {{ spec?: object, data?: object[], title?: string, onSelect?: Function }} props
 */
function KpiReactWrapper({ spec, data, title, onSelect }) {
  // Build the widget spec from either the provided spec or shorthand attrs
  const widgetSpec = spec || {
    id:       'kpi-wc',
    type:     'kpi',
    query_id: null,
    encoding: { value: data?.[0] ? Object.keys(data[0])[0] : 'value' },
    props:    { label: title || 'KPI', format: 'number' },
  }

  // When `data` is provided inject it as a providerTable-compatible structure.
  // KpiWidget accepts `providerTable` (an apache-arrow Table) but for the
  // web-component data-attribute path we accept a plain object array and
  // convert to a minimal duck-typed table.
  const [providerTable, setProviderTable] = useState(null)

  useEffect(() => {
    if (!data || !Array.isArray(data) || data.length === 0) {
      setProviderTable(null)
      return
    }

    // Build a minimal duck-typed Arrow-like table from plain objects.
    // KpiWidget only calls table.numRows, table.getChild(col).get(0), and
    // table.getChild(col).toArray(), so this slim shim is sufficient.
    const cols = Object.keys(data[0])
    const duckTable = {
      numRows: data.length,
      schema: { fields: cols.map(name => ({ name })) },
      getChild(col) {
        const values = data.map(row => row[col] ?? null)
        return {
          get: (i) => values[i],
          toArray: () => values,
        }
      },
    }
    setProviderTable(duckTable)
  }, [data])

  const handleClick = useCallback(() => {
    if (onSelect) {
      onSelect({ rowIndex: 0, row: data?.[0] ?? {} })
    }
  }, [onSelect, data])

  return createElement(
    'div',
    {
      style: { width: '100%', height: '100%', cursor: onSelect ? 'pointer' : 'default' },
      onClick: handleClick,
    },
    createElement(KpiWidget, {
      widget: widgetSpec,
      providerTable: providerTable,
    }),
  )
}

// ---------------------------------------------------------------------------
// Register the custom element
// ---------------------------------------------------------------------------

const NubiKpiReact = defineNubiElement(
  'nubi-kpi-react',
  KpiReactWrapper,
  {
    observedAttributes: ['spec', 'data', 'title', 'get-token', 'base-url'],

    propTypes: {
      spec:  'json',
      data:  'json',
      title: 'string',
    },

    defaultTheme: {},
  },
)

// ---------------------------------------------------------------------------
// Wire up nubi:select — extend the base class to intercept clicks
// The factory-returned class is the right place to add element-level event
// forwarding without touching the React component.
// ---------------------------------------------------------------------------

// Patch: listen for click on the shadow root and emit nubi:select.
// We do this at the module level by adding a delegated listener after define.
if (typeof customElements !== 'undefined') {
  // Use a MutationObserver approach: each time a new <nubi-kpi-react> is
  // connected to the DOM, attach a click listener that re-emits as nubi:select.
  const _tag = 'nubi-kpi-react'
  customElements.whenDefined(_tag).then(() => {
    document.addEventListener('click', (e) => {
      // Walk up to find the host element
      let el = e.composedPath()[0]
      while (el && el.tagName?.toLowerCase() !== _tag) {
        el = el.parentNode || el.host
      }
      if (!el || el.tagName?.toLowerCase() !== _tag) return
      emitSelect(el, {
        id: el.getAttribute('id') || _tag,
        rowIndex: 0,
        row: {},
      })
    }, true)
  }).catch(() => {/* ignore — element may not be registered in non-browser env */})
}

export { NubiKpiReact }
