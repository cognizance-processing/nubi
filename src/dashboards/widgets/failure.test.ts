import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  classifyFailure, extractRequestId, groupFailures, viewerMessage, normaliseMessage, tileNoun,
  type FailureEntry,
} from './failure.js'

test('classifyFailure recognises an unreachable data source without leaking the host', () => {
  const f = classifyFailure(`MySQL query failed: (2003, "Can't connect to MySQL server on '10.0.0.5' ([Errno 111] Connection refused)")`)
  assert.equal(f.cause, 'connection')
  assert.doesNotMatch(f.label, /10\.0\.0\.5|MySQL|2003/)
})

test('classifyFailure maps the common engine failures', () => {
  assert.equal(classifyFailure('canceling statement due to statement timeout').cause, 'timeout')
  assert.equal(classifyFailure('permission denied for table orders').cause, 'permission')
  assert.equal(classifyFailure('No registered query found for id abc').cause, 'missing')
  assert.equal(classifyFailure('Catalog Error: Table with name sales does not exist').cause, 'missing')
  assert.equal(classifyFailure('SELECT list column count mismatch: 7 vs 3').cause, 'sql')
  assert.equal(classifyFailure('Parser Error: syntax error at or near "FROM"').cause, 'sql')
})

test('unclassified failures group only when the engine said the same thing', () => {
  const a = classifyFailure('kaboom 123')
  const b = classifyFailure('kaboom 456')
  const c = classifyFailure('something else entirely')
  assert.equal(a.cause, 'unknown')
  assert.equal(a.key, b.key)
  assert.notEqual(a.key, c.key)
})

test('normaliseMessage drops ids and numbers', () => {
  assert.equal(
    normaliseMessage('query 69852d74-d506-41d8-a5fb-c630f5c9d216 failed after 31ms'),
    'query # failed after #ms',
  )
})

test('extractRequestId reads a quoted id and ignores prose', () => {
  assert.equal(extractRequestId('Bad gateway (request id: req_8f31c2a9)'), 'req_8f31c2a9')
  assert.equal(extractRequestId('Request ID 9a8b7c6d5e'), '9a8b7c6d5e')
  assert.equal(extractRequestId('the request failed'), null)
  assert.equal(extractRequestId(undefined), null)
})

test('viewerMessage names the tile type', () => {
  assert.equal(viewerMessage('chart'), "This chart couldn't load.")
  assert.equal(viewerMessage('kpi'), "This number couldn't load.")
  assert.equal(viewerMessage('table'), "This table couldn't load.")
  assert.equal(viewerMessage(undefined), "This tile couldn't load.")
  assert.equal(tileNoun('metric'), 'number')
})

function entry(id: string, message: string): FailureEntry {
  const f = classifyFailure(message)
  return { id, key: f.key, cause: f.cause, label: f.label, message }
}

test('groupFailures needs three tiles with one cause', () => {
  const two = [entry('a', "Can't connect to server"), entry('b', 'connection refused')]
  assert.deepEqual(groupFailures(two), [])

  const three = [...two, entry('c', 'ECONNREFUSED 127.0.0.1:3306'), entry('d', 'syntax error')]
  const groups = groupFailures(three)
  assert.equal(groups.length, 1)
  assert.equal(groups[0].cause, 'connection')
  assert.equal(groups[0].count, 3)
  assert.deepEqual(groups[0].ids, ['a', 'b', 'c'])
})

test('groupFailures orders the biggest group first and honours a custom threshold', () => {
  const entries = [
    entry('1', 'syntax error'), entry('2', 'syntax error'),
    entry('3', 'connection refused'), entry('4', 'connection refused'), entry('5', 'connection refused'),
  ]
  assert.equal(groupFailures(entries, 2)[0].cause, 'connection')
  assert.equal(groupFailures(entries, 2).length, 2)
  assert.equal(groupFailures(entries).length, 1)
})
