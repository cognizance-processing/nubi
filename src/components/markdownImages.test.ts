// Chat transcripts may contain text the assistant copied from data it read.
// A remote image URL in that text would make the browser send a request to a
// third party (with whatever the URL encodes), so chat only shows images that
// live on this origin or are inline data.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { isSafeChatImageSrc } from './markdownImages.js'

const ORIGIN = 'https://app.nubi.test'

test('remote images are not shown in chat', () => {
  for (const src of [
    'https://evil.example/pixel.png?d=secret',
    'http://evil.example/x.png',
    '//evil.example/x.png',
    'https://app.nubi.test.evil.example/x.png',
    'https://app.nubi.test@evil.example/x.png',
    'ftp://evil.example/x.png',
    'javascript:alert(1)',
    'data:text/html;base64,PHNjcmlwdD4=',
    'data:image/svg+xml;base64,PHN2Zz4=',
    '\\\\evil.example\\x.png',
    '/\\evil.example/x.png',
    '',
    undefined,
    null,
  ]) {
    assert.equal(isSafeChatImageSrc(src as any, ORIGIN), false, String(src))
  }
})

test('same-origin and inline raster images are shown', () => {
  for (const src of [
    '/api/v1/images/abc',
    '/assets/logo.png',
    'https://app.nubi.test/api/v1/images/abc',
    'data:image/png;base64,iVBORw0KGgo=',
    'data:image/jpeg;base64,/9j/4AAQ',
    'data:image/webp;base64,UklGRg==',
    'data:image/gif;base64,R0lGODlh',
  ]) {
    assert.equal(isSafeChatImageSrc(src, ORIGIN), true, src)
  }
})
