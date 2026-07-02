import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  // Build outputs, the Python venv, and the pinned-source checkout are not ours
  // to lint — and the minified bundles overflow ESLint's formatter.
  globalIgnores(['dist', '**/dist', '.venv-backend', '.venv-mig', '.nubi-build']),
  // Node config files (vite.config.js, embed/vite.embed.config.js, …) and build
  // scripts need process + node globals. Use ** so NESTED config files match too.
  {
    files: ['**/*.config.{js,mjs,ts}', 'scripts/**/*.{js,mjs}'],
    languageOptions: {
      globals: { ...globals.node },
    },
  },
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    rules: {
      'no-unused-vars': ['error', { varsIgnorePattern: '^[A-Z_]', argsIgnorePattern: '^[A-Z_]', destructuredArrayIgnorePattern: '^[A-Z_]' }],
      // Fast Refresh is a dev-HMR optimisation, not a correctness rule — advisory.
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // Classic React-hooks linting: rules-of-hooks catches real bugs (error, from
      // the recommended set); exhaustive-deps stays advisory (warn).
      'react-hooks/exhaustive-deps': 'warn',
      // The plugin's latest recommended set also enables experimental,
      // React-Compiler-era rules. They're opt-in and too aggressive for this
      // codebase, so disable them (rules-of-hooks above remains enforced).
      'react-hooks/error-boundaries': 'off',
      'react-hooks/set-state-in-effect': 'off',
      'react-hooks/immutability': 'off',
      'react-hooks/static-components': 'off',
      'react-hooks/preserve-manual-memoization': 'off',
      'react-hooks/incompatible-library': 'off',
    },
  },
  // Test + e2e files run under Node/Vitest/Playwright — give them those globals.
  {
    files: ['**/*.test.{js,mjs,jsx}', 'embed/__tests__/**/*.js', 'embed/e2e/**/*.js', 'src/**/*.test.mjs'],
    languageOptions: {
      globals: { ...globals.node, ...globals.vitest },
    },
  },
])
