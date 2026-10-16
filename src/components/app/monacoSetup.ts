/**
 * monacoSetup — serve Monaco from the app bundle instead of the jsDelivr CDN.
 *
 * @monaco-editor/react loads Monaco from a CDN by default, which breaks
 * self-hosted, air-gapped and offline installs. Importing this module once
 * (side-effect import, before any <Editor> mounts) points the loader at the
 * locally bundled `monaco-editor` and wires the web workers through Vite.
 *
 * It sits in the lazy editor chunk: app routes without a code editor never
 * download it. Every file that renders an <Editor> should
 * `import '<path>/components/app/monacoSetup'`.
 */

import { loader } from '@monaco-editor/react'
import * as monaco from 'monaco-editor'
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import JsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker'
import CssWorker from 'monaco-editor/esm/vs/language/css/css.worker?worker'
import HtmlWorker from 'monaco-editor/esm/vs/language/html/html.worker?worker'
import TsWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker'

(self as unknown as { MonacoEnvironment: unknown }).MonacoEnvironment = {
  getWorker(_id: string, label: string) {
    switch (label) {
      case 'json': return new JsonWorker()
      case 'css':
      case 'scss':
      case 'less': return new CssWorker()
      case 'html':
      case 'handlebars':
      case 'razor': return new HtmlWorker()
      case 'typescript':
      case 'javascript': return new TsWorker()
      default: return new EditorWorker()
    }
  },
}

loader.config({ monaco })
