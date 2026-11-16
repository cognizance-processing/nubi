// Markup for the live instrument (static, authored here). Driven by instrument.js.
export default `
    <div class="inst" id="inst">
      <div class="ibar"><span class="live"></span>LIVE IN THIS TAB<span class="r" id="rowsLbl">PREPARING ROWS…</span></div>
      <div class="ctrls">
        <div class="ctrl"><span>Measure</span><div class="seg" data-k="measure"><button data-v="rev" aria-pressed="true">Revenue</button><button data-v="cnt" aria-pressed="false">Orders</button><button data-v="wait" aria-pressed="false">Wait</button></div></div>
        <div class="ctrl"><span>By</span><div class="seg" data-k="group"><button data-v="hd" aria-pressed="true">Hour × day</button><button data-v="hr" aria-pressed="false">Hour × region</button><button data-v="sd" aria-pressed="false">Store × day</button></div></div>
        <div class="ctrl"><span>Channel</span><div class="seg" data-k="chan"><button data-v="-1" aria-pressed="true">All</button><button data-v="0" aria-pressed="false">Store</button><button data-v="1" aria-pressed="false">Pickup</button><button data-v="2" aria-pressed="false">Delivery</button></div></div>
      </div>
      <div class="istage" id="heroStage"><div class="lbls"></div><div class="hintline">Drag to turn · click a bar to filter</div></div>
      <div class="kpis">
        <div class="kpi"><span>Revenue</span><b id="kRev">—</b></div>
        <div class="kpi"><span>Orders</span><b id="kCnt">—</b></div>
        <div class="kpi"><span>Avg ticket</span><b id="kTkt">—</b></div>
        <div class="kpi"><span>Pickup wait</span><b id="kWait">—</b></div>
      </div>
      <div class="read"><span>Scanned <b id="rScan">—</b> rows</span><span>in <b id="rMs">—</b></span><span>Server requests <b>0</b></span><span class="r">Server cost <b>$0.00</b></span></div>
    </div>
    <div class="note">1,000,000 example retail orders, generated in your browser when this page opened.</div>
`
