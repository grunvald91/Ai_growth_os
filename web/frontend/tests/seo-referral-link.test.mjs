import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../src/utils/seoReferral.ts', import.meta.url), 'utf8')
const javascript = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText
const { withSeoRef } = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`)

test('signed referral survives return to business and next service click', () => {
  const company = withSeoRef('/business/64', 'article.signature')
  assert.equal(company, '/business/64?seo_ref=article.signature')
  assert.equal(withSeoRef('/book/64?service=12', 'article.signature'),
               '/book/64?service=12&seo_ref=article.signature')
})
test('empty referral never adds a tracking parameter', () => {
  assert.equal(withSeoRef('/business/64', undefined), '/business/64')
  assert.equal(withSeoRef('/book/64?service=12', null), '/book/64?service=12')
})
