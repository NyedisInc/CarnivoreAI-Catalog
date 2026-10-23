import { expect, mock, test } from 'claude-code/testing'

import { buildPrompt, cleanReply } from '../hooks/register'

const USAGE = { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }

const MESSAGES = [
  { role: 'user', text: 'fix the restart picker so its arm checkbox survives a reopen', toolUses: [] },
  { role: 'assistant', text: 'Reading the picker code.', toolUses: [] },
  { role: 'user', text: '', toolUses: [] },
  { role: 'user', text: '<bash-stdout>secret output</bash-stdout>', toolUses: [] },
  { role: 'assistant', text: 'Patched the checkbox state.', toolUses: [] },
] as const

test('a finished main turn draws the cleaned summary in light gray under the hint row', async ($, on) => {
  const clock = mock.clock(on)
  const prompts: string[] = []
  on('session.messages', () => ({ value: MESSAGES.map(m => ({ ...m, toolUses: [] })) }))
  on('model.complete', (_$, e) => {
    prompts.push(e.prompt)
    return { value: { isAnswered: true, text: '"Summary: Fixing the restart picker checkbox."\nextra', usage: USAGE } } as const
  })
  on('turn.complete', (_$, e) => ({ text: e.answer }))
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('ui.status', () => ({ value: undefined }))
  // Stands in for the engine's own hint row beneath the plugin.
  on('ui.render', { component: 'PromptHint' }, ($, e) => {
    const { Text } = $.ui.resolve(e)
    return Text({ children: ['? for shortcuts'] })
  })

  await $.session.start({ cwd: '/w', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ plugin: 'working-on', surface: 'terminal', component: 'PromptHint', props: { isDraft: false, isWorking: false, hint: '? for shortcuts' } })
  expect(await ui.find({ text: /^≡ / })).toBeUndefined()

  await $.turn.complete({ answer: 'done', durationMs: 5, isAborted: false, turnId: 't1', reason: 'answer' })
  for (let i = 0; i < 20 && prompts.length === 0; i++) await clock.advance(1)
  let found = await ui.find({ text: /^≡ / })
  for (let i = 0; i < 20 && !found; i++) {
    await clock.advance(1)
    found = await ui.find({ text: /^≡ / })
  }

  expect(found?.text).toBe('≡ Fixing the restart picker checkbox')
  expect(await ui.find({ text: '? for shortcuts' })).toBeDefined()
  expect(JSON.stringify(found)).toContain('#bcbcbc')
  expect(prompts[0]).toContain('goal: fix the restart picker')
  expect(prompts[0]).not.toContain('secret output')
  await ui.unmount()
})

test('an aborted turn and a subagent turn do not call the model', async ($, on) => {
  const clock = mock.clock(on)
  let calls = 0
  on('session.messages', () => ({ value: MESSAGES.map(m => ({ ...m, toolUses: [] })) }))
  on('model.complete', () => {
    calls += 1
    return { value: { isAnswered: true, text: 'x', usage: USAGE } } as const
  })
  on('ui.status', () => ({ value: undefined }))
  on('turn.complete', (_$, e) => ({ text: e.answer }))
  on('session.start', (_$, e) => ({ cwd: e.cwd }))

  await $.session.start({ cwd: '/w', surface: 'terminal', isInteractive: true })
  for (let i = 0; i < 20 && calls === 0; i++) await clock.advance(1)
  expect(calls).toBe(1)
  calls = 0
  await $.turn.complete({ answer: '', durationMs: 5, isAborted: true, turnId: 't2', reason: 'aborted' })
  await $.turn.complete({ answer: '', durationMs: 5, isAborted: false, turnId: 't3', reason: 'answer', agentId: 'a1' })
  await clock.advance(10)

  expect(calls).toBe(0)
})

test('a headless run (claude -p) never calls the model', async ($, on) => {
  const clock = mock.clock(on)
  let calls = 0
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('session.messages', () => ({ value: MESSAGES.map(m => ({ ...m, toolUses: [] })) }))
  on('model.complete', () => {
    calls += 1
    return { value: { isAnswered: true, text: 'x', usage: USAGE } } as const
  })
  on('ui.status', () => ({ value: undefined }))
  on('turn.complete', (_$, e) => ({ text: e.answer }))

  await $.session.start({ cwd: '/w', surface: null, isInteractive: false })
  await $.turn.complete({ answer: 'done', durationMs: 5, isAborted: false, turnId: 't4', reason: 'answer' })
  await clock.advance(10)

  expect(calls).toBe(0)
})

test('the prompt helpers mirror the server summarizer', () => {
  expect(cleanReply('# Label: `Fixing` the **picker**.')).toBe('Fixing the picker')
  expect(buildPrompt([{ role: 'assistant', text: 'hi', toolUses: [] }], '')).toBeNull()
  expect(buildPrompt([{ role: 'user', text: '<bash-input>ls</bash-input>', toolUses: [] }], 'Old')).toContain('previous label: Old')
})
