// Markup for the View anatomy figure (static, authored here). Driven by viewAnatomy.js.
export default `
    <div class="story" id="story">
      <div class="rail" id="rail" role="tablist" aria-label="Story steps">
        <button type="button" role="tab" data-c="1"><b>01</b><span>Query</span><i></i></button>
        <button type="button" role="tab" data-c="2"><b>02</b><span>Cache</span><i></i></button>
        <button type="button" role="tab" data-c="3"><b>03</b><span>Compute</span><i></i></button>
        <button type="button" role="tab" data-c="4"><b>04</b><span>Secure</span><i></i></button>
        <button type="button" role="tab" data-c="5"><b>05</b><span>Author</span><i></i></button>
      </div>
      <div class="story-body">
        <div class="cap-panel" id="cap" aria-live="polite">
          <div class="chn"><span id="cap-n">01 / 05</span><span id="cap-auto">Playing</span></div>
          <h3 id="cap-t">A viewer opens a dashboard.</h3>
          <p id="cap-b"></p>
          <div class="cap-ctl" id="cap-ctl"></div>
          <div class="cap-nav"><button class="btn sm ghost" type="button" id="prev">← Back</button><button class="btn sm" type="button" id="next">Next step →</button><button class="btn sm ghost" type="button" id="play">Pause</button></div>
          <div class="key" aria-label="Legend">
            <div><s class="k1"></s>Arrow record batch (data)</div>
            <div><s class="k2"></s>Layout from an agent</div>
            <div><s class="k3"></s>Route</div>
            <div><s class="k4"></s>Rows that exist but this viewer can’t see</div>
          </div>
        </div>
        <div class="stage" id="stage">
          <canvas id="gl" aria-label="Wireframe diorama of a warehouse, an edge cache and six viewer browser tabs, with data batches moving between them"></canvas>
          <svg class="leaders" id="leaders" aria-hidden="true"></svg>
          <div class="anno" id="anno"></div>
          <span class="drag-hint">drag to turn · synthetic sample data</span>
        </div>
      </div>
      <div class="readouts">
        <div class="ro"><span class="label">Viewers</span><span class="v" id="r-view">0</span><small id="r-view-s">open tabs</small></div>
        <div class="ro"><span class="label">Warehouse queries</span><span class="v" id="r-wq">0</span><small id="r-wq-s">one per cache key</small></div>
        <div class="ro"><span class="label">Served from edge</span><span class="v" id="r-hit">0</span><small>no warehouse work</small></div>
        <div class="ro"><span class="label">Last aggregate</span><span class="v" id="r-agg">—</span><small id="r-agg-s">measured in your browser</small></div>
        <div class="ro"><span class="label">Server compute</span><span class="v zero">0 ms</span><small>per view, after the query</small></div>
      </div>
    </div>
<div class="toast" id="toast" hidden></div>
`
