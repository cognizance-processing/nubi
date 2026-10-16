// Markup for the Edge cache keys figure (static, authored here). Driven by edgeCacheKeys.js.
export default `
<p class="fcap" id="h-cache">A thousand viewers, three queries.</p>
<div class="cache">
      <div class="panel">
        <div class="field"><label class="label" for="viewers">Viewers on the dashboard</label><output id="viewers-o" class="num">1,000</output><input type="range" id="viewers" min="0" max="5" step="0.01" value="3"></div>
        <div class="field"><span class="label" id="ten-l">Distinct security scopes</span>
          <div class="seg" role="group" aria-labelledby="ten-l" id="seg-ten"><button type="button" data-v="1" aria-pressed="false">1</button><button type="button" data-v="3" aria-pressed="true">3</button><button type="button" data-v="12" aria-pressed="false">12</button></div></div>
        <label class="switch"><input type="checkbox" id="shared" checked> Shared edge cache</label>
        <div class="bignum"><span class="label">Warehouse queries</span><span class="v good" id="wq">3</span><small style="color:var(--ink-2)" id="wq-s">one per cache key</small></div>
      </div>
      <div class="figbox">
        <canvas id="cachecv" aria-label="Viewers grouped by security scope flowing into cache keys and then to the warehouse"></canvas>
        <div class="formula"><span>key = <code>sha256(canonical_json({sql, params, rls_claims}))</code></span><span>Fig. 2 · each dot is <span id="perdot">1 viewer</span></span></div>
      </div>
    </div>
  
<div class="toast" id="toast" hidden></div>
`
