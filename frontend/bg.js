// �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?
// BACKGROUND ENGINE — aurora blobs + particle mesh + scan
// �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?
(()=>{
  const cvs = document.getElementById('netbg');
  const ctx  = cvs.getContext('2d');
  let W, H, nodes = [], scan = 0, t = 0;

  // True 8fps throttle via setTimeout+RAF — eliminates 52 wasted RAF callbacks/s
  // (old pattern called requestAnimationFrame unconditionally before the throttle check)
  const _BG_MS = 1000 / 8;
  let _bgRafId = null;

  // Aurora orbs
  const ORBS = [
    {xr:.18, yr:.25, r:.38, h:220, s:.8, spd:.00008},
    {xr:.75, yr:.65, r:.32, h:255, s:.7, spd:.00012},
    {xr:.55, yr:.12, r:.25, h:190, s:.6, spd:.00015},
  ];

  function resize(){
    W = cvs.width  = window.innerWidth;
    H = cvs.height = window.innerHeight;
    nodes = [];
    const STEP = 88; // was 66 — further reduces O(n²) pair checks by ~67% total vs original
    const cols = Math.ceil(W/STEP)+2, rows = Math.ceil(H/STEP)+2;
    for(let r=0;r<rows;r++) for(let c=0;c<cols;c++){
      nodes.push({
        bx: c*STEP - STEP/2, by: r*STEP - STEP/2,
        ox: (Math.random()-.5)*14, oy: (Math.random()-.5)*14,
        vx: (Math.random()-.5)*.12, vy: (Math.random()-.5)*.12,
        bright: Math.random()*.45+.08,
        r: Math.random()*.8+.8
      });
    }
  }

  function hsl(h,s,l,a){ return `hsla(${h},${s}%,${l}%,${a})`; }

  function drawAurora(){
    ORBS.forEach(o=>{
      const phase = t * o.spd;
      const cx = W * (o.xr + Math.sin(phase*1.3)*.06);
      const cy = H * (o.yr + Math.cos(phase)*.06);
      const rx = W * o.r * (1 + Math.sin(phase*.7)*.08);
      const ry = H * o.r * .6 * (1 + Math.cos(phase*.9)*.06);
      const hue = o.h + Math.sin(phase*.5)*20;
      const g = ctx.createRadialGradient(cx,cy,0, cx,cy, Math.max(rx,ry));
      g.addColorStop(0,   hsl(hue, 80, 60, o.s*.18));
      g.addColorStop(0.4, hsl(hue, 70, 50, o.s*.08));
      g.addColorStop(1,   hsl(hue, 60, 40, 0));
      ctx.save();
      ctx.scale(1, ry/rx);
      ctx.beginPath();
      ctx.arc(cx, cy*(rx/ry), rx, 0, Math.PI*2);
      ctx.fillStyle = g;
      ctx.fill();
      ctx.restore();
    });
  }

  function drawMesh(){
    const DIST = 110, DIST2 = DIST*DIST; // compare squared — skips sqrt for ~90% of pairs
    for(let i=0;i<nodes.length;i++){
      const a = nodes[i];
      const ax = a.bx+a.ox, ay = a.by+a.oy;
      for(let j=i+1;j<nodes.length;j++){
        const b = nodes[j];
        const bx = b.bx+b.ox, by = b.by+b.oy;
        const dx=ax-bx, dy=ay-by;
        if(dx*dx+dy*dy > DIST2) continue; // fast reject — no sqrt needed
        const d=Math.sqrt(dx*dx+dy*dy);
        if(d > DIST) continue;
        const fade = (1-d/DIST);
        // scan glow on edges
        const sd = Math.min(Math.abs(ay-scan), Math.abs(by-scan));
        const boost = sd < 50 ? .08*(1-sd/50) : 0;
        ctx.strokeStyle = `rgba(47,140,255,${fade*.09+boost})`;
        ctx.lineWidth = .7;
        ctx.beginPath(); ctx.moveTo(ax,ay); ctx.lineTo(bx,by); ctx.stroke();
      }
    }
    nodes.forEach(n=>{
      const x=n.bx+n.ox, y=n.by+n.oy;
      const sd = Math.abs(y-scan);
      const boost = sd<35 ? .7*(1-sd/35) : 0;
      const a = n.bright + boost;
      // glow halo
      if(boost > .1){
        const g2 = ctx.createRadialGradient(x,y,0,x,y,8);
        g2.addColorStop(0,`rgba(47,140,255,${boost*.3})`);
        g2.addColorStop(1,'rgba(47,140,255,0)');
        ctx.beginPath(); ctx.arc(x,y,8,0,Math.PI*2);
        ctx.fillStyle=g2; ctx.fill();
      }
      ctx.beginPath(); ctx.arc(x,y,n.r,0,Math.PI*2);
      ctx.fillStyle=`rgba(80,155,255,${a})`; ctx.fill();
    });
  }

  function drawScan(){
    scan = (scan + .35) % H;
    // primary beam
    const sg = ctx.createLinearGradient(0,scan-60,0,scan+60);
    sg.addColorStop(0,   'rgba(47,129,247,0)');
    sg.addColorStop(.45, 'rgba(47,129,247,.055)');
    sg.addColorStop(.5,  'rgba(100,180,255,.13)');
    sg.addColorStop(.55, 'rgba(47,129,247,.055)');
    sg.addColorStop(1,   'rgba(47,129,247,0)');
    ctx.fillStyle=sg;
    ctx.fillRect(0, scan-60, W, 120);
    // thin bright line
    ctx.strokeStyle='rgba(120,190,255,.18)';
    ctx.lineWidth=1;
    ctx.beginPath(); ctx.moveTo(0,scan); ctx.lineTo(W,scan); ctx.stroke();
  }

  function frame(){
    _bgRafId = null;
    if(document.hidden) return; // tab hidden — visibilitychange will restart
    t++;
    ctx.clearRect(0,0,W,H);
    drawAurora();
    drawMesh();
    drawScan();
    nodes.forEach(n=>{
      n.ox+=n.vx; n.oy+=n.vy;
      if(Math.abs(n.ox)>16) n.vx*=-1;
      if(Math.abs(n.oy)>16) n.vy*=-1;
    });
    // When map tab is active, the iframe covers most of the bg canvas —
    // drop to 4fps to free GPU bandwidth for the iframe's own canvases.
    const delay = window._bgMapActive ? _BG_MS * 2 : _BG_MS;
    _bgRafId = setTimeout(()=>requestAnimationFrame(frame), delay);
  }

  function startBg(){
    if(!_bgRafId && !document.hidden)
      _bgRafId = setTimeout(()=>requestAnimationFrame(frame), _BG_MS);
  }

  document.addEventListener('visibilitychange', ()=>{ if(!document.hidden) startBg(); });
  // Expose resume hook so app.js can restart bg when leaving the map tab
  window._bgResume = startBg;
  window.addEventListener('resize', resize);
  resize(); startBg();
})();

