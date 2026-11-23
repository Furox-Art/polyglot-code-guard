#!/usr/bin/env node
'use strict';

const { spawnSync } = require('node:child_process');
const { name, version } = require('../../package.json');

const override = process.env.POLYGUARD_PYTHON;
const interpreters = override
  ? [[override]]
  : process.platform === 'win32'
    ? [['python'], ['python3'], ['py', '-3']]
    : [['python3'], ['python']];
const versionCheck = [
  'from importlib.metadata import version',
  'import sys',
  'raise SystemExit(version(sys.argv[1]) != sys.argv[2])',
].join('\n');

function main(args) {
  for (const [python, ...prefix] of interpreters) {
    const probe = spawnSync(python, [...prefix, '-c', versionCheck, name, version], {
      stdio: 'ignore',
      timeout: 5000,
    });
    if (probe.error || probe.status !== 0) continue;

    const result = spawnSync(python, [...prefix, '-m', 'polyguard.cli', ...args], {
      stdio: 'inherit',
    });
    if (result.error) console.error(name + ': ' + result.error.message);
    return result.status ?? 1;
  }

  console.error(name + ': install the matching Python package in an active environment.');
  console.error('python -m pip install ' + name + '==' + version);
  console.error('Set POLYGUARD_PYTHON to select a Python executable.');
  return 1;
}

process.exitCode = main(process.argv.slice(2));
