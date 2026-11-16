import test from 'node:test'
import assert from 'node:assert/strict'
import { validators, check, passwordScore } from './formLogic.js'

test('email validator accepts normal addresses and rejects obvious typos', () => {
  assert.equal(validators.email('jane@acme.com'), null)
  assert.equal(validators.email('  jane@acme.co.uk '), null)
  assert.match(validators.email('')!, /Enter your email/)
  assert.match(validators.email('jane@acme')!, /valid email/)
  assert.match(validators.email('jane acme.com')!, /valid email/)
})

test('required validator names the field and ignores whitespace', () => {
  assert.equal(validators.required('your name')('Jane'), null)
  assert.equal(validators.required('your name')('   '), 'Enter your name.')
})

test('minLength validator counts characters and names the field', () => {
  assert.equal(validators.minLength(8, 'Your password')('12345678'), null)
  assert.equal(validators.minLength(8, 'Your password')('1234567'), 'Your password needs at least 8 characters.')
})

test('check returns the first failing message', () => {
  const v = check('', validators.required('a value'), validators.minLength(4))
  assert.equal(v, 'Enter a value.')
  assert.equal(check('abcd', validators.required('a value'), validators.minLength(4)), null)
})

test('passwordScore rises with length, mixed case, digits and symbols', () => {
  assert.equal(passwordScore(''), 0)
  assert.equal(passwordScore('abc'), 0)
  assert.equal(passwordScore('abcdefgh'), 1)
  assert.equal(passwordScore('Abcdefgh'), 2)
  assert.equal(passwordScore('Abcdefg1'), 3)
  assert.equal(passwordScore('Abcdefg1!'), 4)
  assert.equal(passwordScore('a-very-long-passphrase-indeed'), 2) // long and has a symbol
})
