/**
 * e2e/flows-notebook.spec.js
 *
 * Core notebook flow for /flows (the cell-based NotebookView):
 *
 *   1. Open /flows → create a new flow → switch to the Notebook view →
 *      add a SQL cell → type SQL against the seeded demo DuckDB table →
 *      click the cell's Run button → assert the results panel renders
 *      (row-count header + data table rows, no error banner).
 *      This exercises POST /flows/preview end-to-end
 *      (src/lib/notebooks.js previewCell → backend preview_cell).
 *
 *   2. Same setup but with a bad table name → assert the cell shows the
 *      inline error banner and the notebook UI stays alive (no crash).
 *
 * Selector notes (no data-testids in src/flows — use accessible names/titles):
 *   - "New flow" button on the /flows list (role=button, /new flow/i)
 *   - View switcher in the top bar: aria-label "Notebook / cell view"
 *   - Empty-notebook state: button with text "Add SQL cell"
 *   - Per-cell run button: title "Run cell (preview)" (CellToolbar)
 *   - Results header: "<N> rows" text (SqlCell results panel)
 *   - Error banner: red strip containing the backend error message
 */

import { test, expect } from '@playwright/test'
import { loginAs } from './helpers/auth.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * From a logged-in page: open /flows, create a new draft flow, switch the
 * builder to the Notebook view and add the first (empty) SQL cell.
 * Resolves once the cell's Monaco editor is visible.
 */
async function openNotebookWithSqlCell(page) {
  await page.goto('/flows')
  await page.waitForLoadState('networkidle')

  // Create a new draft flow (opens the builder in canvas view)
  const newFlowBtn = page.getByRole('button', { name: /new flow/i }).first()
  await expect(newFlowBtn).toBeVisible({ timeout: 10_000 })
  await newFlowBtn.click()
  await expect(page.locator('.react-flow').first()).toBeVisible({ timeout: 10_000 })

  // Switch to the Notebook / cell view (icon toggle in the app top bar)
  await page.getByRole('button', { name: 'Notebook / cell view' }).click()

  // Empty notebook → "Add SQL cell" button in the empty state
  const addSqlBtn = page.getByRole('button', { name: 'Add SQL cell', exact: true })
  await expect(addSqlBtn).toBeVisible({ timeout: 10_000 })
  await addSqlBtn.click()

  // The new SQL cell renders a Monaco editor (loaded async — generous timeout)
  const editor = page.locator('.monaco-editor').first()
  await expect(editor).toBeVisible({ timeout: 30_000 })
  return editor
}

/**
 * Set the SQL of the (single) Monaco editor on the page.
 *
 * keyboard.type() races Monaco's async init/suggest widget and can drop
 * keystrokes, so we set the model value through the Monaco API (window.monaco
 * is exposed by the @monaco-editor/react AMD loader). setValue fires
 * onDidChangeModelContent, so the React cell state updates exactly as if the
 * user had typed.
 */
async function typeSql(page, _editor, sql) {
  await page.waitForFunction(
    () => window.monaco?.editor?.getEditors?.().length > 0,
    null,
    { timeout: 15_000 },
  )
  await page.evaluate((value) => {
    window.monaco.editor.getEditors()[0].setValue(value)
  }, sql)
  // Confirm the editor (and therefore the controlled cell state) has the SQL
  await page.waitForFunction(
    (value) => window.monaco.editor.getEditors()[0].getValue() === value,
    sql,
    { timeout: 5_000 },
  )
}

/** The per-cell Run button (CellToolbar, title="Run cell (preview)"). */
function cellRunButton(page) {
  return page.getByTitle('Run cell (preview)').first()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('Flows notebook — run SQL cell (POST /flows/preview)', () => {
  test.use({ viewport: { width: 1440, height: 900 } })

  test.beforeEach(async ({ page }) => {
    await loginAs(page)
  })

  test('create notebook, run a SQL cell against the demo table → rows render', async ({ page }) => {
    const editor = await openNotebookWithSqlCell(page)

    // Type a query against the seeded demo DuckDB table
    await typeSql(page, editor, 'SELECT * FROM demo LIMIT 5')

    // Run the cell (preview)
    const runBtn = cellRunButton(page)
    await expect(runBtn).toBeEnabled()
    await runBtn.click()

    // Results header shows "<N> rows" once the preview returns
    const rowCount = page.locator('span').filter({ hasText: /^\d+ rows$/ }).first()
    await expect(rowCount).toBeVisible({ timeout: 30_000 })
    await expect(rowCount).toHaveText('5 rows')

    // The results data table renders actual demo rows
    const firstNameCell = page.locator('table td').filter({ hasText: 'alpha' }).first()
    await expect(firstNameCell).toBeVisible({ timeout: 10_000 })

    // No error banner anywhere in the cell
    await expect(page.getByText(/cell_execution_failed|Preview failed/i)).toHaveCount(0)
  })

  test('failing SQL cell (bad table) → inline error shown, app stays alive', async ({ page }) => {
    const editor = await openNotebookWithSqlCell(page)

    await typeSql(page, editor, 'SELECT * FROM no_such_table_e2e')

    await cellRunButton(page).click()

    // The cell's error banner shows the backend DuckDB error message
    const errorBanner = page.getByText(/does not exist|Catalog Error|failed/i).first()
    await expect(errorBanner).toBeVisible({ timeout: 30_000 })

    // No success results header rendered for the failed run
    await expect(page.locator('span').filter({ hasText: /^\d+ rows$/ })).toHaveCount(0)

    // App did not crash: notebook toolbar is still interactive —
    // the "+ SQL" add-cell button and the notebook name input are usable.
    await expect(page.getByRole('button', { name: '+ SQL' })).toBeVisible()
    const nameInput = page.getByPlaceholder('Notebook name…')
    await expect(nameInput).toBeVisible()
    await nameInput.fill('still-alive')
    await expect(nameInput).toHaveValue('still-alive')
  })
})
