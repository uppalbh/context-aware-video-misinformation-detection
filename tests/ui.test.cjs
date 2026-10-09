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

test('URL form queues JSON, displays downloading state and safe structured errors', async () => {
  const {context, get} = app();
  const requests = [];
  context.fetch = async (url, options) => {
    requests.push({url, options});
    const result = url === '/api/analyses/url' ? {analysis_id:'a'.repeat(32), status:'queued'}
      : url === '/api/analyses' ? [] : {status:'downloading', transcript:null};
    return {ok:true, status:200, json:async () => result};
  };
  get('video-url').value = 'https://media.w3.org/fixture.mp4';
  await get('url-upload').onsubmit({preventDefault() {}});
  assert.equal(requests[0].url, '/api/analyses/url');
  assert.equal(requests[0].options.method, 'POST');
  assert.equal(JSON.parse(requests[0].options.body).url, get('video-url').value);
  assert.match(get('status').textContent, /Downloading supported public video/);
  assert.equal(get('url-submit').disabled, false);
  context.fetch = async () => ({ok:false, status:422, json:async () => ({detail:{code:'unsupported_url',message:'Use a supported host.'}})});
  await get('url-upload').onsubmit({preventDefault() {}});
  assert.equal(get('notice').textContent, 'Use a supported host.');
  assert.equal(get('url-submit').disabled, false);
});
