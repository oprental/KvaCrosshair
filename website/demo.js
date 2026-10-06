(() => {
 const canvas=document.querySelector('#demo-canvas'),ctx=canvas.getContext('2d'),slider=document.querySelector('#demo-size');
 let style='cross',color='#b69aff';
 function render(){
  const size=Number(slider.value)*2,x=300,y=160,gap=10;
  document.querySelector('#demo-size-value').value=slider.value;
  ctx.clearRect(0,0,600,320);ctx.strokeStyle=color;ctx.fillStyle=color;ctx.lineWidth=4;ctx.shadowBlur=14;ctx.shadowColor=color;ctx.beginPath();
  if(style==='ring'){ctx.arc(x,y,size,0,Math.PI*2);ctx.stroke()}
  else if(style==='dot'){ctx.arc(x,y,Math.max(3,size/5),0,Math.PI*2);ctx.fill()}
  else{for(const [dx,dy] of [[1,0],[-1,0],[0,1],[0,-1]]){ctx.moveTo(x+dx*gap,y+dy*gap);ctx.lineTo(x+dx*(gap+size),y+dy*(gap+size))}ctx.stroke()}
 }
 for(const button of document.querySelectorAll('[data-style]'))button.addEventListener('click',()=>{style=button.dataset.style;document.querySelectorAll('[data-style]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));render()});
 for(const button of document.querySelectorAll('[data-color]'))button.addEventListener('click',()=>{color=button.dataset.color;document.querySelectorAll('[data-color]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));render()});
 slider.addEventListener('input',render);render();
})();
