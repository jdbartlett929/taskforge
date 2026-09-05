const $ = id => document.getElementById(id);
let apiKey = "", connected = false, selected = null, offset = 0, refreshing = false;
const limit = 20;
const payloads = {primes:{limit:100000}, fibonacci:{n:100}, hash:{text:"TaskForge",iterations:10000}, retry_demo:{fail_until_attempt:2}, sleep:{seconds:2}};
function node(tag, text, cls) { const element = document.createElement(tag); if(text !== undefined) element.textContent = text; if(cls) element.className = cls; return element; }
function badge(status) { return node("span", status.replaceAll("_"," "), "badge " + status); }
function stamp(time) { return time ? new Date(time*1000).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"}) : "—"; }
async function api(path, options={}) {
  const response = await fetch(path, {...options, headers: {"Content-Type":"application/json", "X-API-Key":apiKey, ...options.headers}});
  const data = await response.json();
  if(!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data));
  return data;
}
function showError(error) { $("error").hidden = !error; $("error").textContent = error || ""; }
function connection(state) { connected=state; $("connection-label").textContent=state?"Connected · polling every 2s":"Disconnected"; $("connection-dot").className=state?"online":""; }
function renderWorkers(items) {
  $("worker-count").textContent=items.length; $("workers").replaceChildren();
  if(!items.length) { $("workers").append(node("p","No workers have a live heartbeat. Jobs remain safely queued.","empty")); return; }
  for(const worker of items) {
    const tile=node("article",undefined,"worker");
    tile.append(node("i",undefined,worker.current_job?"busy":"idle"), node("strong",worker.id), node("span",worker.current_job?"Processing "+worker.current_job.slice(0,8):"Idle · ready for work"));
    $("workers").append(tile);
  }
}
function renderJobs(data) {
  $("jobs").replaceChildren(); $("jobs-empty").hidden=!!data.items.length;
  for(const job of data.items) {
    const row=node("tr"); if(selected===job.id) row.className="selected";
    const name=node("td"), button=node("button",job.task,"job-link");
    button.addEventListener("click",()=> {selected=job.id; refresh();});
    name.append(button,node("small",job.id.slice(0,8)));
    const priority=node("td"); priority.append(node("span",["Low","Normal","High"][job.priority],"priority p"+job.priority));
    const status=node("td"); status.append(badge(job.status));
    row.append(name,priority,status,node("td",job.attempts+"/"+job.max_attempts),node("td",stamp(job.created_at)));
    $("jobs").append(row);
  }
  $("page-info").textContent=data.total?(offset+1)+"–"+(offset+data.items.length)+" of "+data.total+" jobs":"0 jobs";
  $("previous").disabled=offset===0; $("next").disabled=offset+limit>=data.total;
}
function renderDetail(job) {
  const body=$("detail"); body.replaceChildren();
  const heading=node("div",undefined,"detail-title"); heading.append(node("h3",job.task),badge(job.status)); body.append(heading,node("p",job.id,"job-id"));
  const facts=node("dl",undefined,"facts");
  for(const [key,value] of [["Priority",["Low","Normal","High"][job.priority]],["Attempts",job.attempts+" / "+job.max_attempts],["Worker",job.worker_id||"Awaiting assignment"],["Timeout",job.timeout_seconds+"s"]]) {
    const row=node("div"); row.append(node("dt",key),node("dd",value)); facts.append(row);
  }
  body.append(facts,node("h4","PAYLOAD"),node("pre",JSON.stringify(job.payload,null,2)));
  if(job.result) body.append(node("h4","RESULT"),node("pre",JSON.stringify(job.result,null,2),"result"));
  if(job.error) body.append(node("p",job.error,"attempt-error"));
  body.append(node("h4","PERSISTED TIMELINE"));
  const timeline=node("ol",undefined,"timeline");
  for(const event of job.history) { const item=node("li"); item.append(node("span",stamp(event.at),"event-time"),node("b",event.kind.replaceAll("_"," ")),node("p",event.detail)); timeline.append(item); }
  body.append(timeline);
}
async function refresh() {
  if(!connected||refreshing) return;
  refreshing=true;
  try {
    const status=$("status-filter").value;
    const [stats,jobs,workers] = await Promise.all([api("/api/stats"),api("/api/jobs?limit="+limit+"&offset="+offset+(status?"&status="+status:"")),api("/api/workers")]);
    $("total").textContent=stats.total.toLocaleString();
    $("active").textContent=((stats.counts.queued||0)+(stats.counts.running||0)+(stats.counts.retry_wait||0)).toLocaleString();
    $("succeeded").textContent=(stats.counts.succeeded||0).toLocaleString();
    $("retried").textContent=stats.jobs_retried.toLocaleString();
    $("latency").textContent=stats.p95_attempt_seconds===null?"—":stats.p95_attempt_seconds.toFixed(2)+"s";
    renderJobs(jobs); renderWorkers(workers.items);
    if(selected) renderDetail(await api("/api/jobs/"+selected));
    showError(""); $("connection-label").textContent="Connected · "+stamp(Date.now()/1000);
  } catch(error) { showError(error.message); $("connection-label").textContent="Refresh failed · retrying"; }
  finally { refreshing=false; }
}
$("connect-form").addEventListener("submit",async event=> {
  event.preventDefault(); apiKey=$("api-key").value;
  try { await api("/api/stats"); const info=await api("/api/info"); $("runtime-label").textContent=(info.mode==="local_preview"?"LOCAL PREVIEW · ":"")+info.storage; connection(true); await refresh(); } catch(error) { connection(false); showError(error.message); }
});
$("new-job").addEventListener("click",()=>{ $("submit-error").textContent=""; $("submit-dialog").showModal(); });
$("close-dialog").addEventListener("click",()=>$("submit-dialog").close());
$("task").addEventListener("change",()=>{$("payload").value=JSON.stringify(payloads[$("task").value],null,2);});
$("submit-form").addEventListener("submit",async event=>{
  event.preventDefault(); $("submit-button").disabled=true; $("submit-error").textContent="";
  try {
    if(!connected) throw new Error("Connect your workspace first.");
    const data=await api("/api/jobs",{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({task:$("task").value,payload:JSON.parse($("payload").value),priority:Number($("priority").value),max_attempts:Number($("attempts").value),timeout_seconds:Number($("timeout").value)})});
    selected=data.id; offset=0; $("status-filter").value=""; $("submit-dialog").close(); await refresh();
  } catch(error) { $("submit-error").textContent=error.message; } finally { $("submit-button").disabled=false; }
});
$("status-filter").addEventListener("change",()=>{offset=0;refresh();});
$("refresh").addEventListener("click",refresh);
$("previous").addEventListener("click",()=>{offset=Math.max(0,offset-limit);refresh();});
$("next").addEventListener("click",()=>{offset+=limit;refresh();});
setInterval(()=>{if(!document.hidden)refresh();},2000);
