const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function app() {
  const elements = new Map();
  function element() {
    return {hidden:false, disabled:false, children:[], textContent:'', value:0,
      append(...nodes) { this.children.push(...nodes); },
      replaceChildren(...nodes) { this.children = nodes; }};
  }
  const document = {
    getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
    createElement: element,
  };
  const context = vm.createContext({document, console, setTimeout: () => 0, clearTimeout: () => {},
    // Leave startup fetch pending; deterministic render tests supply explicit test fixtures only.
    fetch: () => new Promise(() => {})});
  vm.runInContext(fs.readFileSync('app/static/app.js', 'utf8'), context);
  return {context, get: id => document.getElementById(id)};
}

test('bounded seconds rendering, 01:05 navigation, and legacy segment context', () => {
  const {context, get} = app();
  vm.runInContext(`currentTranscript = {
    word_timing_status: 'available',
    transcript_by_second: Object.fromEntries(Array.from({length:3600}, (_,i) => [String(i), i === 65 ? ['fixture'] : []])),
    segments: Array.from({length:250}, (_,i) => ({start:i, end:i+1, text:'segment fixture'}))
  }; renderTranscript();`, context);
  assert.equal(get('seconds').children.length, 60);
  assert.equal(get('segments').children.length, 100);
  assert.match(get('seconds').children[0].children[1].textContent, /No word starts/);
  get('second-jump').value = '65'; get('second-jump').onchange();
  assert.equal(get('seconds').children[0].children[0].textContent, '01:05');
  assert.equal(get('seconds').children[0].children[1].textContent, 'fixture');
  get('second-jump').value = '99999'; get('second-jump').onchange();
  assert.equal(get('seconds').children.length, 1);
  assert.equal(get('second-next').disabled, true);
  get('segment-next').onclick();
  assert.equal(get('segments').children.length, 100);
  get('segment-next').onclick();
  assert.equal(get('segments').children.length, 50);
  vm.runInContext(`currentTranscript = {segments:[{start:0,end:1,text:'legacy fixture'}]}; renderTranscript();`, context);
  assert.equal(get('seconds-view').hidden, true);
  assert.equal(get('word-unavailable').hidden, false);
  assert.equal(get('segments').children.length, 1);
  assert.equal(get('download').hidden, false);
});