// ── Hero radar canvas ────────────────────────────────────────────
(()=>{
  function initRadar(){
    const cvs=document.getElementById('radarCvs');
    if(!cvs)return;
    const ctx=cvs.getContext('2d');
    const W=220,H=220,CX=110,CY=110,R=96;
    let angle=0;
    // blips: {a, r, life, max}
    const blips=[];
    function addBlip(){
      blips.push({a:Math.random()*Math.PI*2,r:Math.random()*R*.85+8,life:1,max:1});
    }
    for(let i=0;i<5;i++)addBlip();

    // 15fps — halved from 30fps to reduce GPU composite pressure.
    // The sweep animation is smooth enough at 15fps and saves significant GPU bandwidth.
    const RADAR_MS = 1000 / 15;
    let _radarRafId = null;

    function frame(){
      _radarRafId = null;
      if(document.hidden) return; // tab hidden — startRadar() resumes on visibilitychange
      ctx.clearRect(0,0,W,H);

      // outer glow ring
      const og=ctx.createRadialGradient(CX,CY,R-4,CX,CY,R+12);
      og.addColorStop(0,'rgba(47,129,247,.18)');
      og.addColorStop(1,'rgba(47,129,247,0)');
      ctx.beginPath();ctx.arc(CX,CY,R+12,0,Math.PI*2);
      ctx.fillStyle=og;ctx.fill();

      // concentric circles
      [R*.28,R*.54,R*.78,R].forEach((r,i)=>{
        ctx.beginPath();ctx.arc(CX,CY,r,0,Math.PI*2);
        ctx.strokeStyle=`rgba(47,129,247,${.08+i*.04})`;
        ctx.lineWidth=.8;ctx.stroke();
      });

      // crosshairs
      ctx.strokeStyle='rgba(47,129,247,.12)';ctx.lineWidth=.7;
      [-1,0,1].forEach(d=>{
        if(d===0){
          ctx.beginPath();ctx.moveTo(CX-R,CY);ctx.lineTo(CX+R,CY);ctx.stroke();
          ctx.beginPath();ctx.moveTo(CX,CY-R);ctx.lineTo(CX,CY+R);ctx.stroke();
        } else {
          const off=R*.71;
          ctx.beginPath();ctx.moveTo(CX-off,CY-off*d);ctx.lineTo(CX+off,CY+off*d);ctx.stroke();
        }
      });

      // sweep gradient (trailing arc)
      const sweepLen = Math.PI*.7;
      const sweepG=ctx.createConicalGradient?null:null; // fallback: manual arc slices
      for(let i=0;i<40;i++){
        const frac=i/40;
        const a0=angle - sweepLen*frac;
        const a1=angle - sweepLen*(frac+1/40);
        ctx.beginPath();ctx.moveTo(CX,CY);
        ctx.arc(CX,CY,R,a1,a0);ctx.closePath();
        ctx.fillStyle=`rgba(35,209,139,${frac*.12})`;
        ctx.fill();
      }

      // sweep leading edge line
      const lx=CX+Math.cos(angle)*R, ly=CY+Math.sin(angle)*R;
      const lg=ctx.createLinearGradient(CX,CY,lx,ly);
      lg.addColorStop(0,'rgba(35,209,139,0)');
      lg.addColorStop(1,'rgba(35,209,139,.6)');
      ctx.beginPath();ctx.moveTo(CX,CY);ctx.lineTo(lx,ly);
      ctx.strokeStyle=lg;ctx.lineWidth=1.5;ctx.stroke();

      // blips
      blips.forEach((b,idx)=>{
        const bx=CX+Math.cos(b.a)*b.r, by=CY+Math.sin(b.a)*b.r;
        // light up when sweep passes
        const da=((angle-b.a)%(Math.PI*2)+Math.PI*2)%(Math.PI*2);
        if(da<.15) b.life=1;
        b.life=Math.max(0,b.life-.004);
        if(b.life<.01&&Math.random()<.005){blips.splice(idx,1);addBlip();return;}
        // glow
        const g=ctx.createRadialGradient(bx,by,0,bx,by,7);
        g.addColorStop(0,`rgba(35,209,139,${b.life*.7})`);
        g.addColorStop(1,'rgba(35,209,139,0)');
        ctx.beginPath();ctx.arc(bx,by,7,0,Math.PI*2);
        ctx.fillStyle=g;ctx.fill();
        // dot
        ctx.beginPath();ctx.arc(bx,by,2,0,Math.PI*2);
        ctx.fillStyle=`rgba(35,209,139,${b.life})`;ctx.fill();
      });

      // center dot
      const cg=ctx.createRadialGradient(CX,CY,0,CX,CY,8);
      cg.addColorStop(0,'rgba(47,129,247,.9)');
      cg.addColorStop(1,'rgba(47,129,247,0)');
      ctx.beginPath();ctx.arc(CX,CY,8,0,Math.PI*2);ctx.fillStyle=cg;ctx.fill();
      ctx.beginPath();ctx.arc(CX,CY,3,0,Math.PI*2);ctx.fillStyle='#60a5fa';ctx.fill();

      angle=(angle+.072)%(Math.PI*2); // .018*4 — compensate for 15 FPS vs 60 FPS
      _radarRafId = setTimeout(()=>requestAnimationFrame(frame), RADAR_MS);
    }

    function startRadar(){
      if(!_radarRafId && !document.hidden)
        _radarRafId = setTimeout(()=>requestAnimationFrame(frame), RADAR_MS);
    }
    document.addEventListener('visibilitychange', ()=>{ if(!document.hidden) startRadar(); });
    startRadar();
  }
  // init when DOM ready
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',initRadar);
  else initRadar();
})();
