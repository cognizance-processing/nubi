const SHARED_HEAD = `<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f172a;color:#f1f5f9;padding:1.25rem;min-height:100vh}
.card{background:rgba(30,41,59,.8);border:1px solid rgba(255,255,255,.08);border-radius:.875rem;padding:1.5rem;backdrop-filter:blur(12px)}
.card-title{font-size:.8rem;font-weight:600;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em;margin-bottom:1rem}
canvas{max-height:300px}
</style>`

const templates = [
    {
        id: 'bar-chart',
        name: 'Bar Chart',
        description: 'Vertical bar chart with labeled axes',
        icon: '📊',
        color: 'bg-blue-500/10 border-blue-500/20 text-blue-400',
        code: `<!DOCTYPE html><html lang="en"><head>${SHARED_HEAD}<title>Bar Chart</title></head><body>
<div class="card">
  <div class="card-title">Monthly Revenue</div>
  <canvas id="chart"></canvas>
</div>
<script>
new Chart(document.getElementById('chart'),{
  type:'bar',
  data:{
    labels:['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug'],
    datasets:[{
      label:'Revenue ($K)',
      data:[42,53,61,47,72,65,80,74],
      backgroundColor:'rgba(99,102,241,.75)',
      borderRadius:6,
      borderSkipped:false
    }]
  },
  options:{
    responsive:true,
    plugins:{legend:{display:false}},
    scales:{
      y:{grid:{color:'rgba(255,255,255,.04)'},ticks:{color:'#64748b'}},
      x:{grid:{display:false},ticks:{color:'#64748b'}}
    }
  }
});
</script></body></html>`
    },
    {
        id: 'line-chart',
        name: 'Line Chart',
        description: 'Smooth time-series line with gradient fill',
        icon: '📈',
        color: 'bg-indigo-500/10 border-indigo-500/20 text-indigo-400',
        code: `<!DOCTYPE html><html lang="en"><head>${SHARED_HEAD}<title>Line Chart</title></head><body>
<div class="card">
  <div class="card-title">User Growth</div>
  <canvas id="chart"></canvas>
</div>
<script>
const ctx=document.getElementById('chart').getContext('2d');
const grad=ctx.createLinearGradient(0,0,0,300);
grad.addColorStop(0,'rgba(99,102,241,.35)');
grad.addColorStop(1,'rgba(99,102,241,0)');
new Chart(ctx,{
  type:'line',
  data:{
    labels:['W1','W2','W3','W4','W5','W6','W7','W8','W9','W10','W11','W12'],
    datasets:[{
      label:'Users',
      data:[120,180,210,290,350,410,480,530,620,740,810,920],
      borderColor:'rgb(99,102,241)',
      backgroundColor:grad,
      fill:true,
      tension:.4,
      pointRadius:0,
      pointHoverRadius:5,
      borderWidth:2.5
    }]
  },
  options:{
    responsive:true,
    plugins:{legend:{display:false}},
    scales:{
      y:{grid:{color:'rgba(255,255,255,.04)'},ticks:{color:'#64748b'}},
      x:{grid:{display:false},ticks:{color:'#64748b'}}
    }
  }
});
</script></body></html>`
    },
    {
        id: 'pie-chart',
        name: 'Donut Chart',
        description: 'Donut chart with center label',
        icon: '🍩',
        color: 'bg-purple-500/10 border-purple-500/20 text-purple-400',
        code: `<!DOCTYPE html><html lang="en"><head>${SHARED_HEAD}<title>Donut Chart</title>
<style>.donut-wrap{position:relative;max-width:280px;margin:0 auto}.donut-center{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center}.donut-center .val{font-size:2rem;font-weight:800;color:#fff}.donut-center .lbl{font-size:.75rem;color:#94a3b8}</style>
</head><body>
<div class="card">
  <div class="card-title">Traffic Sources</div>
  <div class="donut-wrap">
    <canvas id="chart"></canvas>
    <div class="donut-center"><div class="val">24.8K</div><div class="lbl">Total Visits</div></div>
  </div>
</div>
<script>
new Chart(document.getElementById('chart'),{
  type:'doughnut',
  data:{
    labels:['Organic','Direct','Social','Referral'],
    datasets:[{
      data:[42,28,18,12],
      backgroundColor:['#6366f1','#8b5cf6','#ec4899','#f59e0b'],
      borderWidth:0,
      spacing:3,
      borderRadius:4
    }]
  },
  options:{
    cutout:'72%',
    responsive:true,
    plugins:{legend:{position:'bottom',labels:{color:'#94a3b8',padding:16,usePointStyle:true,pointStyleWidth:8}}}
  }
});
</script></body></html>`
    },
    {
        id: 'stats-card',
        name: 'Stats Card',
        description: 'KPI cards with trend indicators',
        icon: '🔢',
        color: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400',
        code: `<!DOCTYPE html><html lang="en"><head>${SHARED_HEAD}<title>Stats Card</title>
<style>
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.75rem}
.stat{text-align:left}
.stat .label{font-size:.75rem;color:#94a3b8;margin-bottom:.5rem}
.stat .value{font-size:1.75rem;font-weight:800;color:#fff;line-height:1.2}
.stat .trend{display:inline-flex;align-items:center;gap:.25rem;font-size:.75rem;font-weight:600;margin-top:.5rem;padding:.15rem .5rem;border-radius:9999px}
.stat .trend.up{color:#34d399;background:rgba(52,211,153,.1)}
.stat .trend.down{color:#f87171;background:rgba(248,113,113,.1)}
.stat .sub{font-size:.7rem;color:#64748b;margin-top:.25rem}
</style></head><body>
<div class="grid">
  <div class="card stat">
    <div class="label">Total Revenue</div>
    <div class="value">$84.2K</div>
    <span class="trend up">↑ 12.5%</span>
    <div class="sub">vs last month</div>
  </div>
  <div class="card stat">
    <div class="label">Active Users</div>
    <div class="value">2,847</div>
    <span class="trend up">↑ 8.3%</span>
    <div class="sub">vs last month</div>
  </div>
  <div class="card stat">
    <div class="label">Churn Rate</div>
    <div class="value">3.2%</div>
    <span class="trend down">↑ 0.4%</span>
    <div class="sub">vs last month</div>
  </div>
  <div class="card stat">
    <div class="label">Avg Order Value</div>
    <div class="value">$127</div>
    <span class="trend up">↑ 5.1%</span>
    <div class="sub">vs last month</div>
  </div>
</div>
</body></html>`
    },
    {
        id: 'plain-text',
        name: 'Plain Text',
        description: 'Rich formatted text block with headings',
        icon: '📝',
        color: 'bg-slate-500/10 border-slate-500/20 text-slate-400',
        code: `<!DOCTYPE html><html lang="en"><head>${SHARED_HEAD}<title>Plain Text</title>
<style>
.prose{max-width:640px}
.prose h1{font-size:1.5rem;font-weight:800;color:#fff;margin-bottom:.75rem;line-height:1.3}
.prose h2{font-size:1.1rem;font-weight:700;color:#e2e8f0;margin:1.25rem 0 .5rem;line-height:1.4}
.prose p{color:#94a3b8;font-size:.9rem;line-height:1.75;margin-bottom:.75rem}
.prose ul{color:#94a3b8;font-size:.9rem;line-height:1.75;padding-left:1.25rem;margin-bottom:.75rem}
.prose strong{color:#e2e8f0;font-weight:600}
.prose .badge{display:inline-block;padding:.15rem .6rem;border-radius:9999px;font-size:.7rem;font-weight:600;background:rgba(99,102,241,.15);color:#818cf8;margin-bottom:1rem}
</style></head><body>
<div class="card prose">
  <span class="badge">Announcement</span>
  <h1>Q4 Performance Summary</h1>
  <p>We closed the quarter with <strong>record-breaking growth</strong> across all key metrics. Here's a snapshot of what the team accomplished.</p>
  <h2>Key Highlights</h2>
  <ul>
    <li><strong>Revenue:</strong> $2.4M (+32% YoY)</li>
    <li><strong>New customers:</strong> 847 accounts acquired</li>
    <li><strong>Retention:</strong> 96.8% monthly retention rate</li>
    <li><strong>NPS score:</strong> 72 (up from 64)</li>
  </ul>
  <p>The team is well positioned heading into the new year. Focus areas for Q1 include enterprise expansion and the new self-serve onboarding flow.</p>
</div>
</body></html>`
    },
    {
        id: 'drill-down-section',
        name: 'Drill Down Section',
        description: 'Expandable accordion with nested content',
        icon: '🔽',
        color: 'bg-amber-500/10 border-amber-500/20 text-amber-400',
        code: `<!DOCTYPE html><html lang="en"><head>${SHARED_HEAD}<title>Drill Down Section</title>
<style>
.accordion{display:flex;flex-direction:column;gap:.5rem}
.acc-item{border:1px solid rgba(255,255,255,.06);border-radius:.75rem;overflow:hidden;background:rgba(30,41,59,.5)}
.acc-header{display:flex;align-items:center;justify-content:space-between;padding:1rem 1.25rem;cursor:pointer;user-select:none;transition:background .15s}
.acc-header:hover{background:rgba(255,255,255,.03)}
.acc-header .label{font-size:.875rem;font-weight:600;color:#e2e8f0;display:flex;align-items:center;gap:.6rem}
.acc-header .count{font-size:.7rem;font-weight:600;padding:.15rem .5rem;border-radius:9999px;background:rgba(99,102,241,.15);color:#818cf8}
.acc-header .arrow{color:#64748b;transition:transform .2s;font-size:.75rem}
.acc-header.open .arrow{transform:rotate(180deg)}
.acc-body{max-height:0;overflow:hidden;transition:max-height .25s ease}
.acc-body.open{max-height:500px}
.acc-content{padding:0 1.25rem 1rem}
.acc-content p{font-size:.8rem;color:#94a3b8;line-height:1.65;margin-bottom:.5rem}
.acc-content .row{display:flex;justify-content:space-between;padding:.5rem 0;border-bottom:1px solid rgba(255,255,255,.04);font-size:.8rem}
.acc-content .row:last-child{border:none}
.acc-content .row .k{color:#94a3b8}.acc-content .row .v{color:#e2e8f0;font-weight:600}
</style></head><body>
<div class="card">
  <div class="card-title">Department Breakdown</div>
  <div class="accordion" id="acc"></div>
</div>
<script>
const sections=[
  {title:'Engineering',count:'48 people',rows:[['Headcount','48'],['Avg Tenure','2.4 yrs'],['Open Roles','7'],['Budget Util.','89%']]},
  {title:'Marketing',count:'22 people',rows:[['Headcount','22'],['Avg Tenure','1.8 yrs'],['Open Roles','3'],['Budget Util.','94%']]},
  {title:'Sales',count:'35 people',rows:[['Headcount','35'],['Avg Tenure','1.6 yrs'],['Open Roles','5'],['Budget Util.','78%']]},
  {title:'Support',count:'18 people',rows:[['Headcount','18'],['Avg Tenure','2.1 yrs'],['Open Roles','2'],['Budget Util.','82%']]}
];
const acc=document.getElementById('acc');
sections.forEach((s,i)=>{
  const item=document.createElement('div');item.className='acc-item';
  item.innerHTML=\`<div class="acc-header" onclick="toggle(this)"><span class="label">\${s.title}<span class="count">\${s.count}</span></span><span class="arrow">▼</span></div><div class="acc-body"><div class="acc-content">\${s.rows.map(r=>\`<div class="row"><span class="k">\${r[0]}</span><span class="v">\${r[1]}</span></div>\`).join('')}</div></div>\`;
  acc.appendChild(item);
});
function toggle(el){el.classList.toggle('open');el.nextElementSibling.classList.toggle('open')}
</script></body></html>`
    },
    {
        id: 'drill-down-chart',
        name: 'Drill Down Chart',
        description: 'Click a bar to reveal detail breakdown',
        icon: '🔍',
        color: 'bg-cyan-500/10 border-cyan-500/20 text-cyan-400',
        code: `<!DOCTYPE html><html lang="en"><head>${SHARED_HEAD}<title>Drill Down Chart</title>
<style>
.back{display:none;align-items:center;gap:.35rem;padding:.35rem .75rem;border-radius:.5rem;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.04);color:#94a3b8;font-size:.75rem;cursor:pointer;margin-bottom:.75rem;font-weight:500;transition:all .15s}
.back:hover{background:rgba(255,255,255,.08);color:#fff}
.back.show{display:inline-flex}
.hint{text-align:center;font-size:.7rem;color:#475569;margin-top:.75rem}
</style></head><body>
<div class="card">
  <div class="card-title" id="title">Revenue by Region</div>
  <button class="back" id="backBtn" onclick="showTop()">← Back to regions</button>
  <canvas id="chart"></canvas>
  <div class="hint" id="hint">Click a bar to drill down</div>
</div>
<script>
const topData={labels:['North America','Europe','Asia Pacific','Latin America'],data:[420,310,280,140],colors:['#6366f1','#8b5cf6','#06b6d4','#f59e0b']};
const details={
  'North America':{labels:['US East','US West','Canada','Mexico'],data:[180,140,60,40]},
  'Europe':{labels:['UK','Germany','France','Nordics'],data:[95,85,72,58]},
  'Asia Pacific':{labels:['Japan','Australia','India','SEA'],data:[90,75,68,47]},
  'Latin America':{labels:['Brazil','Argentina','Colombia','Chile'],data:[55,38,28,19]}
};
let chart;
function render(labels,data,colors,onClick){
  if(chart)chart.destroy();
  chart=new Chart(document.getElementById('chart'),{
    type:'bar',
    data:{labels,datasets:[{data,backgroundColor:colors||'rgba(99,102,241,.75)',borderRadius:6,borderSkipped:false}]},
    options:{responsive:true,plugins:{legend:{display:false}},
      scales:{y:{grid:{color:'rgba(255,255,255,.04)'},ticks:{color:'#64748b'}},x:{grid:{display:false},ticks:{color:'#64748b'}}},
      onClick:onClick||null}
  });
}
function showTop(){
  document.getElementById('title').textContent='Revenue by Region';
  document.getElementById('backBtn').classList.remove('show');
  document.getElementById('hint').style.display='';
  render(topData.labels,topData.data,topData.colors,(_,els)=>{
    if(els.length){const region=topData.labels[els[0].index];drillInto(region)}
  });
}
function drillInto(region){
  const d=details[region];if(!d)return;
  document.getElementById('title').textContent=region+' Breakdown';
  document.getElementById('backBtn').classList.add('show');
  document.getElementById('hint').style.display='none';
  render(d.labels,d.data);
}
showTop();
</script></body></html>`
    },
    {
        id: 'data-table',
        name: 'Data Table',
        description: 'Sortable table with status badges',
        icon: '📋',
        color: 'bg-teal-500/10 border-teal-500/20 text-teal-400',
        code: `<!DOCTYPE html><html lang="en"><head>${SHARED_HEAD}<title>Data Table</title>
<style>
table{width:100%;border-collapse:collapse;font-size:.8rem}
th{text-align:left;padding:.65rem .75rem;color:#64748b;font-weight:600;border-bottom:1px solid rgba(255,255,255,.08);cursor:pointer;user-select:none;white-space:nowrap;font-size:.7rem;text-transform:uppercase;letter-spacing:.04em}
th:hover{color:#e2e8f0}
td{padding:.65rem .75rem;border-bottom:1px solid rgba(255,255,255,.04);color:#cbd5e1}
tr:hover td{background:rgba(255,255,255,.02)}
.badge{padding:.15rem .5rem;border-radius:9999px;font-size:.65rem;font-weight:600}
.badge.active{background:rgba(52,211,153,.12);color:#34d399}
.badge.pending{background:rgba(251,191,36,.12);color:#fbbf24}
.badge.churned{background:rgba(248,113,113,.12);color:#f87171}
.sort-arrow{opacity:.4;margin-left:.25rem}
</style></head><body>
<div class="card" style="padding:1rem">
  <div class="card-title" style="padding:0 .75rem">Top Customers</div>
  <table id="tbl">
    <thead><tr>
      <th onclick="sort(0)">Customer <span class="sort-arrow">↕</span></th>
      <th onclick="sort(1)">Revenue <span class="sort-arrow">↕</span></th>
      <th onclick="sort(2)">Orders <span class="sort-arrow">↕</span></th>
      <th onclick="sort(3)">Status</th>
    </tr></thead>
    <tbody id="tbody"></tbody>
  </table>
</div>
<script>
const rows=[
  ['Acme Corp','$48,200',342,'active'],
  ['Globex Inc','$36,800',281,'active'],
  ['Stark Industries','$29,400',198,'active'],
  ['Wayne Enterprises','$22,100',156,'pending'],
  ['Umbrella Corp','$18,600',132,'churned'],
  ['Soylent Corp','$15,900',104,'active'],
  ['Initech','$12,300',87,'pending'],
  ['Hooli','$9,800',63,'active'],
];
let data=[...rows],sortCol=-1,sortAsc=true;
function render(){
  document.getElementById('tbody').innerHTML=data.map(r=>\`<tr><td style="font-weight:600;color:#fff">\${r[0]}</td><td>\${r[1]}</td><td>\${r[2]}</td><td><span class="badge \${r[3]}">\${r[3]}</span></td></tr>\`).join('');
}
function sort(col){
  if(sortCol===col)sortAsc=!sortAsc;else{sortCol=col;sortAsc=true}
  data.sort((a,b)=>{
    let va=a[col],vb=b[col];
    if(col===1){va=parseFloat(va.replace(/[^0-9.]/g,''));vb=parseFloat(vb.replace(/[^0-9.]/g,''))}
    if(col===2){va=+va;vb=+vb}
    if(va<vb)return sortAsc?-1:1;if(va>vb)return sortAsc?1:-1;return 0;
  });
  render();
}
render();
</script></body></html>`
    },
    {
        id: 'area-chart',
        name: 'Stacked Area Chart',
        description: 'Multi-series stacked area with legend',
        icon: '🏔️',
        color: 'bg-violet-500/10 border-violet-500/20 text-violet-400',
        code: `<!DOCTYPE html><html lang="en"><head>${SHARED_HEAD}<title>Area Chart</title></head><body>
<div class="card">
  <div class="card-title">Traffic by Channel</div>
  <canvas id="chart"></canvas>
</div>
<script>
const ctx=document.getElementById('chart').getContext('2d');
function makeGrad(color){const g=ctx.createLinearGradient(0,0,0,300);g.addColorStop(0,color.replace('1)','0.4)'));g.addColorStop(1,color.replace('1)','0)'));return g}
const labels=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
new Chart(ctx,{
  type:'line',
  data:{
    labels,
    datasets:[
      {label:'Organic',data:[320,380,420,390,460,280,310],borderColor:'rgba(99,102,241,1)',backgroundColor:makeGrad('rgba(99,102,241,1)'),fill:true,tension:.4,pointRadius:0,borderWidth:2},
      {label:'Paid',data:[180,220,200,260,240,160,190],borderColor:'rgba(139,92,246,1)',backgroundColor:makeGrad('rgba(139,92,246,1)'),fill:true,tension:.4,pointRadius:0,borderWidth:2},
      {label:'Social',data:[90,110,130,100,140,200,170],borderColor:'rgba(236,72,153,1)',backgroundColor:makeGrad('rgba(236,72,153,1)'),fill:true,tension:.4,pointRadius:0,borderWidth:2}
    ]
  },
  options:{
    responsive:true,
    plugins:{legend:{position:'bottom',labels:{color:'#94a3b8',padding:16,usePointStyle:true,pointStyleWidth:8}}},
    scales:{
      y:{stacked:true,grid:{color:'rgba(255,255,255,.04)'},ticks:{color:'#64748b'}},
      x:{grid:{display:false},ticks:{color:'#64748b'}}
    }
  }
});
</script></body></html>`
    },
    {
        id: 'gauge',
        name: 'Gauge / Progress',
        description: 'Animated progress rings with labels',
        icon: '⏱️',
        color: 'bg-rose-500/10 border-rose-500/20 text-rose-400',
        code: `<!DOCTYPE html><html lang="en"><head>${SHARED_HEAD}<title>Gauge</title>
<style>
.gauges{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1rem;text-align:center}
.gauge{display:flex;flex-direction:column;align-items:center;gap:.75rem}
.ring{position:relative;width:100px;height:100px}
.ring svg{transform:rotate(-90deg)}
.ring circle{fill:none;stroke-width:8;stroke-linecap:round}
.ring .bg{stroke:rgba(255,255,255,.06)}
.ring .fg{transition:stroke-dashoffset .8s ease}
.ring .val{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:1.25rem;font-weight:800;color:#fff}
.gauge .label{font-size:.75rem;color:#94a3b8;font-weight:500}
</style></head><body>
<div class="card">
  <div class="card-title">Performance Metrics</div>
  <div class="gauges" id="gauges"></div>
</div>
<script>
const metrics=[
  {label:'Uptime',value:99.9,color:'#34d399'},
  {label:'CPU Load',value:67,color:'#6366f1'},
  {label:'Memory',value:82,color:'#f59e0b'},
  {label:'Disk I/O',value:45,color:'#06b6d4'}
];
const C=2*Math.PI*42;
document.getElementById('gauges').innerHTML=metrics.map(m=>{
  const offset=C-(m.value/100)*C;
  return \`<div class="gauge"><div class="ring"><svg viewBox="0 0 100 100"><circle class="bg" cx="50" cy="50" r="42"/><circle class="fg" cx="50" cy="50" r="42" stroke="\${m.color}" stroke-dasharray="\${C}" stroke-dashoffset="\${C}" style="transition-delay:.2s"/></svg><div class="val">\${m.value}%</div></div><div class="label">\${m.label}</div></div>\`;
}).join('');
requestAnimationFrame(()=>{
  document.querySelectorAll('.fg').forEach((el,i)=>{
    const offset=C-(metrics[i].value/100)*C;
    el.style.strokeDashoffset=offset;
  });
});
</script></body></html>`
    },
]

export default templates
