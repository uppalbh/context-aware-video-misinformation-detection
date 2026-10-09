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
  const context = vm.createContext({document, console, URL, setTimeout: () => 0, clearTimeout: () => {},
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

test('report comparison preserves clocks, marks omissions, cites evidence and labels synthetic abstention', () => {
  const {context, get} = app();
  vm.runInContext(`renderReport({synthetic:true, assessment:'inconclusive', source_match_confidence:1,
    context_risk_score:null, evidence_coverage:1, context_risk_explanation:'Ordinal heuristic, unavailable.',
    source:{title:'SYNTHETIC source',url:'https://example.invalid/source',duration:180,provenance:'Fabricated',permission:'Project authored'},
    semantic_status:'unavailable: lexical retrieval only', ranking_limits:'Heuristic',
    candidates:[{title:'Candidate',url:'javascript:alert(1)',rank_score:.9,fuzzy_token_coverage:1,kind:'synthetic'}],
    alignment:{passages:[{clip_id:'clip:c0',clip_start:0,clip_end:6,source_start:100,source_end:115,match:'exact'}],
      evidence:[{id:'clip:c0',timeline:'uploaded_clip',start:0,end:6,text:'<script>literal</script>'},
        {id:'source:s1',timeline:'original_source',start:100,end:115,text:'Close only if safe.'}],
      omitted:[{evidence_id:'source:s1',char_start:6,char_end:18}],reordered:false,disjoint:false},
    context:{findings:[{finding:'Mock fixture',severity:'moderate',support_ids:['clip:c0','source:s1'],contradiction_ids:[],
      quotes:[{evidence_id:'source:s1',text:'only if safe'}]}]},uncertainty:['No real-world conclusion.']});`, context);
  assert.equal(get('synthetic-warning').hidden, false);
  assert.match(get('scores').textContent, /unavailable \/ inconclusive/);
  assert.equal(get('timeline').children[0].children[1].value, 0);
  assert.equal(get('timeline').children[0].children[2].value, 100);
  assert.equal(get('source-evidence').children[0].children[2].textContent, 'only if safe');
  assert.equal(get('clip-evidence').children[0].children[1].textContent, '<script>literal</script>');
  assert.equal(get('candidates').children[0].children[0].href, undefined);
  assert.match(get('findings').children[0].children[0].textContent, /source:s1/);
  vm.runInContext('renderReport(null)', context);
  assert.equal(get('report-view').hidden, true);
});

test('saved report history reopens and investigation retries poll without retranscription', async () => {
  const {context, get} = app();
  const requests = [];
  context.fetch = async (url, options) => {
    requests.push({url, options});
    const row = {id:'a'.repeat(32),status:'transcribed',created_at:1,investigation_status:'queued',transcript:{segments:[]},report:null};
    return {ok:true,status:200,json:async () => url === '/api/analyses' ? [row] : row};
  };
  await vm.runInContext('history()', context);
  await get('history').children[0].onclick();
  assert.match(get('status').textContent, /Investigation queued/);
  assert.equal(get('delete').hidden, true);
  assert.equal(get('investigate').hidden, true);
  await get('investigate').onclick();
  assert.equal(requests.find(r => r.options?.method === 'POST').url, '/api/analyses/' + 'a'.repeat(32) + '/investigate');
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
