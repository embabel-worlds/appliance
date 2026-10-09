// Which appliance the app's docker calls land on. Run with `npm test`.

import assert from 'node:assert/strict'
import path from 'node:path'
import { test } from 'node:test'

import {
  applianceInstance, composeCommand, composeProject, serviceFilter, useSavedInstance, volumeName,
} from './instance'

test('the default instance keeps the project and volume it always had', () => {
  useSavedInstance(undefined)
  assert.equal(applianceInstance({}), 'appliance')
  assert.equal(composeProject('appliance'), 'embabel-appliance')
  assert.equal(volumeName('embabel_assistant_data', 'appliance'), 'embabel-appliance_embabel_assistant_data')
})

test('EMBABEL_INSTANCE wins over the saved setting, which wins over the default', () => {
  useSavedInstance('saved')
  assert.equal(applianceInstance({}), 'saved')
  assert.equal(applianceInstance({ EMBABEL_INSTANCE: 'fresh' }), 'fresh')
  useSavedInstance(undefined)
})

test('a name that cannot be an instance is refused, not swapped for the default', () => {
  assert.throws(() => applianceInstance({ EMBABEL_INSTANCE: '../etc' }))
  assert.throws(() => applianceInstance({ EMBABEL_INSTANCE: 'My World' }))
})

test('compose goes through the appliance command, named for the instance', () => {
  // scripts/check-compose-project.py runs exactly this argv through the CLI and checks it
  // reaches docker with the same files, memory limits and profiles as `embabel up`.
  assert.deepEqual(composeCommand('/opt/appliance', ['up', '-d', 'assistant'], 'fresh'), [
    path.join('/opt/appliance', 'embabel'),
    ['--instance', 'fresh', 'compose', '--me', 'up', '-d', 'assistant'],
  ])
  assert.deepEqual(composeCommand('/opt/appliance', ['pull'], 'appliance')[1],
    ['--instance', 'appliance', 'compose', '--me', 'pull'])
})

test('a service is found by project and service label, never by service alone', () => {
  assert.deepEqual(serviceFilter('worlds', 'fresh'), [
    '--filter', 'label=com.docker.compose.project=embabel-fresh',
    '--filter', 'label=com.docker.compose.service=worlds',
  ])
})
