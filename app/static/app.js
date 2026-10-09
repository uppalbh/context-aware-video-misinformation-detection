const $ = id => document.getElementById(id);
let limits, current, timer, currentTranscript, currentReport, secondOffset = 0, segmentOffset = 0;
const labels = {queued: 'Queued', downloading: 'Downloading supported public video', extracting: 'Validating media and extracting audio',
  transcribing: 'Transcribing speech', transcribed: 'Transcript ready', completed: 'Context report completed', failed: 'Processing failed'};
const investigationLabels = {not_started:'Investigation not started', queued:'Investigation queued', retrieving:'Retrieving and aligning curated sources',
  interpreting:'Interpreting transcript evidence', completed:'Context interpretation completed', source_not_found:'Source not found in corpus',
  inconclusive:'Ambiguous source match', setup_required:'Context model requires server setup', unavailable:'Investigation unavailable', demo_only:'Synthetic alignment demo only'};
const investigating = row => ['queued','retrieving','interpreting'].includes(row.investigation_status);

function textNode(tag, text) { const node = document.createElement(tag); node.textContent = text; return node; }
function sourceLink(source) {
  const node = textNode('a', source.title);
  try { const url = new URL(source.url); if (url.protocol === 'https:') { node.href = url.href; node.target = '_blank'; node.rel = 'noopener noreferrer'; } } catch { /* no unsafe link */ }
  return node;
}
function renderReport(report) {
  currentReport = report; $('report-view').hidden = !report;
  if (!report) return;
  $('synthetic-warning').hidden = !report.synthetic;
  $('assessment').textContent = `Assessment: ${report.assessment.replaceAll('_', ' ')}`;
  $('scores').textContent = `Source quality: ${report.source_match_confidence === null ? 'unaccepted' : report.source_match_confidence.toFixed(3)} (heuristic, not probability) · Context risk: ${report.context_risk_score === null ? 'unavailable / inconclusive' : report.context_risk_score + '/100 (ordinal)'} · Clip-token coverage: ${(report.evidence_coverage * 100).toFixed(1)}%`;
  $('risk-explanation').textContent = report.context_risk_explanation;
  $('source-link').replaceChildren();
  $('source-details').textContent = report.source ? `Provenance: ${report.source.provenance} Permission: ${report.source.permission}` : 'No source accepted.';
  if (report.source) $('source-link').append(sourceLink(report.source));
  $('retrieval-status').textContent = `${report.semantic_status}. ${report.ranking_limits}`;
  $('candidates').replaceChildren();
  for (const candidate of report.candidates) {
    const li = document.createElement('li'); li.append(sourceLink(candidate), textNode('span', ` · rank ${candidate.rank_score.toFixed(3)} · fuzzy coverage ${(candidate.fuzzy_token_coverage * 100).toFixed(1)}% · ${candidate.kind}`)); $('candidates').append(li);
  }
  for (const id of ['timeline','clip-evidence','source-evidence','findings','uncertainty']) $(id).replaceChildren();
  const alignment = report.alignment;
  if (alignment) {
    for (const passage of alignment.passages) {
      const row = document.createElement('div'); row.className = 'timeline-row';
      row.append(textNode('p', `${passage.clip_id} · clip ${timestamp(passage.clip_start)}–${timestamp(passage.clip_end)} ↔ ${passage.source_start === undefined ? 'unmatched' : 'source ' + timestamp(passage.source_start) + '–' + timestamp(passage.source_end)} · ${passage.match}`));
      const clipBar = document.createElement('progress'); clipBar.max = Math.max(1, ...alignment.evidence.filter(e => e.timeline === 'uploaded_clip').map(e => e.end)); clipBar.value = passage.clip_start; clipBar.title = 'Clip segment start';
      row.append(clipBar);
      if (passage.source_start !== undefined) { const sourceBar = document.createElement('progress'); sourceBar.max = report.source.duration; sourceBar.value = passage.source_start; sourceBar.title = 'Source segment start'; row.append(sourceBar); }
      $('timeline').append(row);
    }
    for (const evidence of alignment.evidence) {
      const li = document.createElement('li'); li.append(textNode('p', `${evidence.id} · ${timestamp(evidence.start)}–${timestamp(evidence.end)}`));
      let offset = 0;
      const spans = alignment.omitted.filter(o => o.evidence_id === evidence.id).sort((a,b) => a.char_start - b.char_start);
      for (const span of spans) { li.append(textNode('span', evidence.text.slice(offset, span.char_start)), textNode('mark', evidence.text.slice(span.char_start, span.char_end))); offset = span.char_end; }
      li.append(textNode('span', evidence.text.slice(offset)));
      $(evidence.timeline === 'uploaded_clip' ? 'clip-evidence' : 'source-evidence').append(li);
    }
    if (alignment.reordered || alignment.disjoint) $('timeline').append(textNode('p', 'Passages are disjoint or reordered. No single time offset applies.'));
  }
  $('impressions').textContent = report.context.clip_impression ? `Clip impression: ${report.context.clip_impression} Fuller context: ${report.context.full_context}` : 'No semantic interpretation accepted.';
  for (const finding of report.context.findings) {
    const li = textNode('li', `${finding.finding} Severity: ${finding.severity}. Supporting IDs: ${finding.support_ids.join(', ')}. Contradictory IDs: ${finding.contradiction_ids.join(', ') || 'none listed'}.`);
    for (const quote of finding.quotes) li.append(textNode('blockquote', `${quote.evidence_id}: ${quote.text}`)); $('findings').append(li);
  }
  for (const item of report.uncertainty) $('uncertainty').append(textNode('li', item));
}
async function api(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    try { const detail = (await response.json()).detail; message = typeof detail === 'string' ? detail : detail?.message || message; } catch { /* non-JSON failure */ }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}
function timestamp(value) {
  return `${Math.floor(value / 60)}:${(value % 60).toFixed(1).padStart(4, '0')}`;
}
function elapsed(second) {
  return `${String(Math.floor(second / 60)).padStart(2, '0')}:${String(second % 60).padStart(2, '0')}`;
}
function transcriptRow(label, content) {
  const li = document.createElement('li'), time = document.createElement('time'), text = document.createElement('span');
  time.textContent = label; text.textContent = content; li.append(time, text);
  return li;
}
function renderTranscript() {
  const transcript = currentTranscript;
  const seconds = transcript?.transcript_by_second;
  const available = transcript?.word_timing_status === 'available' && seconds;
  $('seconds-view').hidden = !available;
  $('word-unavailable').hidden = !transcript || Boolean(available);
  $('download').hidden = !transcript;
  $('seconds').replaceChildren();
  if (available) {
    const count = Object.keys(seconds).length;
    secondOffset = Math.max(0, Math.min(secondOffset, Math.max(0, count - 1)));
    $('second-jump').max = count - 1;
    $('second-jump').value = secondOffset;
    const end = Math.min(secondOffset + 60, count);
    $('second-page').textContent = `Seconds ${secondOffset}–${end - 1} of ${count} elapsed-second buckets`;
    for (let second = secondOffset; second < end; second++) {
      $('seconds').append(transcriptRow(elapsed(second), seconds[String(second)]?.join(' ') || 'No word starts in this second.'));
    }
    $('second-prev').disabled = secondOffset === 0;
    $('second-next').disabled = end >= count;
  }
  const segments = transcript?.segments || [];
  segmentOffset = Math.max(0, Math.min(segmentOffset, Math.max(0, segments.length - 1)));
  const segmentEnd = Math.min(segmentOffset + 100, segments.length);
  $('segments').replaceChildren();
  for (const segment of segments.slice(segmentOffset, segmentEnd)) {
    $('segments').append(transcriptRow(`${timestamp(segment.start)}–${timestamp(segment.end)}`, segment.text));
  }
  $('segment-page').textContent = segments.length ? `Segments ${segmentOffset + 1}–${segmentEnd} of ${segments.length}` : '';
  $('segment-prev').disabled = segmentOffset === 0;
  $('segment-next').disabled = segmentEnd >= segments.length;
}
$('second-prev').onclick = () => { secondOffset -= 60; renderTranscript(); };
$('second-next').onclick = () => { secondOffset += 60; renderTranscript(); };
$('second-jump').onchange = () => {
  const value = Number($('second-jump').value);
  if (Number.isInteger(value) && value >= 0) { secondOffset = value; renderTranscript(); }
};
$('segment-prev').onclick = () => { segmentOffset -= 100; renderTranscript(); };
$('segment-next').onclick = () => { segmentOffset += 100; renderTranscript(); };
$('download').onclick = () => {
  if (!currentTranscript) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(currentTranscript, null, 2)], {type:'application/json'}));
  const link = document.createElement('a'); link.href = url; link.download = `clipcontext-${current}-transcript.json`;
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
};
async function history() {
  const rows = await api('/api/analyses');
  $('history').replaceChildren();
  if (!rows.length) $('history').textContent = 'No clips yet.';
  for (const row of rows.reverse()) {
    const button = document.createElement('button');
    button.textContent = `${new Date(row.created_at * 1000).toLocaleString()} · ${labels[row.status]} · ${investigationLabels[row.investigation_status] || ''}${row.ingestion?.kind === 'synthetic_demo' ? ' · SYNTHETIC' : ''}`;
    button.onclick = () => select(row.id);
    $('history').append(button);
  }
}
async function select(id) {
  if (current !== id) { secondOffset = 0; segmentOffset = 0; currentTranscript = null; renderTranscript(); }
  clearTimeout(timer); current = id;
  $('result').hidden = false;
  try {
    const row = await api(`/api/analyses/${id}`);
    if (current !== id) return;
    $('status').textContent = `${labels[row.status]}${row.error ? ': ' + row.error.message : ''} · ${investigationLabels[row.investigation_status] || 'Investigation not started'}${row.investigation_error ? ': ' + row.investigation_error.message : ''}`;
    $('metadata').textContent = row.metadata ? `${row.metadata.duration.toFixed(1)} seconds · ${row.metadata.width} × ${row.metadata.height} · ${row.metadata.frame_rate.toFixed(1)} fps` : '';
    $('retry').hidden = row.status !== 'failed' || row.attempts >= 3;
    $('delete').hidden = !['failed', 'transcribed', 'completed'].includes(row.status) || investigating(row);
    $('investigate').hidden = !row.transcript || investigating(row) || (row.investigation_attempts || 0) >= 5;
    renderReport(row.report);
    currentTranscript = row.transcript;
    renderTranscript();
    if (['queued', 'downloading', 'extracting', 'transcribing'].includes(row.status) || investigating(row)) {
      timer = setTimeout(() => select(id), 2000);
    } else { await history(); }
  } catch (error) { $('status').textContent = `${error.message} Reopen this clip to try fetching again.`; }
}
$('upload').onsubmit = async event => {
  event.preventDefault();
  const file = $('file').files[0];
  if (!limits || !file) return;
  if (!/\.(mp4|mov)$/i.test(file.name)) { $('notice').textContent = 'Choose an MP4 or MOV file.'; return; }
  if (file.size > limits.max_upload_bytes || !file.size) { $('notice').textContent = 'File is empty or exceeds the upload limit.'; return; }
  $('submit').disabled = true; $('notice').textContent = 'Uploading…';
  try {
    const row = await api('/api/analyses', {method:'POST', headers:{'Content-Type': /\.mov$/i.test(file.name) ? 'video/quicktime' : 'video/mp4'}, body:file});
    $('notice').textContent = 'Upload received. Processing in the background.';
    await history(); await select(row.analysis_id);
  } catch (error) { $('notice').textContent = error.message; }
  finally { $('submit').disabled = false; }
};
$('retry').onclick = async () => {
  $('retry').disabled = true;
  try { await api(`/api/analyses/${current}/retry`, {method:'POST'}); await select(current); }
  catch (error) { $('status').textContent = error.message; }
  finally { $('retry').disabled = false; }
};
$('url-upload').onsubmit = async event => {
  event.preventDefault(); $('url-submit').disabled = true; $('notice').textContent = 'Queuing URL…';
  try {
    const row = await api('/api/analyses/url', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url:$('video-url').value.trim()})});
    $('notice').textContent = 'Link queued. Downloading and validation run in the background.';
    await history(); await select(row.analysis_id);
  } catch (error) { $('notice').textContent = error.message; }
  finally { $('url-submit').disabled = false; }
};
$('delete').onclick = async () => {
  try { await api(`/api/analyses/${current}`, {method:'DELETE'}); clearTimeout(timer); current = null; $('result').hidden = true; await history(); }
  catch (error) { $('status').textContent = error.message; }
};
$('investigate').onclick = async () => {
  $('investigate').disabled = true;
  try { await api(`/api/analyses/${current}/investigate`, {method:'POST'}); await select(current); }
  catch (error) { $('status').textContent = error.message; }
  finally { $('investigate').disabled = false; }
};
$('demo').onclick = async () => {
  $('demo').disabled = true;
  try { const row = await api('/api/analyses/demo', {method:'POST'}); await history(); await select(row.analysis_id); }
  catch (error) { $('notice').textContent = error.message; }
  finally { $('demo').disabled = false; }
};
$('report-download').onclick = () => {
  if (!currentReport) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(currentReport, null, 2)], {type:'application/json'}));
  const link = document.createElement('a'); link.href = url; link.download = `clipcontext-${current}-report.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
};
(async () => {
  try {
    limits = await api('/api/config');
    $('demo-section').hidden = !limits.synthetic_demo;
    $('url-upload').hidden = !limits.url_ingestion;
    $('url-support').textContent = `Supported hosts: ${(limits.video_url_hosts || []).join(', ')}. HTTPS port 443; no query or fragment.`;
    $('limits').textContent = `MP4 or MOV · up to ${limits.max_upload_bytes / 1024 / 1024} MB · ${limits.max_duration_seconds} seconds. The server validates actual media.`;
    await history();
  } catch (error) { $('notice').textContent = error.message; }
})();
