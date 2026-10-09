const $ = id => document.getElementById(id);
let limits, current, timer;
const labels = {queued: 'Queued', extracting: 'Validating media and extracting audio',
  transcribing: 'Transcribing speech', transcribed: 'Transcript ready · investigation not started', failed: 'Processing failed'};
async function api(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    try { message = (await response.json()).detail || message; } catch { /* non-JSON failure */ }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}
function timestamp(value) {
  return `${Math.floor(value / 60)}:${(value % 60).toFixed(1).padStart(4, '0')}`;
}
async function history() {
  const rows = await api('/api/analyses');
  $('history').replaceChildren();
  if (!rows.length) $('history').textContent = 'No clips yet.';
  for (const row of rows.reverse()) {
    const button = document.createElement('button');
    button.textContent = `${new Date(row.created_at * 1000).toLocaleString()} · ${labels[row.status]}`;
    button.onclick = () => select(row.id);
    $('history').append(button);
  }
}
async function select(id) {
  clearTimeout(timer); current = id;
  $('result').hidden = false;
  try {
    const row = await api(`/api/analyses/${id}`);
    if (current !== id) return;
    $('status').textContent = row.error ? `${labels[row.status]}: ${row.error.message}` : labels[row.status];
    $('metadata').textContent = row.metadata ? `${row.metadata.duration.toFixed(1)} seconds · ${row.metadata.width} × ${row.metadata.height} · ${row.metadata.frame_rate.toFixed(1)} fps` : '';
    $('retry').hidden = row.status !== 'failed' || row.attempts >= 3;
    $('delete').hidden = !['failed', 'transcribed'].includes(row.status);
    $('segments').replaceChildren();
    for (const segment of row.transcript?.segments || []) {
      const li = document.createElement('li'), time = document.createElement('time'), text = document.createElement('span');
      time.textContent = `${timestamp(segment.start)}–${timestamp(segment.end)}`;
      text.textContent = segment.text; li.append(time, text); $('segments').append(li);
    }
    if (['queued', 'extracting', 'transcribing'].includes(row.status)) {
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
$('delete').onclick = async () => {
  try { await api(`/api/analyses/${current}`, {method:'DELETE'}); clearTimeout(timer); current = null; $('result').hidden = true; await history(); }
  catch (error) { $('status').textContent = error.message; }
};
(async () => {
  try {
    limits = await api('/api/config');
    $('limits').textContent = `MP4 or MOV · up to ${limits.max_upload_bytes / 1024 / 1024} MB · ${limits.max_duration_seconds} seconds. The server validates actual media.`;
    await history();
  } catch (error) { $('notice').textContent = error.message; }
})();
