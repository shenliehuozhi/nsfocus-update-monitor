import js from '@eslint/js';
import globals from 'globals';

// The frontend is intentionally build-free: 12 classic <script> tags share one
// global scope. Static scope analysis cannot see declarations made by another
// file, so no-undef would report hundreds of false positives; the pytest gate
// in tests/test_frontend_assets.py already validates cross-file declarations,
// action names and load order.
export default [
  {
    ignores: ['node_modules/**'],
  },
  js.configs.recommended,
  {
    files: ['src/web/static/js/**/*.js'],
    languageOptions: {
      ecmaVersion: 2020,
      sourceType: 'script',
      globals: { ...globals.browser },
    },
    rules: {
      'no-undef': 'off',
      // `function history()` intentionally shadows window.history; the app
      // uses location.hash for routing and never calls the native API.
      'no-redeclare': ['error', { builtinGlobals: false }],
      // Top-level functions/consts are the shared API, so only flag locals.
      'no-unused-vars': ['error', { vars: 'local', args: 'none', caughtErrors: 'none' }],
      'no-empty': ['error', { allowEmptyCatch: true }],
      'no-constant-condition': ['error', { checkLoops: false }],
    },
  },
];
