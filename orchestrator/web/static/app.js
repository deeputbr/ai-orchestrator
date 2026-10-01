'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pretty = value => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
const money = value => '$' + (value / 1000000).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:6});
const labels = {PENDING:'Ready / queued', RUNNING:'Running', COMPLETED:'Completed', FAILED:'Failed', WAITING_FOR_APPROVAL:'Needs attention'};
let token, state, detail, selected = localStorage.getItem('orchestrator-project'), editing = false, editingTask = null, loading = false, lastJob, networkFailed = false, stopped = false, lastRendered;

async function api(path, data) {
  const response = await fetch(path, data === undefined ? {cache:'no-store'} : {method:'POST', headers:{'Content-Type':'application/json','X-Orchestrator-Token':token}, body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'The action could not be completed.');
  return result;
}
function notice(message, error=false) { $('notice').textContent=message; $('notice').className=error?'error':''; $('notice').hidden=false; }
function close(dialog) { dialog.close(); }
function open(id) { const dialog=$(id); dialog.querySelectorAll('.form-error').forEach(el=>el.textContent=''); dialog.showModal(); }
function download(name, data) {
  const url=URL.createObjectURL(new Blob([pretty(data)],{type:'application/json'}));
  const link=document.createElement('a'); link.href=url; link.download=name; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function formAction(form, fn) {
  const button=form.querySelector('button[type=submit]'); if(button)button.disabled=true;
  const error=form.querySelector('.form-error'); if(error)error.textContent='';
  try {await fn();} catch(e) {if(error)error.textContent=e.message; else notice(e.message,true);} finally {if(button)button.disabled=false;}
}
async function action(name, data, message) {
  const result=await api('/api/actions/'+name,data);
  if(message)notice(message);
  await refresh(); return result;
}
async function refresh() {
  if(loading || stopped)return; loading=true;
  try {
    state=await api('/api/state');
    if(!state.projects.some(p=>p.project.id===selected))selected=state.projects[0]?.project.id || null;
    detail=selected?await api('/api/projects/'+encodeURIComponent(selected)):null;
    if(selected)localStorage.setItem('orchestrator-project',selected);
    const rendered=JSON.stringify([state,detail,selected]);
    if(rendered!==lastRendered){render();lastRendered=rendered;}
    const jobKey=state.job?JSON.stringify(state.job):'';
    if(state.job && state.job.status!=='running' && lastJob!==jobKey) {
      if(state.job.error)notice(state.job.error,true);
      else if(lastJob && selected===state.job.project_id)notice('Run finished. Review the results and any tasks that need your attention.');
    }
    lastJob=jobKey;
    if(networkFailed){notice('Reconnected to the local app.');networkFailed=false;}
  } catch(e) {networkFailed=true;notice('Cannot reach the local app. Reopen the launcher if it has stopped. '+e.message,true);} finally {loading=false;}
}
function render() {
  $('project-count').textContent=state.projects.length;
  $('projects').innerHTML=state.projects.map(p=>`<button class="project-link ${selected===p.project.id?'active':''}" data-project="${esc(p.project.id)}"><span>${esc(p.project.id)}</span></button>`).join('');
  $('welcome').hidden=!!selected; $('workspace').hidden=!selected;
  const cfg=state.connections;
  $('connection-status').textContent=cfg.jev_key_present?'Jev connected':'Connect Jev for live work';
  $('jev-status').textContent=cfg.jev_key_present?'Token available · '+cfg.jev_key_source:'Not connected';
  $('jev-status').className='pill '+(cfg.jev_key_present?'completed':'');
  $('generation-name').textContent=cfg.backend==='chatgpt'?'ChatGPT subscription':'OpenAI API billing';
  $('generation-detail').textContent=(cfg.backend==='chatgpt'?'Uses your existing Codex / ChatGPT sign-in. ':'Uses the configured OPENAI_API_KEY. ')+cfg.models.sol+' · '+cfg.models.astra;
  $('caps-detail').textContent=`Project subscription caps: ${cfg.max_subscription_calls} total calls, ${cfg.max_subscription_astra_calls} Astra calls. Context files are read only within: ${cfg.context_root}`;
  const busy=state.job?.status==='running';
  ['run-project','add-task','edit-project','new-project','first-project','clear-key','check-connections','stop-app'].forEach(id=>$(id).disabled=busy);
  $('connections-dialog').querySelector('button[type=submit]').disabled=busy;
  $('run-project').textContent=busy?'Running…':'▶ Run workflow';
  if(!detail){$('breadcrumb').textContent='Overview';return;}
  const summary=detail.summary,p=summary.project;
  $('breadcrumb').textContent=p.id; $('project-title').textContent=p.id; $('project-goal').textContent=p.goal;
  $('metric-progress').textContent=`${summary.completed.length} / ${summary.tasks}`;
  $('progress-fill').style.width=(summary.tasks?100*summary.completed.length/summary.tasks:0)+'%';
  $('metric-attention').textContent=summary.blockers.length;
  $('metric-spend').textContent=money(summary.spend.total_microusd);
  $('budget-detail').textContent=money(summary.budget_remaining_microusd)+' available of '+money(p.budget_microusd)+(summary.spend.reserved_microusd?' · '+money(summary.spend.reserved_microusd)+' reserved':'');
  $('metric-calls').textContent=summary.subscription_usage.calls;
  $('run-banner').hidden=!busy;
  if(busy)$('run-banner').innerHTML=`<span class="spinner"></span>${esc(state.job.project_id)} is running in ${state.job.mode==='live'?'live':'simulation'} mode. Progress updates automatically.`;
  $('templates').innerHTML=state.templates.map(t=>`<button class="template" aria-label="Add ${esc(t.name)} workflow" data-template="${esc(t.id)}" ${busy?'disabled':''}><h3>${esc(t.name)}<span>＋</span></h3><p>${esc(t.description)}</p></button>`).join('');
  $('workflow-file').disabled=busy;
  $('task-list').innerHTML=detail.tasks.length?detail.tasks.map((t,i)=>taskRow(t,i,busy)).join(''):'<div class="empty-tasks"><span class="empty-symbol">◇</span><h3>A fresh canvas for your work.</h3><p>Choose a workflow from the library, or add your first task.</p></div>';
  $('events').innerHTML=detail.events.slice(-5).reverse().map(e=>`<div class="event">${esc(e.task_id?e.task_id+' · ':'')}${esc(e.kind.replaceAll('_',' '))}<small>${esc(new Date(e.created_at).toLocaleString())}</small></div>`).join('') || '<p class="subtle">Your project activity will appear here.</p>';
  updateModeHelp();
}
function taskRow(t,i,busy) {
  const gate=t.metadata.gate;
  const simulated=t.metadata.execution_mode==='mock' && t.selected_tier && t.selected_tier!=='CODE';
  let gateBlock='';
  if(gate)gateBlock=`<div class="gate"><p>${esc(gate.reason)}${gate.expected_cost_microusd!=null?' · Request estimate '+money(gate.expected_cost_microusd):''}</p><p>${esc(gate.recommended_action)}</p>${gate.kind==='approval'?`<button class="primary" data-task-action="approve" data-task="${esc(t.id)}" ${busy?'disabled':''}>Approve this request</button>`:gate.kind!=='reconcile'?`<button class="secondary" data-task-action="resume" data-task="${esc(t.id)}" ${busy?'disabled':''}>Recheck task</button>`:''}<button class="secondary" data-details="${esc(t.id)}">Review details</button></div>`;
  const editable=t.status==='PENDING' && !t.metadata.execution_mode && !detail.attempts.some(a=>a.task_id===t.id) && t.task_type!=='jev_decision';
  let output=pretty(t.output);
  if(t.task_type==='jev_decision' && t.output?.answers?.selection){const answer=t.output.answers.selection;output='Jev chose: '+(t.inputs.questions?.selection?.criteria?.[answer.choice] || answer.choice)+'\nConfidence: '+Math.round(answer.confidence*100)+'%';}
  return `<article class="task-row"><div class="task-number ${t.status==='COMPLETED'?'done':''}">${t.status==='COMPLETED'?'✓':String(i+1).padStart(2,'0')}</div><div class="task-body"><div class="task-header"><button data-details="${esc(t.id)}">${esc(t.id)}</button><span class="pill ${esc(t.status.toLowerCase())}">${labels[t.status] || esc(t.status)}</span></div><p class="task-description">${esc(t.description)}</p><div class="task-meta"><span>${esc(t.selected_tier || 'Awaiting route')}</span><span>·</span><span>${esc(t.task_type.replaceAll('_',' '))}</span>${t.dependencies.length?`<span>· After ${esc(t.dependencies.join(', '))}</span>`:''}${simulated?'<span class="pill">SIMULATED</span>':''}${t.sensitive?'<span class="pill">LOCAL ONLY</span>':''}${editable?`<button class="quiet" data-edit-task="${esc(t.id)}" ${busy?'disabled':''}>Edit task</button>`:''}</div>${t.output!=null?`<div class="output-preview">${esc(output.slice(0,600))}</div>`:''}${gateBlock}</div></article>`;
}
function updateModeHelp() {
  $('mode-help').textContent=$('run-mode').value==='live'?'Live work uses Jev plus '+(state?.connections.backend==='api'?'direct OpenAI API billing.':'your ChatGPT subscription. Both inputs and outputs may be shared.'):'Local tasks compute real results. AI results in simulation are marked SIMULATED.';
}
function projectForm(edit=false) {
  editing=edit; const form=$('project-form');form.reset();
  $('project-dialog-title').textContent=edit?'Project settings':'New project';
  form.querySelector('button[type=submit]').textContent=edit?'Save settings':'Create project';
  form.elements.id.disabled=edit;
  form.elements.goal.disabled=edit;
  if(edit){const p=detail.summary.project;form.elements.id.value=p.id;form.elements.goal.value=p.goal;form.elements.budget_usd.value=p.budget_microusd/1000000;form.elements.allow_external.checked=p.allow_external;}
  open('project-dialog');
}
async function run(live) {
  const result=await action('run',{project_id:selected,live},'Workflow started.');
  lastJob=JSON.stringify(result);
}
function taskDetails(id) {
  const task=detail.tasks.find(t=>t.id===id); if(!task)return;
  $('detail-title').textContent=task.id;
  const sections=[['Instructions',task.description],['Result',task.output??'No result yet.'],['Pending review',task.metadata.gate],['Validation',task.validation_results],['Inputs & routing', {inputs:task.inputs,context_files:task.context_files,dependencies:task.dependencies,selected_tier:task.selected_tier,selected_model:task.selected_model,metadata:task.metadata}],['Attempt history',detail.attempts.filter(a=>a.task_id===id)]];
  $('detail-content').innerHTML=sections.filter(s=>s[1]!=null).map(([name,value])=>`<section class="detail-section"><h3>${name}</h3><pre>${esc(pretty(value))}</pre></section>`).join('')+`<button class="secondary" data-output-download="${esc(id)}">↓ Download task result</button>`;
  const unknown=detail.attempts.filter(a=>a.task_id===id && ['STARTED','UNKNOWN'].includes(a.status));
  $('detail-content').innerHTML+=unknown.map(a=>`<form class="reconcile-form" data-attempt="${a.id}"><h3>Resolve uncertain request #${a.id}</h3><p class="field-help">Verify the provider outcome before recording it. This clears the reservation and does not replay the request.</p><label>Verified API charge (USD)<input name="cost_usd" type="number" required min="0" step="0.000001" value="0" ${a.data.billing==='chatgpt_subscription'?'readonly':''}></label>${a.data.billing==='chatgpt_subscription'?'<p class="field-help">Subscription requests have no API USD charge; enter an outcome note.</p>':''}<label>Verification note<textarea name="note" required rows="2" placeholder="What evidence confirms the outcome?"></textarea></label><label class="check"><input type="checkbox" name="not_dispatched"><span>I verified that the request never reached the provider</span></label><div class="form-error" role="alert"></div><button class="secondary" type="submit">Record verified outcome</button></form>`).join('');
  open('detail-dialog');
}

function taskForm(id=null) {
  editingTask=id?detail.tasks.find(t=>t.id===id):null;
  const form=$('task-form'); form.reset();
  $('task-dialog-title').textContent=id?'Edit task':'Add a task';
  form.querySelector('button[type=submit]').textContent=id?'Save task':'Add task';
  form.elements.id.disabled=!!id; form.elements.task_type.disabled=!!id;
  // Template types such as content/coding use the AI generation form.
  if(editingTask){const t=editingTask;form.elements.id.value=t.id;form.elements.task_type.value=['word_count','sha256','json_normalize'].includes(t.task_type)?t.task_type:'generation';form.elements.description.value=t.description;form.elements.text.value=t.inputs.text??t.inputs.brief??'';form.elements.risk.value=t.risk;form.elements.complexity.value=t.complexity;form.elements.requires_approval.checked=t.requires_approval;form.elements.sensitive.checked=t.sensitive;}
  $('dependency-options').innerHTML=detail.tasks.filter(t=>t.id!==id).map(t=>`<label><input type="checkbox" name="dependency" value="${esc(t.id)}" ${editingTask?.dependencies.includes(t.id)?'checked':''} ${id?'disabled':''}>${esc(t.id)}</label>`).join('') || '<span class="field-help">No earlier tasks yet.</span>';
  taskTypeChanged();open('task-dialog');
}
function taskTypeChanged() {$('choices-field').hidden=$('task-form').elements.task_type.value!=='jev_decision';}

document.querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',()=>close(b.closest('dialog'))));
$('new-project').onclick=()=>projectForm(); $('first-project').onclick=()=>projectForm(); $('edit-project').onclick=()=>projectForm(true);
$('refresh').onclick=refresh;
$('connections-button').onclick=()=>open('connections-dialog');
$('setup-mobile').onclick=()=>open('connections-dialog');
$('run-mode').onchange=updateModeHelp;
$('projects').onclick=async e=>{const b=e.target.closest('[data-project]');if(b){selected=b.dataset.project;await refresh();}};
$('project-form').onsubmit=e=>{e.preventDefault();formAction(e.target,async()=>{const f=e.target.elements;const data={budget_usd:f.budget_usd.value,allow_external:f.allow_external.checked};if(editing)data.project_id=selected;else Object.assign(data,{id:f.id.value,goal:f.goal.value});await api('/api/actions/'+(editing?'configure':'create'),data);if(!editing)selected=f.id.value;close($('project-dialog'));await refresh();notice(editing?'Project settings saved.':'Project created. Choose a workflow or add a task.');});};
$('add-task').onclick=()=>taskForm();
$('task-form').elements.task_type.onchange=taskTypeChanged;
$('task-form').onsubmit=e=>{e.preventDefault();formAction(e.target,async()=>{const f=e.target.elements;let inputs={...(editingTask?.inputs || {})};inputs[f.task_type.value==='generation' || f.task_type.value==='jev_decision'?'brief':'text']=f.text.value;if(f.task_type.value==='jev_decision'){const options=f.choices.value.split('\n').map(s=>s.trim()).filter(Boolean);if(options.length<2 || options.length>255)throw new Error('Enter between 2 and 255 options, one per line.');inputs.questions={selection:{type:'choice',instructions:f.description.value,criteria:Object.fromEntries(options.map((s,i)=>['option_'+(i+1),s]))}};}const changes={description:f.description.value,inputs,risk:f.risk.value,complexity:f.complexity.value,requires_approval:f.requires_approval.checked,sensitive:f.sensitive.checked};if(editingTask){await action('edit-task',{project_id:selected,task_id:editingTask.id,changes},'Task updated.');}else{const task={...changes,id:f.id.value,task_type:f.task_type.value,dependencies:[...e.target.querySelectorAll('[name=dependency]:checked')].map(i=>i.value)};await action('import',{project_id:selected,workflow:{tasks:[task]}},'Task added.');}close($('task-dialog'));});};
$('templates').onclick=async e=>{const b=e.target.closest('[data-template]');if(!b)return;b.disabled=true;try{await action('import',{project_id:selected,template:b.dataset.template},'Workflow added. Review the sample instructions before running.');}catch(err){notice(err.message,true);}finally{b.disabled=false;}};
$('workflow-file').onchange=async e=>{const file=e.target.files[0];if(!file)return;try{if(file.size>900000)throw new Error('Choose a workflow JSON file smaller than 900 KB.');const workflow=JSON.parse(await file.text());await action('import',{project_id:selected,workflow},'Workflow imported.');}catch(err){notice(err.message,true);}finally{e.target.value='';}};
$('run-project').onclick=async()=>{try{if(!detail.tasks.length)throw new Error('Add a workflow or task first.');if($('run-mode').value==='live'){const p=detail.summary.project;if(!p.allow_external)throw new Error('Enable external sharing in Project settings before starting live work.');$('live-summary').textContent=`Project: ${p.id} · API budget remaining: ${money(detail.summary.budget_remaining_microusd)} · ${state.connections.backend==='chatgpt'?'ChatGPT subscription':'Direct API billing'} · ${detail.tasks.length} tasks`;open('live-dialog');}else await run(false);}catch(e){notice(e.message,true);}};
$('live-form').onsubmit=e=>{e.preventDefault();formAction(e.target,async()=>{await run(true);close($('live-dialog'));});};
$('task-list').onclick=async e=>{const edit=e.target.closest('[data-edit-task]');if(edit)return taskForm(edit.dataset.editTask);const details=e.target.closest('[data-details]');if(details)return taskDetails(details.dataset.details);const b=e.target.closest('[data-task-action]');if(b){b.disabled=true;try{await action(b.dataset.taskAction,{project_id:selected,task_id:b.dataset.task},b.dataset.taskAction==='approve'?'Request approved. Run the workflow to continue.':'Task queued for rechecking. Run the workflow to continue.');}catch(err){notice(err.message,true);}finally{b.disabled=false;}}};
$('detail-content').onclick=e=>{const b=e.target.closest('[data-output-download]');if(b){const t=detail.tasks.find(t=>t.id===b.dataset.outputDownload);download(t.id+'-result.json',t.output??{status:t.status});}};
$('detail-content').onsubmit=e=>{if(!e.target.matches('.reconcile-form'))return;e.preventDefault();formAction(e.target,async()=>{const f=e.target.elements;await action('reconcile',{project_id:selected,attempt_id:Number(e.target.dataset.attempt),cost_usd:f.cost_usd.value,note:f.note.value,not_dispatched:f.not_dispatched.checked},'Verified outcome recorded. Inspect the saved result before deciding whether to recheck the task.');close($('detail-dialog'));});};
$('export-project').onclick=()=>download(selected+'-snapshot.json',detail);
$('key-form').onsubmit=e=>{e.preventDefault();formAction(e.target,async()=>{await action('connections',{jev_key:e.target.elements.jev_key.value.trim()},'Jev token connected for this app session.');e.target.reset();});};
$('clear-key').onclick=async()=>{try{await action('connections',{jev_key:''},'Session token cleared. An environment token, if configured, is still available.');$('key-form').reset();}catch(e){notice(e.message,true);}};
$('check-connections').onclick=async()=>{const b=$('check-connections');b.disabled=true;b.textContent='Checking…';try{const result=await api('/api/actions/doctor',{});$('doctor-result').textContent=(result.ready_for_live?'Connections are ready for a live attempt.':'Setup needs attention before a live attempt.')+'\n\n'+pretty(result);$('doctor-result').hidden=false;}catch(e){notice(e.message,true);}finally{b.disabled=false;b.textContent='Check connections';}};
$('stop-app').onclick=async()=>{try{await api('/api/actions/shutdown',{});stopped=true;close($('connections-dialog'));notice('The local app has stopped. Double-click the launcher to open it again.');document.querySelectorAll('button').forEach(b=>b.disabled=true);}catch(e){notice(e.message,true);}};
(async()=>{try{token=(await api('/api/session')).token;await refresh();setInterval(()=>{if(!document.hidden)refresh();},2500);}catch(e){notice('Could not initialize the local dashboard: '+e.message,true);}})();
